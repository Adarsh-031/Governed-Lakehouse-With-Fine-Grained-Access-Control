"""Guard against vacuous access checks, leaked rows, and ineffective masks."""

import json
from datetime import datetime

import pytest

from tests.access.oracle import Oracle, assert_rows, masked_value


@pytest.mark.parametrize(
    "value,expected",
    [
        ("02139", "021**"),
        ("02139-1234", "021**"),
        ("00000", "000**"),
        (None, None),
        ("", None),
        ("1234", None),
        ("123456", None),
        ("ABC12", None),
        ("02139-ABCD", None),
        ("０２１３９", None),
    ],
)
def test_zip_mask_has_literal_expectations(value, expected):
    assert masked_value("patients", "zip", value) == expected


@pytest.mark.parametrize("column", ["start_time", "stop_time"])
def test_month_mask_preserves_null_and_has_literal_expectations(column):
    assert masked_value("encounters", column, None) is None
    assert masked_value("encounters", column, "2026-10-28 12:34:56.123456") == datetime(2026, 10, 1)


def test_new_masks_require_an_explicit_independent_expectation():
    with pytest.raises(AssertionError, match="No independent mask"):
        masked_value("patients", "birthdate", "2000-01-01")


@pytest.mark.parametrize(
    "actual,expected",
    [
        ([("allowed",), ("leak",)], [("allowed",)]),
        ([], [("allowed",)]),
        ([("leak",)], [("allowed",)]),  # Same cardinality can still leak.
        ([("allowed",), ("allowed",)], [("allowed",)]),  # Duplicates matter.
        ([("02139",)], [("021**",)]),  # Identity mask must fail.
    ],
)
def test_row_comparison_rejects_extra_missing_replaced_and_duplicate_values(actual, expected):
    with pytest.raises(AssertionError, match="unexpected rows"):
        assert_rows(actual, expected, "patients", ["patient_token"])


def test_row_comparison_does_not_require_engine_order():
    assert_rows([("b",), ("a",), ("a",)], [("a",), ("b",), ("a",)], "patients", ["patient_token"])


def test_doctor_oracle_uses_saved_sets_and_table_population_without_live_authorization(tmp_path):
    files = {
        "care-team-oracle.json": {
            "valid_from": "2025-01-01",
            "valid_to": None,
            "expected_patient_tokens": {
                "doctor_alice": ["alice", "shared"],
                "doctor_bob": ["bob", "shared"],
            },
        },
        "policy-boundary-oracle.json": {
            "utc_date": "2026-10-29",
            "expected_patient_tokens": {"doctor_alice": [], "doctor_bob": []},
        },
        "access-data-oracle.json": {
            "patients": [
                {"patient_token": token} for token in ("alice", "bob", "shared", "unassigned")
            ],
            "encounters": [
                {"patient_token": token} for token in ("bob", "shared", "shared", "unassigned")
            ],
        },
    }
    for name, content in files.items():
        (tmp_path / name).write_text(json.dumps(content))
    oracle = Oracle(tmp_path)
    assert oracle.tokens("doctor_alice", "patients") == {"alice", "shared"}
    assert oracle.tokens("doctor_alice", "encounters") == {"shared"}
    assert oracle.rows("doctor_alice", "encounters", ["patient_token"]) == [
        ("shared",),
        ("shared",),
    ]
    assert oracle.tokens("doctor_bob", "encounters") == {"bob", "shared"}
