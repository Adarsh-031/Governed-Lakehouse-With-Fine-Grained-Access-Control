"""Unit tests for researcher-visible QI and rare-code risk summaries."""

import pytest

from tests.bypass.privacy import k_anonymity_metrics, rare_reason_code_profiles


def test_k_anonymity_reports_singleton_records_and_small_classes():
    records = [
        {"age_band": "40-49", "sex": "F", "zip": "021**"},
        {"age_band": "40-49", "sex": "F", "zip": "021**"},
        {"age_band": "50-59", "sex": "M", "zip": "999**"},
        {"age_band": "60-69", "sex": "F", "zip": None},
    ]

    result = k_anonymity_metrics(records, ("age_band", "sex", "zip"), threshold=5)

    assert result["population"] == 4
    assert result["equivalence_classes"] == 3
    assert result["minimum_k"] == 1
    assert result["unique_equivalence_classes"] == 2
    assert result["unique_records"] == 2
    assert result["unique_record_percent"] == 50.0
    assert result["records_in_groups_below_k"] == 4
    assert result["records_with_missing_qis"] == 1
    assert result["meets_k_threshold"] is False
    assert result["class_size_histogram"] == {"1": 2, "2": 1}


def test_k_anonymity_empty_population_has_no_false_anonymity_claim():
    result = k_anonymity_metrics([], ("age_band", "sex"), threshold=5)

    assert result["population"] == 0
    assert result["minimum_k"] is None
    assert result["unique_record_percent"] is None
    assert result["meets_k_threshold"] is False


def test_k_anonymity_validates_quasi_identifiers_and_threshold():
    with pytest.raises(ValueError, match="At least one"):
        k_anonymity_metrics([{"age_band": "40-49"}], ())
    with pytest.raises(ValueError, match="at least 2"):
        k_anonymity_metrics([{"age_band": "40-49"}], ("age_band",), threshold=1)


def test_rare_reason_codes_count_distinct_patients_not_encounters():
    patients = [
        {"patient_token": "p1", "age_band": "40-49"},
        {"patient_token": "p2", "age_band": "40-49"},
        {"patient_token": "p3", "age_band": "50-59"},
        {"patient_token": "p4", "age_band": "60-69"},
    ]
    encounters = [
        {"patient_token": "p1", "reason_code": "COMMON"},
        {"patient_token": "p1", "reason_code": "COMMON"},
        {"patient_token": "p2", "reason_code": "COMMON"},
        {"patient_token": "p3", "reason_code": "COMMON"},
        {"patient_token": "p4", "reason_code": "RARE-A"},
        {"patient_token": "p4", "reason_code": None},
    ]

    profiles, summary = rare_reason_code_profiles(
        patients, encounters, rare_threshold=3
    )

    assert {row["patient_token"]: row["rare_reason_codes"] for row in profiles} == {
        "p1": (),
        "p2": (),
        "p3": (),
        "p4": ("RARE-A",),
    }
    assert summary["distinct_reason_codes"] == 2
    assert summary["rare_reason_code_count"] == 1
    assert summary["patients_with_rare_reason_codes"] == 1
    assert summary["patient_reason_code_pairs"] == 4


def test_rare_reason_code_profiles_ignore_orphan_encounters_and_validate_threshold():
    patients = [{"patient_token": "p1"}]
    encounters = [{"patient_token": "orphan", "reason_code": "RARE"}]
    profiles, summary = rare_reason_code_profiles(patients, encounters)

    assert profiles[0]["rare_reason_codes"] == ()
    assert summary["distinct_reason_codes"] == 0
    with pytest.raises(ValueError, match="at least 2"):
        rare_reason_code_profiles(patients, encounters, rare_threshold=1)
