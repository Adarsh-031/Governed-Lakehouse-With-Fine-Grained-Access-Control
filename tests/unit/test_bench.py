"""Offline checks for the policy-overhead benchmark helpers (#25)."""

import re

import pytest

from bench.workload import (
    ROOT,
    baseline_sql,
    load_policy,
    parse_queries,
    percentile,
    summarize,
    table_rule,
    verification_sql,
    workloads,
)


def test_every_role_query_is_parsed_with_expected_columns():
    items = workloads()
    assert [i["query_id"] for i in items] == [
        *(f"D{n}" for n in range(1, 5)),
        *(f"R{n}" for n in range(1, 5)),
        *(f"B{n}" for n in range(1, 5)),
    ]
    assert all(i["columns"] and ";" not in i["enforced"] for i in items)


def test_semicolon_inside_comment_does_not_split(tmp_path):
    path = tmp_path / "q.sql"
    path.write_text(
        "-- header; with a semicolon\n\n-- X1. A.\n-- Expected columns: a, b.\nSELECT 1 AS a, 2 AS b;\n"
    )
    assert parse_queries(path) == [
        {"id": "X1", "sql": "SELECT 1 AS a, 2 AS b", "columns": ["a", "b"]}
    ]


def test_rule_matching_is_first_match_per_user_and_group():
    rules, groups, _ = load_policy()
    alice = table_rule(rules, groups, "doctor_alice", "patients")
    assert "'doctor_alice'" in alice["filter"]
    assert "'doctor_bob'" in table_rule(rules, groups, "doctor_bob", "patients")["filter"]
    assert "SELECT" not in table_rule(rules, groups, "billing", "patients").get("privileges", [])


def test_baseline_inlines_doctor_filter_and_keeps_aliases():
    rules, groups, columns = load_policy()
    sql = "SELECT p.patient_token FROM hospital.silver.patients AS p"
    rewritten = baseline_sql(sql, "doctor_alice", rules, groups, columns)
    assert rewritten.endswith(") AS p")
    assert "ct.doctor_user = 'doctor_alice'" in rewritten
    assert "FROM hospital.silver.patients AS patients WHERE EXISTS" in rewritten


def test_baseline_applies_researcher_masks_and_drops_hidden_columns():
    rules, groups, columns = load_policy()
    rewritten = baseline_sql(
        "SELECT zip FROM hospital.silver.patients", "researcher", rules, groups, columns
    )
    assert "substr(zip, 1, 3) || '**'" in rewritten
    assert "birthdate" not in rewritten
    assert "WHERE" not in rewritten.split("AS patients")[0].split("FROM hospital")[1]


def test_baseline_rejects_tables_the_role_cannot_read():
    rules, groups, columns = load_policy()
    with pytest.raises(ValueError, match="no SELECT"):
        baseline_sql("SELECT 1 FROM hospital.silver.claims", "researcher", rules, groups, columns)


def test_verification_removes_only_trailing_limit():
    sql = verification_sql("SELECT a FROM t ORDER BY a DESC\nLIMIT 100", ["a"])
    assert "LIMIT" not in sql
    assert sql.startswith("SELECT count(*) AS result_rows, checksum(ROW(a))")
    assert "ORDER BY a DESC" in sql


def test_percentile_and_summary_overhead():
    assert percentile(list(range(1, 11)), 0.95) == 10
    assert percentile([5], 0.95) == 5
    base = {
        "role": "doctor",
        "query_id": "D1",
        "phase": "warm",
        "cpu_ms": 1,
        "peak_memory_bytes": 1,
        "result_rows": 3,
    }
    samples = [
        *({**base, "variant": "baseline", "elapsed_ms": v} for v in (100, 100, 100)),
        *({**base, "variant": "enforced", "elapsed_ms": v} for v in (110, 110, 200)),
    ]
    rows = {r["variant"]: r for r in summarize(samples)}
    assert rows["baseline"]["median_ms"] == 100
    assert rows["enforced"]["p95_ms"] == 200
    assert rows["enforced"]["overhead_pct_vs_baseline"] == 10.0
    assert rows["baseline"]["overhead_pct_vs_baseline"] == ""


def test_plan_scales_and_documented_entrypoint():
    readme = (ROOT / "bench/README.md").read_text()
    assert "python -m bench.run" in readme
    assert re.search(r"not_run", readme)
