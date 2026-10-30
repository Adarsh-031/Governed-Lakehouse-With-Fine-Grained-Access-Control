"""Deterministic audit query-ID reconciliation helpers for issue #22."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

EXPECTED_EVENT_TYPES = ("QueryCreated", "QueryCompleted")


def reconcile_query_events(
    expected_query_ids: Iterable[str],
    observed_events: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """Measure query-level lifecycle coverage and duplicate/missing deliveries."""
    expected = sorted(set(expected_query_ids))
    expected_set = set(expected)
    counts: Counter[tuple[str, str]] = Counter()
    all_observed: Counter[tuple[str, str]] = Counter()
    unexpected: Counter[tuple[str, str]] = Counter()
    invalid = 0

    for event in observed_events:
        query_id = event.get("query_id")
        event_type = event.get("event_type")
        if (
            not isinstance(query_id, str)
            or not query_id
            or event_type not in EXPECTED_EVENT_TYPES
        ):
            invalid += 1
            continue

        pair = (query_id, event_type)
        all_observed[pair] += 1
        target = counts if query_id in expected_set else unexpected
        target[pair] += 1

    expected_pairs = {
        (query_id, event_type)
        for query_id in expected
        for event_type in EXPECTED_EVENT_TYPES
    }
    missing = sorted(expected_pairs - set(counts))
    covered = {
        query_id
        for query_id in expected
        if all(counts[(query_id, event_type)] for event_type in EXPECTED_EVENT_TYPES)
    }
    coverage = 100.0 * len(covered) / len(expected) if expected else 100.0

    return {
        "expected_query_count": len(expected),
        "observed_expected_query_count": len(covered),
        "coverage_percent": round(coverage, 2),
        "missing_query_ids": sorted({query_id for query_id, _ in missing}),
        "missing_event_pairs": [
            {"query_id": query_id, "event_type": event_type}
            for query_id, event_type in missing
        ],
        "missing_event_count": len(missing),
        "duplicate_event_count": sum(
            count - 1 for count in all_observed.values() if count > 1
        ),
        "unexpected_query_ids": sorted({query_id for query_id, _ in unexpected}),
        "unexpected_event_count": sum(unexpected.values()),
        "invalid_event_count": invalid,
        "expected_event_types": list(EXPECTED_EVENT_TYPES),
    }
