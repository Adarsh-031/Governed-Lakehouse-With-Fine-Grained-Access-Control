"""Reject DDL drift that could silently expand the approved patient inventory."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_ddl_matches_approved_resources_types_and_patient_identifier_boundary():
    tables = yaml.safe_load((ROOT / "policies/schema-contract.yaml").read_text())["tables"]
    tags = yaml.safe_load((ROOT / "ingest/pii_tags.yaml").read_text())["tables"]
    expected = {spec["resource"]: key for key, spec in tables.items() if key != "audit"}
    found = {}
    for path in sorted((ROOT / "sql/schema").glob("*.sql")):
        sql = re.sub(r"--[^\n]*", "", path.read_text())
        for match in re.finditer(
            r"CREATE TABLE IF NOT EXISTS (\w+\.\w+\.\w+)\s*\((.*?)\)\s*WITH\s*\((.*?)\);",
            sql,
            flags=re.DOTALL,
        ):
            resource, body, properties = match.groups()
            assert resource in expected and resource not in found
            # Split columns only at commas outside precision/scale parentheses.
            columns = {}
            required = set()
            for declaration in re.split(r",(?![^()]*\))", body):
                column, sql_type = declaration.strip().split(None, 1)
                if sql_type.endswith(" NOT NULL"):
                    required.add(column)
                    sql_type = sql_type.removesuffix(" NOT NULL")
                assert column not in columns
                columns[column] = sql_type
            key = expected[resource]
            assert columns == tables[key]["columns"]
            assert "patient_token" in required
            assert "format = 'PARQUET'" in properties
            assert "format_version = 2" in properties
            assert "location" not in properties  # Warehouse controls placement.
            if key != "identity":
                # doctor_user is explicitly required protected authorization metadata.
                assert {
                    column
                    for column in columns
                    if tags[key][column]["classification"] == "direct_identifier"
                } <= ({"doctor_user"} if key == "care_team" else set())
            found[resource] = required
    assert set(found) == set(expected)
    assert {"doctor_user", "valid_from"} <= found["hospital.silver.care_team"]
    assert "encounter_id" in found["hospital.silver.encounters"]
    assert {"claim_id", "encounter_id"} <= found["hospital.silver.claims"]
