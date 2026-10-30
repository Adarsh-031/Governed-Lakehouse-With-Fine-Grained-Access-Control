"""Pure reconciliation tests for audit collector outage evidence (#22)."""

from audit.reliability import reconcile_query_events


def test_reconciliation_counts_missing_duplicates_and_coverage():
    expected = ["q1", "q2", "q3", "q4"]
    observed = [
        {"query_id": "q1", "event_type": "QueryCreated"},
        {"query_id": "q1", "event_type": "QueryCompleted"},
        {"query_id": "q2", "event_type": "QueryCreated"},
        {"query_id": "q2", "event_type": "QueryCompleted"},
        {"query_id": "q2", "event_type": "QueryCompleted"},
        {"query_id": "unexpected", "event_type": "QueryCompleted"},
        {"query_id": "unexpected", "event_type": "QueryCompleted"},
    ]

    result = reconcile_query_events(expected, observed)

    assert result["expected_query_count"] == 4
    assert result["observed_expected_query_count"] == 2
    assert result["coverage_percent"] == 50.0
    assert result["missing_query_ids"] == ["q3", "q4"]
    assert result["unexpected_query_ids"] == ["unexpected"]
    assert result["duplicate_event_count"] == 2
    assert result["unexpected_event_count"] == 2
    assert result["missing_event_count"] == 4


def test_empty_expected_set_is_fully_covered():
    result = reconcile_query_events([], [])

    assert result["coverage_percent"] == 100.0
    assert result["missing_query_ids"] == []


def test_invalid_events_do_not_inflate_coverage():
    observed = [{"query_id": "q1"}, {"event_type": "QueryCreated"}]
    result = reconcile_query_events(["q1"], observed)

    assert result["coverage_percent"] == 0.0
    assert result["invalid_event_count"] == 2
