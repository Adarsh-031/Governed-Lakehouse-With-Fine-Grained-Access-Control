"""Unit tests for Trino 476 audit event normalization."""

import copy
import json
from pathlib import Path

import pytest

from audit.normalize import AuditEventError, classify_status, deduplicate_events, normalize_event

FIXTURES = Path(__file__).parents[1] / "fixtures" / "trino"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_created_event_is_running_and_sql_is_redacted():
    event = fixture("created.json")
    record = normalize_event(
        event,
        received_timestamp="2026-10-29T10:15:30.200Z",
        policy_version="clinical-v1",
        policy_digest="sha256:policy",
    )
    assert record["query_id"] == event["metadata"]["queryId"]
    assert record["event_type"] == "QueryCreated"
    assert record["authenticated_user"] == "doctor_alice"
    assert record["groups"] == ["doctor"]
    assert record["tables"] == ["hospital.silver.patients"]
    assert record["columns"] == ["patient_token"]
    assert record["status"] == "running"
    assert record["output_rows_if_available"] is None
    assert record["policy_version"] == "clinical-v1"
    assert record["policy_digest"] == "sha256:policy"
    assert "synthetic-patient-42" not in str(record)
    assert record["query_text_restricted"] == "[REDACTED]"


def test_completed_success_has_output_rows_and_metadata():
    record = normalize_event(
        fixture("completed_success.json"), received_timestamp="2026-10-29T10:16:00Z"
    )
    assert record["event_type"] == "QueryCompleted"
    assert record["status"] == "success"
    assert record["error_code"] is None
    assert record["output_rows_if_available"] == 1
    assert record["tables"] == ["hospital.silver.patients"]
    assert record["columns"] == ["patient_token"]


def test_sql_failure_does_not_leak_message_or_stack():
    record = normalize_event(
        fixture("completed_failure.json"), received_timestamp="2026-10-29T10:16:01Z"
    )
    assert record["status"] == "sql_failure"
    assert record["error_code"] == "COLUMN_NOT_FOUND"
    assert "cannot be resolved" not in str(record)
    assert "stack trace" not in str(record)


def test_authorization_denial_is_classified_separately():
    record = normalize_event(
        fixture("completed_access_denied.json"), received_timestamp="2026-10-29T10:17:01Z"
    )
    assert record["status"] == "authorization_denial"
    assert record["error_code"] == "PERMISSION_DENIED"


@pytest.mark.parametrize(
    ("query_state", "error_code", "expected"),
    [
        ("FINISHED", None, "success"),
        ("FAILED", "SYNTAX_ERROR", "sql_failure"),
        ("FAILED", "PERMISSION_DENIED", "authorization_denial"),
        ("CANCELED", None, "sql_failure"),
    ],
)
def test_terminal_status_classification(query_state, error_code, expected):
    assert classify_status(query_state, error_code) == expected


def test_exact_duplicate_events_collapse_by_query_id_and_type():
    record = normalize_event(
        fixture("completed_success.json"), received_timestamp="2026-10-29T10:16:00Z"
    )
    assert deduplicate_events([record, copy.deepcopy(record)]) == [record]


def test_created_and_completed_have_distinct_deduplication_keys():
    created = normalize_event(
        fixture("created.json"), received_timestamp="2026-10-29T10:15:30.200Z"
    )
    completed = normalize_event(
        fixture("completed_success.json"), received_timestamp="2026-10-29T10:16:00Z"
    )
    assert len(deduplicate_events([created, completed])) == 2


def test_conflicting_duplicate_fails_closed():
    first = normalize_event(
        fixture("completed_success.json"), received_timestamp="2026-10-29T10:16:00Z"
    )
    second = dict(first, output_rows_if_available=9)
    with pytest.raises(AuditEventError, match="conflicting duplicate"):
        deduplicate_events([first, second])


def test_missing_user_and_timezone_are_rejected():
    missing_user = copy.deepcopy(fixture("created.json"))
    missing_user["context"].pop("user")
    missing_user["context"].pop("originalUser")
    with pytest.raises(AuditEventError, match="context.user"):
        normalize_event(missing_user)
    invalid_time = copy.deepcopy(fixture("created.json"))
    invalid_time["createTime"] = "2026-10-29T10:15:30"
    with pytest.raises(AuditEventError, match="timezone"):
        normalize_event(invalid_time)


def test_plan_addresses_and_invalid_column_names_are_not_recorded():
    event = fixture("completed_success.json")
    event["metadata"]["tables"][0]["columns"].append({"column": "patient-name"})
    event["metadata"]["jsonPlan"] = "sensitive plan"
    event["context"]["remoteClientAddress"] = "192.0.2.25"
    record = normalize_event(event, received_timestamp="2026-10-29T10:16:00Z")
    assert record["columns"] == ["patient_token"]
    assert "sensitive plan" not in str(record)
    assert "192.0.2.25" not in str(record)


def test_invalid_event_shape_fails_closed():
    with pytest.raises(AuditEventError, match="metadata"):
        normalize_event({"context": {}})


def test_duplicate_delivery_timestamps_do_not_create_conflicts():
    record = normalize_event(
        fixture("completed_success.json"), received_timestamp="2026-10-29T10:16:00Z"
    )
    retry = dict(record, received_timestamp="2026-10-29T10:16:05Z")
    assert deduplicate_events([record, retry]) == [record]


def test_conflicting_duplicate_is_quarantined_without_blocking_batch():
    first = normalize_event(
        fixture("completed_success.json"), received_timestamp="2026-10-29T10:16:00Z"
    )
    conflict = dict(first, output_rows_if_available=9)
    other = normalize_event(fixture("created.json"), received_timestamp="2026-10-29T10:15:31Z")
    quarantine = []
    assert deduplicate_events([first, conflict, other], quarantine=quarantine) == [first, other]
    assert quarantine == [conflict]


def test_row_filters_and_masks_are_recorded_without_expressions():
    event = fixture("completed_success.json")
    table = event["metadata"]["tables"][0]
    table["filters"] = ["patient_token IN (SELECT 'secret-literal')"]
    table["columns"].append({"column": "zip", "mask": "substr(zip, 1, 3) || 'XX'"})
    record = normalize_event(event, received_timestamp="2026-10-29T10:16:00Z")
    assert record["row_filter_tables"] == ["hospital.silver.patients"]
    assert record["masked_columns"] == ["hospital.silver.patients.zip"]
    assert "secret-literal" not in str(record)
    assert "substr" not in str(record)


def test_unfiltered_unmasked_query_records_empty_enforcement():
    record = normalize_event(
        fixture("completed_success.json"), received_timestamp="2026-10-29T10:16:00Z"
    )
    assert record["row_filter_tables"] == []
    assert record["masked_columns"] == []


def test_epoch_second_timestamps_are_accepted():
    event = fixture("created.json")
    event["createTime"] = 1761733530.5
    record = normalize_event(event, received_timestamp="2026-10-29T10:16:00Z")
    assert record["event_timestamp"] == "2025-10-29T10:25:30.500000Z"
