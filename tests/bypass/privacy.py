"""Privacy risk measurements over researcher-visible synthetic records.

No patient rows or quasi-identifier keys are written to public files. The
functions return aggregate metrics only; callers keep row-level evidence private.
"""

from collections import Counter, defaultdict


def _validated_columns(rows, quasi_identifiers):
    columns = tuple(quasi_identifiers)
    if not columns:
        raise ValueError("At least one quasi-identifier is required")
    for row in rows:
        for column in columns:
            if column not in row:
                raise KeyError(column)
    return columns


def equivalence_class_metrics(rows, quasi_identifiers):
    """Return k-anonymity summary for the supplied released QI columns."""
    columns = _validated_columns(rows, quasi_identifiers)
    if not columns:
        raise ValueError("At least one quasi-identifier is required")
    counts = Counter(tuple(row.get(column) for column in columns) for row in rows)
    population = len(rows)
    if not population:
        return {
            "population": 0,
            "quasi_identifiers": list(columns),
            "equivalence_classes": 0,
            "k_min": None,
            "unique_records": 0,
            "unique_percent": None,
            "class_size_histogram": {},
        }
    unique = sum(count for count in counts.values() if count == 1)
    return {
        "population": population,
        "quasi_identifiers": list(columns),
        "equivalence_classes": len(counts),
        "k_min": min(counts.values()),
        "unique_records": unique,
        "unique_percent": 100.0 * unique / population,
        "class_size_histogram": {
            str(size): sum(count for count in counts.values() if count == size)
            for size in sorted(set(counts.values()))
        },
    }


def aggregate_inference_metrics(rows, *, quasi_identifiers, target_index=0):
    """Measure singleton cells and exact count differencing on released rows."""
    columns = _validated_columns(rows, quasi_identifiers)
    if not rows:
        return {
            "population": 0,
            "singleton_groups": 0,
            "singleton_percent": None,
            "total_count": 0,
            "excluded_count": 0,
            "count_difference": None,
            "target_present_reconstructed": None,
        }
    if not 0 <= target_index < len(rows):
        raise IndexError("target_index is outside the released population")
    if any("patient_token" not in row for row in rows):
        raise KeyError("patient_token")
    cells = Counter(tuple(row.get(column) for column in columns) for row in rows)
    target_token = rows[target_index]["patient_token"]
    excluded_count = sum(row["patient_token"] != target_token for row in rows)
    total_count = len(rows)
    singleton_groups = sum(size == 1 for size in cells.values())
    return {
        "population": total_count,
        "singleton_groups": singleton_groups,
        "singleton_percent": 100.0 * singleton_groups / total_count,
        "total_count": total_count,
        "excluded_count": excluded_count,
        "count_difference": total_count - excluded_count,
        "target_present_reconstructed": total_count - excluded_count == 1,
    }


def quasi_identifier_linkage_metrics(released, auxiliary, *, quasi_identifiers):
    """Score exact auxiliary-data linkage; ambiguous/incomplete keys do not link."""
    columns = _validated_columns(released, quasi_identifiers)
    _validated_columns(auxiliary, quasi_identifiers)
    if any("patient_token" not in row for row in [*released, *auxiliary]):
        raise KeyError("patient_token")
    index = defaultdict(set)
    for row in released:
        key = tuple(row.get(column) for column in columns)
        if all(value is not None for value in key):
            index[key].add(row["patient_token"])

    complete = attempted = correct = 0
    for row in auxiliary:
        key = tuple(row.get(column) for column in columns)
        if any(value is None for value in key):
            continue
        complete += 1
        candidates = index.get(key, set())
        if len(candidates) != 1:
            continue
        attempted += 1
        correct += row["patient_token"] in candidates

    population = len(auxiliary)
    return {
        "population": population,
        "complete_auxiliary_rows": complete,
        "unique_matches": attempted,
        "correct_links": correct,
        "linkage_percent": 100.0 * correct / population if population else None,
        "match_precision_percent": 100.0 * correct / attempted if attempted else None,
    }


def combine_privacy_metrics(rows, *, quasi_identifiers, auxiliary_rows=None):
    """Return all requested metrics from the same released researcher view."""
    output = {
        "k_anonymity": equivalence_class_metrics(rows, quasi_identifiers),
        "aggregate_inference": aggregate_inference_metrics(
            rows, quasi_identifiers=quasi_identifiers
        ),
    }
    if auxiliary_rows is not None:
        output["quasi_identifier_linkage"] = quasi_identifier_linkage_metrics(
            rows, auxiliary_rows, quasi_identifiers=quasi_identifiers
        )
    return output


def with_rare_condition_signatures(patient_rows, encounter_rows, *, threshold_percent=5):
    """Attach signatures for uncommon researcher-visible encounter reason codes.

    Rarity is measured by distinct patients, not encounter-row frequency. A
    reason code is rare when it occurs in at most max(1, ceil(N * threshold/100))
    patients. This is an attacker-feature definition, not a privacy control.
    """
    import math

    if not 0 < threshold_percent <= 100:
        raise ValueError("threshold_percent must be in (0, 100]")
    patient_tokens = {row["patient_token"] for row in patient_rows}
    by_patient = defaultdict(set)
    prevalence = Counter()
    for row in encounter_rows:
        token, code = row.get("patient_token"), row.get("reason_code")
        if token in patient_tokens and code not in (None, ""):
            by_patient[token].add(code)
    for codes in by_patient.values():
        for code in codes:
            prevalence[code] += 1
    threshold = max(1, math.ceil(len(patient_rows) * threshold_percent / 100))
    rare_codes = {code for code, count in prevalence.items() if count <= threshold}
    return [
        {
            **row,
            "rare_condition_signature": tuple(
                sorted(by_patient.get(row["patient_token"], set()) & rare_codes)
            ),
        }
        for row in patient_rows
    ]
