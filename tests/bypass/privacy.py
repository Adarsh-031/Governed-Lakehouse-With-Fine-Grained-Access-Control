"""Privacy-risk summaries for researcher-visible quasi-identifiers.

These helpers deliberately report measured equivalence-class statistics; they do
not label synthetic records anonymous or make claims about real populations.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def k_anonymity_metrics(
    records: Iterable[Mapping[str, Any]],
    quasi_identifiers: Sequence[str],
    *,
    threshold: int = 5,
) -> dict[str, Any]:
    """Summarize k-anonymity for a QI tuple.

    NULL is retained as an explicit equivalence-class value and is also counted
    separately. Callers may run a second complete-case summary to expose the
    effect of missing QIs. Percent unique means the percentage of records that
    belong to a singleton equivalence class, not the percentage of classes.
    """
    if not quasi_identifiers:
        raise ValueError("At least one quasi-identifier is required")
    if threshold < 2:
        raise ValueError("k-anonymity threshold must be at least 2")

    rows = list(records)
    class_counts = Counter(
        tuple(row.get(column) for column in quasi_identifiers) for row in rows
    )
    histogram = Counter(class_counts.values())
    population = len(rows)
    minimum_k = min(class_counts.values(), default=None)
    unique_records = histogram.get(1, 0)
    records_below_threshold = sum(
        size * group_count
        for size, group_count in histogram.items()
        if size < threshold
    )
    missing_records = sum(
        any(row.get(column) is None for column in quasi_identifiers) for row in rows
    )

    return {
        "population": population,
        "quasi_identifiers": list(quasi_identifiers),
        "k_threshold": threshold,
        "equivalence_classes": len(class_counts),
        "minimum_k": minimum_k,
        "unique_equivalence_classes": histogram.get(1, 0),
        "unique_records": unique_records,
        "unique_record_percent": (
            round(100.0 * unique_records / population, 2) if population else None
        ),
        "groups_below_k": sum(
            group_count for size, group_count in histogram.items() if size < threshold
        ),
        "records_in_groups_below_k": records_below_threshold,
        "records_in_groups_below_k_percent": (
            round(100.0 * records_below_threshold / population, 2)
            if population
            else None
        ),
        "meets_k_threshold": minimum_k is not None and minimum_k >= threshold,
        "records_with_missing_qis": missing_records,
        "class_size_histogram": {
            str(size): histogram[size] for size in sorted(histogram)
        },
    }


def rare_reason_code_profiles(
    patients: Iterable[Mapping[str, Any]],
    encounters: Iterable[Mapping[str, Any]],
    *,
    rare_threshold: int = 5,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Attach each patient's rare researcher-visible encounter reason codes.

    Encounter.reason_code is the available condition-like attribute in this
    schema; this function does not claim that it is a complete diagnosis table.
    A code is considered rare when it appears for fewer than rare_threshold
    distinct patients. Repeated encounters for one patient count once.
    """
    if rare_threshold < 2:
        raise ValueError("Rare-code threshold must be at least 2")

    patient_rows = list(patients)
    encounter_rows = list(encounters)
    patient_tokens = {
        str(row["patient_token"])
        for row in patient_rows
        if row.get("patient_token") is not None
    }
    code_patients: dict[str, set[str]] = defaultdict(set)
    patient_codes: dict[str, set[str]] = defaultdict(set)

    for row in encounter_rows:
        token = row.get("patient_token")
        code = row.get("reason_code")
        if token is None or code is None:
            continue
        token, code = str(token), str(code).strip()
        if not code or token not in patient_tokens:
            continue
        code_patients[code].add(token)
        patient_codes[token].add(code)

    rare_codes = {
        code for code, tokens in code_patients.items() if len(tokens) < rare_threshold
    }
    profiles = [
        {
            **row,
            "rare_reason_codes": tuple(
                sorted(patient_codes.get(str(row["patient_token"]), set()) & rare_codes)
            ),
        }
        for row in patient_rows
    ]
    rare_patients = {
        token
        for token in patient_tokens
        if patient_codes.get(token, set()) & rare_codes
    }
    prevalence_histogram = Counter(len(tokens) for tokens in code_patients.values())

    summary = {
        "attribute": "encounters.reason_code",
        "interpretation": "researcher-visible encounter reason-code proxy, not a complete diagnosis table",
        "rare_threshold_distinct_patients": rare_threshold,
        "distinct_reason_codes": len(code_patients),
        "rare_reason_code_count": len(rare_codes),
        "patients_with_rare_reason_codes": len(rare_patients),
        "patients_without_rare_reason_codes": len(patient_tokens - rare_patients),
        "patient_reason_code_pairs": sum(len(tokens) for tokens in code_patients.values()),
        "reason_code_patient_prevalence_histogram": {
            str(count): prevalence_histogram[count]
            for count in sorted(prevalence_histogram)
        },
    }
    return profiles, summary
