"""Opt-in, isolated stack; durable reports survive setup errors and interruptions."""

import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from tests.access.conftest import access as clinical_access  # noqa: F401

from .report import ROOT, Attempt, Report


def pytest_addoption(parser):
    parser.addoption(
        "--bypass-results-dir", help="New private directory; existing paths are refused."
    )


def pytest_sessionstart(session):
    if os.environ.get("RUN_BYPASS_INTEGRATION") == "1":
        name = f"bypass-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid4().hex[:8]}"
        directory = session.config.getoption("--bypass-results-dir") or ROOT / "results/raw" / name
        try:
            session.config._bypass_report = Report(directory)
        except FileExistsError:
            raise pytest.UsageError(
                f"Bypass evidence directory already exists: {directory}"
            ) from None


def pytest_collection_modifyitems(items):
    if os.environ.get("RUN_BYPASS_INTEGRATION") != "1":
        for item in items:
            if Path(__file__).parent in Path(str(item.path)).parents:
                item.add_marker(
                    pytest.mark.skip(reason="Set RUN_BYPASS_INTEGRATION=1 for Docker bypass tests.")
                )


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    result = outcome.get_result()
    report = getattr(item.config, "_bypass_report", None)
    case = getattr(item, "callspec", None)
    if report is not None and case is not None and "case" in case.params:
        row = report.cases[case.params["case"]]
        if result.when == "setup" and result.outcome != "passed":
            row["reason"] = (
                f"Setup {result.outcome}; no bypass attempt executed. See pytest diagnostics."
            )
        report.save()


def pytest_sessionfinish(session):
    report = getattr(session.config, "_bypass_report", None)
    if report is not None:
        report.context["pytest_exitstatus"] = int(session.exitstatus)
        report.save()


def pytest_terminal_summary(terminalreporter):
    report = getattr(terminalreporter.config, "_bypass_report", None)
    if report is not None:
        terminalreporter.write_line(f"Private bypass evidence: {report.directory}")


@pytest.fixture(scope="module")
def bypass(request):
    access = request.getfixturevalue("clinical_access")
    report = request.config._bypass_report
    for name in (
        "policy",
        "care-team-oracle.json",
        "policy-boundary-oracle.json",
        "access-data-oracle.json",
    ):
        source = access.directory / name
        destination = report.directory / name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copyfile(source, destination)
            destination.chmod(0o600)
    manifest = json.loads((ROOT / "policies/version.json").read_text())
    config = json.loads(access.compose("config", "--format", "json"))
    # Retain topology only. Full rendered Compose includes generated credentials.
    topology = {
        "backend_internal": config["networks"]["backend"]["internal"],
        "services": {
            name: {"networks": sorted(spec["networks"]), "ports": spec.get("ports", [])}
            for name, spec in config["services"].items()
        },
    }
    report.context.update(
        policy_version=manifest["policy_version"],
        policy_digest=manifest["policy_digest"],
        oracle_sha256=access.oracle_sha256,
        topology=topology,
        boundary_date_utc=access.oracle.boundary["utc_date"],
    )
    access.compose.override_file.write_text(
        access.compose.override_file.read_text()
        + "  catalog-check:\n    volumes:\n"
        + f"      - {ROOT / 'tests/bypass/backend_probe.py'}:/app/bypass_probe.py:ro\n"
    )
    report.save()

    def attempt(name):
        return Attempt(report, name, access)

    return attempt
