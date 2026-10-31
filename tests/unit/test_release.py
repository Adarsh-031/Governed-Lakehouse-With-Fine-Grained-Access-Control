"""Issue #46: aggregate-only research releases suppress every small cell."""

import pytest

from ingest.release import MIN_CELL, SCHEMAS, build, zip_prefix


def patient(index, age="30-39", sex="F", race="white", zip_code="02139"):
    return {
        "patient_token": f"v1:{index:064d}",
        "age_band": age,
        "sex": sex,
        "race": race,
        "zip": zip_code,
    }


def test_small_cells_are_suppressed_and_large_cells_count_distinct_patients():
    patients = [patient(i) for i in range(MIN_CELL)] + [
        patient(100 + i, age="90+", zip_code="99999") for i in range(MIN_CELL - 1)
    ]
    # Repeated encounters for one patient must not inflate a condition count.
    encounters = [
        {"patient_token": p["patient_token"], "reason_code": "common"} for p in patients
    ] * 3 + [{"patient_token": patients[0]["patient_token"], "reason_code": "rare"}]
    tables = build(patients, encounters)
    assert tables["researcher_demographics"].to_pylist() == [
        {"age_band": "30-39", "sex": "F", "race": "white", "patient_count": MIN_CELL}
    ]
    assert tables["researcher_geography"].to_pylist() == [
        {"zip_prefix": "021**", "patient_count": MIN_CELL}
    ]
    assert tables["researcher_conditions"].to_pylist() == [
        {"reason_code": "common", "patient_count": 2 * MIN_CELL - 1}
    ]
    for name, table in tables.items():
        assert table.schema.equals(SCHEMAS[name])
        assert "patient_token" not in table.column_names
        assert all(count >= MIN_CELL for count in table.column("patient_count").to_pylist())


def test_release_cannot_be_differenced_by_excluding_one_patient():
    patients = [patient(i) for i in range(MIN_CELL)]
    full = build(patients, [])
    without_one = build(patients[1:], [])
    # Dropping one person makes the cell disappear instead of revealing a count of 1.
    assert full["researcher_demographics"].num_rows == 1
    assert without_one["researcher_demographics"].num_rows == 0


def test_null_keys_form_their_own_cell_and_missing_reason_codes_are_ignored():
    patients = [patient(i, race=None, zip_code="ABC12") for i in range(MIN_CELL)]
    encounters = [{"patient_token": p["patient_token"], "reason_code": None} for p in patients]
    tables = build(patients, encounters)
    assert tables["researcher_demographics"].to_pylist()[0]["race"] is None
    assert tables["researcher_geography"].to_pylist() == [
        {"zip_prefix": None, "patient_count": MIN_CELL}
    ]
    assert tables["researcher_conditions"].num_rows == 0


@pytest.mark.parametrize(
    "value,expected",
    [
        ("02139", "021**"),
        ("02139-1234", "021**"),
        (None, None),
        ("ABC12", None),
        ("123456", None),
        ("", None),
        ("1234", None),
    ],
)
def test_zip_prefix_matches_the_former_research_mask(value, expected):
    assert zip_prefix(value) == expected


def test_threshold_must_exceed_one():
    with pytest.raises(ValueError):
        build([], [], min_cell=1)
