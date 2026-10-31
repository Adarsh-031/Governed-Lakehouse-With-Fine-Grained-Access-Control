"""Check classification coverage and treatment agreement, not runtime enforcement."""

import csv
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
GROUPS = {"doctor", "researcher", "billing", "admin"}
CLASSIFICATIONS = {
    "identifier",
    "direct_identifier",
    "quasi_identifier",
    "sensitive_clinical",
    "financial",
    "none",
}
STORAGE_TREATMENTS = {"tokenize", "restricted_table", "mask", "generalize", "drop", "keep"}
ROLE_VISIBILITY = {"keep": "visible", "drop": "hidden", "mask": "masked", "generalize": "masked"}


class UniqueKeyLoader(yaml.SafeLoader):
    """SafeLoader normally silently replaces earlier duplicate keys."""

    def construct_mapping(self, node, deep=False):
        keys = [self.construct_object(key, deep=deep) for key, _ in node.value]
        if len(keys) != len(set(keys)):
            raise ValueError(f"Duplicate YAML key at {node.start_mark}")
        return super().construct_mapping(node, deep=deep)


TAGS = yaml.load((ROOT / "ingest/pii_tags.yaml").read_text(), Loader=UniqueKeyLoader)
TABLES = yaml.safe_load((ROOT / "policies/schema-contract.yaml").read_text())["tables"]
with (ROOT / "policies/access-matrix.csv").open(newline="") as handle:
    MATRIX = {(r["group"], r["table"], r["column"]): r for r in csv.DictReader(handle)}
COLUMNS = [(table, column) for table, spec in TABLES.items() for column in spec["columns"]]


@pytest.mark.parametrize(
    "document", ["patients: {}\npatients: {}", "patients:\n  name: keep\n  name: drop"]
)
def test_duplicate_keys_cannot_silently_replace_classifications(document):
    with pytest.raises(ValueError, match="Duplicate YAML key"):
        yaml.load(document, Loader=UniqueKeyLoader)


def test_tags_cover_exactly_the_schema_inventory_including_identity_and_audit():
    assert set(TAGS) == {"version", "unlisted_source_columns", "source_fields", "tables"}
    assert TAGS["version"] == 1
    assert set(TAGS["tables"]) == set(TABLES)
    for table, spec in TABLES.items():
        assert set(TAGS["tables"][table]) == set(spec["columns"])


@pytest.mark.parametrize("table,column", COLUMNS)
def test_every_column_has_valid_explicit_classification_and_treatments(table, column):
    tag = TAGS["tables"][table][column]
    assert set(tag) in (
        {"classification", "treatment", "roles", "rationale"},
        {"classification", "treatment", "roles", "rationale", "masks"},
    )
    assert tag["classification"] in CLASSIFICATIONS
    assert tag["treatment"] in STORAGE_TREATMENTS
    assert set(tag["roles"]) == GROUPS
    assert set(tag["roles"].values()) <= ROLE_VISIBILITY.keys()
    assert isinstance(tag["rationale"], str) and tag["rationale"].strip()
    # A dropped inventory column would contradict its promised DDL presence.
    assert tag["treatment"] != "drop"
    assert set(tag.get("masks", {})) == {
        group for group, treatment in tag["roles"].items() if treatment in {"mask", "generalize"}
    }


@pytest.mark.parametrize("table,column", COLUMNS)
def test_role_treatments_and_exact_masks_match_the_access_matrix(table, column):
    tag = TAGS["tables"][table][column]
    for group in GROUPS:
        decision = MATRIX[group, table, column]
        assert ROLE_VISIBILITY[tag["roles"][group]] == decision["visibility"]
        assert tag.get("masks", {}).get(group, "") == decision["mask_sql"]
        if decision["operations"] == "NONE":
            assert tag["roles"][group] == "drop"


def test_source_identifiers_cannot_pass_through_to_shared_tables():
    assert TAGS["unlisted_source_columns"] == "drop"
    assert TAGS["source_fields"] == {
        "patients": {
            "patient_id": {
                "classification": "identifier",
                "treatment": "tokenize",
                "destination": "patients.patient_token",
            },
            "name": {
                "classification": "direct_identifier",
                "treatment": "restricted_table",
                "destination": "identity.name",
            },
            "ssn": {"classification": "direct_identifier", "treatment": "drop"},
        }
    }
    for source in TAGS["source_fields"]["patients"].values():
        if "destination" in source:
            table, column = source["destination"].split(".")
            target = TAGS["tables"][table][column]
            assert target["classification"] == source["classification"]
            assert target["treatment"] == source["treatment"]


def test_shared_tokens_are_pseudonymous_and_record_keys_are_preserved():
    for columns in TAGS["tables"].values():
        if "patient_token" in columns:
            tag = columns["patient_token"]
            assert tag["classification"] == "identifier"
            assert tag["treatment"] == "tokenize"
    for table, column in (
        ("encounters", "encounter_id"),
        ("claims", "claim_id"),
        ("claims", "encounter_id"),
        ("audit", "query_id"),
    ):
        tag = TAGS["tables"][table][column]
        assert tag["classification"] == "identifier"
        assert tag["treatment"] == "keep"


def test_clinical_and_financial_content_is_classified_independently_of_permissions():
    clinical = {
        "encounters": {
            "encounter_class",
            "code",
            "description",
            "reason_code",
            "reason_description",
        },
        "claims": {"procedure_code", "procedure_description"},
    }
    for table, columns in clinical.items():
        for column in columns:
            assert TAGS["tables"][table][column]["classification"] == "sensitive_clinical"
    for column in ("payer", "total_cost", "payer_coverage"):
        assert TAGS["tables"]["claims"][column]["classification"] == "financial"
    claims = TAGS["tables"]["claims"]
    assert claims["procedure_code"]["roles"]["billing"] == "keep"
    assert claims["procedure_description"]["roles"]["billing"] == "drop"


def test_research_generalization_preserves_exact_stored_values_for_care():
    patients = TAGS["tables"]["patients"]
    assert patients["age_band"]["treatment"] == "generalize"
    for column in ("birthdate", "age_band", "sex", "race", "ethnicity", "zip"):
        assert patients[column]["classification"] == "quasi_identifier"
    assert patients["birthdate"]["treatment"] == "keep"
    assert patients["birthdate"]["roles"]["researcher"] == "drop"
    # #46: research gets no row-level quasi-identifiers; only suppressed gold cells.
    for column in ("age_band", "sex", "race", "ethnicity", "zip"):
        assert patients[column]["roles"]["researcher"] == "drop"
    assert TAGS["tables"]["encounters"]["reason_code"]["roles"]["researcher"] == "drop"
    for table in ("researcher_demographics", "researcher_geography", "researcher_conditions"):
        for tag in TAGS["tables"][table].values():
            assert tag["treatment"] == "generalize"
            assert tag["roles"] == {
                "doctor": "drop",
                "researcher": "keep",
                "billing": "drop",
                "admin": "keep",
            }
    for table, column in (
        ("encounters", "start_time"),
        ("encounters", "stop_time"),
    ):
        tag = TAGS["tables"][table][column]
        assert tag["classification"] == "quasi_identifier"
        assert tag["treatment"] == "keep"
        assert tag["roles"]["doctor"] == tag["roles"]["admin"] == "keep"
        assert tag["roles"]["researcher"] == "generalize"


def test_direct_identifiers_and_restricted_query_text_remain_restricted():
    for table, column in (
        ("identity", "name"),
        ("care_team", "doctor_user"),
        ("audit", "authenticated_user"),
        ("audit", "query_text_restricted"),
    ):
        assert TAGS["tables"][table][column]["classification"] == "direct_identifier"
    name = TAGS["tables"]["identity"]["name"]
    assert name["treatment"] == "restricted_table"
    for tag in TAGS["tables"]["identity"].values():
        assert set(tag["roles"].values()) == {"drop"}
    assert set(TAGS["tables"]["audit"]["query_text_restricted"]["roles"].values()) == {"drop"}
    for tag in TAGS["tables"]["audit"].values():
        if tag["classification"] == "none":
            assert {tag["roles"][group] for group in ("doctor", "researcher", "billing")} == {
                "drop"
            }
