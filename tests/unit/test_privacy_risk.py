"""Regression tests for researcher-visible uniqueness and inference measurements."""

import pytest

from tests.bypass.privacy import (
    aggregate_inference_metrics,
    combine_privacy_metrics,
    equivalence_class_metrics,
    quasi_identifier_linkage_metrics,
    with_rare_condition_signatures,
)


def population():
    return [
        {"patient_token": "p1", "age_band": "40-49", "sex": "F", "zip": "021**"},
        {"patient_token": "p2", "age_band": "40-49", "sex": "F", "zip": "021**"},
        {"patient_token": "p3", "age_band": "50-59", "sex": "M", "zip": "999**"},
        {"patient_token": "p4", "age_band": "60-69", "sex": "M", "zip": None},
    ]


def test_k_anonymity_reports_minimum_class_and_unique_percent():
    result = equivalence_class_metrics(population(), ["age_band", "sex", "zip"])
    assert result["population"] == 4
    assert result["equivalence_classes"] == 3
    assert result["k_min"] == 1
    assert result["unique_records"] == 2
    assert result["unique_percent"] == 50.0
    assert result["class_size_histogram"] == {"1": 2, "2": 1}


def test_empty_population_has_no_fabricated_anonymity_claim():
    result = equivalence_class_metrics([], ["age_band", "sex"])
    assert result["population"] == 0
    assert result["k_min"] is None
    assert result["unique_percent"] is None


def test_aggregate_differencing_is_recorded_as_disclosure():
    result = aggregate_inference_metrics(
        population(), quasi_identifiers=["age_band", "sex", "zip"], target_index=2
    )
    assert result["singleton_groups"] == 2
    assert result["singleton_percent"] == 50.0
    assert result["count_difference"] == 1
    assert result["target_present_reconstructed"] is True


def test_linkage_ignores_incomplete_and_ambiguous_keys_and_scores_truth():
    released = population()
    auxiliary = [
        dict(population()[0]),
        dict(population()[1]),
        dict(population()[2]),
        dict(population()[3]),
        {"patient_token": "not-p3", "age_band": "50-59", "sex": "M", "zip": "999**"},
    ]
    result = quasi_identifier_linkage_metrics(
        released, auxiliary, quasi_identifiers=["age_band", "sex", "zip"]
    )
    assert result == {
        "population": 5,
        "complete_auxiliary_rows": 4,
        "unique_matches": 2,
        "correct_links": 1,
        "linkage_percent": 20.0,
        "match_precision_percent": 50.0,
    }


@pytest.mark.parametrize("columns", [[], ["not_a_column"]])
def test_invalid_quasi_identifier_columns_are_rejected(columns):
    with pytest.raises((ValueError, KeyError)):
        equivalence_class_metrics(population(), columns)


def test_combined_measurement_can_include_auxiliary_data():
    metrics = combine_privacy_metrics(
        population(),
        quasi_identifiers=["age_band", "sex", "zip"],
        auxiliary_rows=population(),
    )
    assert set(metrics) == {
        "k_anonymity",
        "aggregate_inference",
        "quasi_identifier_linkage",
    }


def test_rare_condition_signatures_use_distinct_patient_prevalence():
    patients = [
        {"patient_token": f"p{i}", "age_band": "40-49", "sex": "F", "zip": "021**"}
        for i in range(1, 5)
    ]
    encounters = [
        {"patient_token": "p1", "reason_code": "RARE"},
        {"patient_token": "p1", "reason_code": "COMMON"},
        {"patient_token": "p2", "reason_code": "COMMON"},
        {"patient_token": "p3", "reason_code": "COMMON"},
    ]
    result = with_rare_condition_signatures(patients, encounters, threshold_percent=25)
    signatures = {row["patient_token"]: row["rare_condition_signature"] for row in result}
    assert signatures == {
        "p1": ("RARE",),
        "p2": (),
        "p3": (),
        "p4": (),
    }


def test_rare_condition_threshold_is_validated():
    with pytest.raises(ValueError):
        with_rare_condition_signatures([], [], threshold_percent=0)
    with pytest.raises(ValueError):
        with_rare_condition_signatures([], [], threshold_percent=101)


def test_rare_condition_prevalence_counts_distinct_patients_not_encounters():
    patients = [
        {"patient_token": "p1"},
        {"patient_token": "p2"},
        {"patient_token": "p3"},
        {"patient_token": "p4"},
    ]
    encounters = [
        {"patient_token": "p1", "reason_code": "REPEATED"},
        {"patient_token": "p1", "reason_code": "REPEATED"},
        {"patient_token": "p1", "reason_code": "REPEATED"},
        {"patient_token": "p2", "reason_code": "REPEATED"},
    ]
    result = with_rare_condition_signatures(patients, encounters, threshold_percent=25)
    assert [row["rare_condition_signature"] for row in result] == [
        (),
        (),
        (),
        (),
    ]


def test_linkage_excludes_blank_quasi_identifier_values():
    released = [
        {"patient_token": "p1", "age_band": "40-49", "sex": "F", "zip": "021**"},
        {"patient_token": "p2", "age_band": "50-59", "sex": "M", "zip": ""},
    ]
    auxiliary = [
        {"patient_token": "p1", "age_band": "40-49", "sex": "F", "zip": "021**"},
        {"patient_token": "p2", "age_band": "50-59", "sex": "M", "zip": ""},
    ]
    result = quasi_identifier_linkage_metrics(
        released, auxiliary, quasi_identifiers=["age_band", "sex", "zip"]
    )
    assert result["complete_auxiliary_rows"] == 1
    assert result["unique_matches"] == 1
    assert result["correct_links"] == 1
