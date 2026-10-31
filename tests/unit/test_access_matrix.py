"""Validate the permission specification; these are not Trino enforcement tests."""

import csv
import json
from collections import Counter
from itertools import product
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GROUPS = {"doctor", "researcher", "billing", "admin"}
RELEASES = {"researcher_demographics", "researcher_geography", "researcher_conditions"}
TABLES = yaml.safe_load((ROOT / "policies/schema-contract.yaml").read_text())["tables"]
with (ROOT / "policies/access-matrix.csv").open(newline="") as handle:
    READER = csv.DictReader(handle)
    HEADERS = READER.fieldnames
    ROWS = list(READER)
INDEX = {(row["group"], row["table"], row["column"]): row for row in ROWS}


def test_every_inventory_column_has_exactly_one_decision_per_group():
    assert HEADERS == [
        "group",
        "table",
        "column",
        "operations",
        "row_scope",
        "visibility",
        "mask_sql",
    ]
    expected = {
        (group, table, column)
        for group in GROUPS
        for table, spec in TABLES.items()
        for column in spec["columns"]
    }
    counts = Counter((row["group"], row["table"], row["column"]) for row in ROWS)
    assert set(counts) == expected  # Missing AND unexpected columns/groups fail closed.
    assert set(counts.values()) == {1}
    assert {spec["resource"] for spec in TABLES.values()} == {
        "hospital.silver.patients",
        "hospital.silver.encounters",
        "hospital.silver.claims",
        "hospital.silver.care_team",
        "identity.identity.patient_identity",
        "audit.audit.query_events",
        *(f"hospital.gold.{table}" for table in RELEASES),
    }


@pytest.mark.parametrize(
    "row", ROWS, ids=lambda r: "/".join(r[k] for k in ("group", "table", "column"))
)
def test_decisions_are_explicit_and_denied_columns_cannot_carry_masks(row):
    assert set(row) == set(HEADERS), "Malformed CSV row (for example an unquoted comma)"
    assert all(value is not None and value == value.strip() for value in row.values())
    assert row["operations"] in {"SELECT", "NONE"}
    assert row["row_scope"] in {"NONE", "ALL_MVP", "ACTIVE_CARE_TEAM"}
    assert row["visibility"] in {"visible", "masked", "hidden"}
    assert bool(row["mask_sql"]) == (row["visibility"] == "masked")
    if row["operations"] == "NONE":
        assert row["row_scope"] == "NONE"
        assert row["visibility"] == "hidden"
    else:
        assert row["row_scope"] != "NONE"


@pytest.mark.parametrize("group,table", list(product(sorted(GROUPS), TABLES)))
def test_table_operations_and_scopes_match_role_boundaries(group, table):
    expected_tables = {
        "doctor": {"patients", "encounters"},
        "researcher": {"patients", "encounters", *RELEASES},
        "billing": {"claims"},
        "admin": {"patients", "encounters", "claims", "care_team", "audit", *RELEASES},
    }
    expected_scope = "NONE"
    if table in expected_tables[group]:
        expected_scope = "ACTIVE_CARE_TEAM" if group == "doctor" else "ALL_MVP"
    decisions = [INDEX[group, table, column] for column in TABLES[table]["columns"]]
    assert {row["row_scope"] for row in decisions} == {expected_scope}
    operation = "NONE" if expected_scope == "NONE" else "SELECT"
    assert {row["operations"] for row in decisions} == {operation}
    if operation == "SELECT":
        assert any(row["visibility"] != "hidden" for row in decisions)


def test_research_masks_and_hidden_fields_cannot_leak_exact_values():
    decisions = {
        (table, column): row
        for (group, table, column), row in INDEX.items()
        if group == "researcher" and row["operations"] == "SELECT"
    }
    # #46: row-level quasi-identifiers and reason codes are hidden from research;
    # they are released only as suppressed aggregates in hospital.gold.
    assert {key for key, row in decisions.items() if row["visibility"] == "hidden"} == {
        ("patients", "birthdate"),
        ("patients", "age_band"),
        ("patients", "sex"),
        ("patients", "race"),
        ("patients", "ethnicity"),
        ("patients", "zip"),
        ("encounters", "description"),
        ("encounters", "reason_code"),
        ("encounters", "reason_description"),
    }
    expected_masks = {
        ("encounters", "start_time"): "CAST(date_trunc('month', start_time) AS TIMESTAMP(6))",
        ("encounters", "stop_time"): "CAST(date_trunc('month', stop_time) AS TIMESTAMP(6))",
    }
    assert {
        key: row["mask_sql"] for key, row in decisions.items() if row["visibility"] == "masked"
    } == expected_masks
    for table, column in expected_masks:
        assert TABLES[table]["columns"][column] == "TIMESTAMP(6)"
    assert TABLES["patients"]["columns"]["birthdate"] == "DATE"
    assert TABLES["patients"]["columns"]["age_band"] == "VARCHAR"


def test_research_releases_are_aggregate_only_without_patient_keys():
    for table in RELEASES:
        columns = TABLES[table]["columns"]
        assert TABLES[table]["resource"] == f"hospital.gold.{table}"
        assert "patient_token" not in columns
        assert columns["patient_count"] == "BIGINT"
        for group in GROUPS:
            row = INDEX[group, table, "patient_count"]
            assert row["operations"] == ("SELECT" if group in {"researcher", "admin"} else "NONE")


def test_billing_has_codes_and_costs_without_free_text_or_patient_demographics():
    visible = {
        column
        for (group, table, column), row in INDEX.items()
        if group == "billing" and table == "claims" and row["visibility"] == "visible"
    }
    assert visible == {
        "claim_id",
        "patient_token",
        "encounter_id",
        "payer",
        "procedure_code",
        "total_cost",
        "payer_coverage",
    }
    assert INDEX["billing", "claims", "procedure_description"]["visibility"] == "hidden"


def test_clinical_and_admin_projections_preserve_their_approved_stored_values():
    for (group, table, column), row in INDEX.items():
        if row["operations"] == "SELECT" and (
            group == "doctor" or (group == "admin" and table != "audit")
        ):
            assert row["visibility"] == "visible"
        if column == "patient_token" and row["operations"] == "SELECT":
            assert row["visibility"] == "visible"


def test_shared_tables_exclude_direct_identifiers_and_audit_matches_normalized_contract():
    for table in ("patients", "encounters", "claims", "care_team"):
        columns = TABLES[table]["columns"]
        assert "patient_token" in columns
        assert not {"patient_id", "name", "ssn", "address", "email", "phone"} & columns.keys()
    assert set(TABLES["care_team"]["columns"]) == {
        "doctor_user",
        "patient_token",
        "valid_from",
        "valid_to",
    }
    assert TABLES["care_team"]["columns"]["valid_from"] == "DATE"
    assert TABLES["care_team"]["columns"]["valid_to"] == "DATE"
    assert set(TABLES["identity"]["columns"]) == {"patient_token", "name"}
    contract = json.loads((ROOT / "audit/event-contract.json").read_text())
    assert set(TABLES["audit"]["columns"]) == set(contract["fields"])
    assert INDEX["admin", "audit", "query_text_restricted"]["visibility"] == "hidden"
    assert all(
        row["visibility"] == "visible"
        for (group, table, column), row in INDEX.items()
        if group == "admin" and table == "audit" and column != "query_text_restricted"
    )
