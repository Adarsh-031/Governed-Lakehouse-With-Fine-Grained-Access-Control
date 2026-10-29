"""Reject vacuous coverage and false passes; verify inference scoring semantics."""

import csv
import json
from types import SimpleNamespace

import pytest

from tests.bypass.report import Attempt, Report, linkage_metrics, load_cases
from tests.bypass.test_bypass import HANDLERS


def test_inventory_has_exactly_one_handler_per_case():
    assert {row["case"] for row in load_cases()} == set(HANDLERS)


def test_report_keeps_unexecuted_cases_and_private_evidence(tmp_path):
    report = Report(tmp_path / "run")
    with Attempt(report, "bronze_query", None) as attempt:
        attempt.observe(identity="doctor_alice", query_id="synthetic-id", error="PERMISSION_DENIED")
    payload = json.loads((report.directory / "bypass-results.json").read_text())
    assert payload["summary"] == {"not_run": len(HANDLERS) - 1, "blocked": 1}
    assert all(row["reason"] for row in payload["cases"])
    with (report.directory / "cases.csv").open() as handle:
        assert len(list(csv.DictReader(handle))) == len(HANDLERS)
    assert report.directory.stat().st_mode & 0o777 == 0o700
    for name in ("bypass-results.json", "cases.csv"):
        assert (report.directory / name).stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        Report(report.directory)


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_failed_and_interrupted_attempts_are_inconclusive(tmp_path, error):
    report = Report(tmp_path / "run")
    with pytest.raises(error), Attempt(report, "bronze_query", None):
        raise error("Do not persist potentially credential-bearing exception text")
    assert report.cases["bronze_query"]["status"] == "inconclusive"
    assert "credential-bearing" not in (report.directory / "bypass-results.json").read_text()


@pytest.mark.parametrize(
    "error_name,status", [(None, "leaked"), ("TABLE_NOT_FOUND", "inconclusive")]
)
def test_only_successful_forbidden_sql_is_a_leak(tmp_path, error_name, status):
    def execute(*args, **kwargs):
        access.queries.append({"query_id": "q1", "error": error_name})
        raise AssertionError("Unexpected engine outcome")

    access = SimpleNamespace(queries=[], execute=execute)
    report = Report(tmp_path / "run")
    with pytest.raises(AssertionError), Attempt(report, "bronze_query", access) as attempt:
        attempt.sql("SELECT * FROM hospital.bronze.access_probe", denied=True)
    row = report.cases["bronze_query"]
    assert row["status"] == status
    assert row["attempts"][-1]["query_id"] == "q1"


def test_measurement_can_succeed_while_reporting_residual_leak(tmp_path):
    report = Report(tmp_path / "run")
    with Attempt(report, "aggregate_inference", None) as attempt:
        attempt.row.update(status="leaked", reason="Measured residual risk")
    assert report.cases["aggregate_inference"]["status"] == "leaked"


def test_linkage_excludes_ambiguous_and_incomplete_keys_and_scores_against_truth():
    observed = [
        ("a", "40-49", "F", "021**"),
        ("b", "40-49", "F", "021**"),  # Ambiguous key cannot identify a patient.
        ("c", "50-59", "M", "999**"),
        ("d", "60-69", "M", None),  # Missing QIs cannot establish a link.
        ("wrong", "70-79", "F", "100**"),
    ]
    auxiliary = [*observed[:4], ("truth", "70-79", "F", "100**")]
    metrics = linkage_metrics(observed, auxiliary)
    assert metrics == {
        "population": 5,
        "complete_auxiliary_rows": 4,
        "unique_matches": 2,
        "correct_links": 1,
        "linkage_rate": 0.2,
        "match_precision": 0.5,
    }
    assert linkage_metrics([], [])["linkage_rate"] is None


@pytest.mark.parametrize(
    "content",
    [
        "case,expected,evidence,status\nx,a,b,not_run\nx,a,b,not_run\n",
        "case,expected,evidence,status\nx,a,b,pass\n",
    ],
)
def test_invalid_inventory_is_rejected(tmp_path, content):
    path = tmp_path / "cases.csv"
    path.write_text(content)
    with pytest.raises(ValueError):
        load_cases(path)
