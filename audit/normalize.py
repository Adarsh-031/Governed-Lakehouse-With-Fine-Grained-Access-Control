"""Normalize Trino 476 query lifecycle events into the restricted audit contract."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
_AUTHORIZATION_CODES = {
    "PERMISSION_DENIED",
    "ACCESS_DENIED",
    "NOT_AUTHORIZED",
    "AUTHORIZATION_DENIED",
}


class AuditEventError(ValueError):
    """Raised when a Trino event cannot be safely normalized."""


def _mapping(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise AuditEventError(f"{field} must be an object")
    return value


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AuditEventError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _iso_timestamp(value: Any, field: str) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Jackson writes java.time.Instant as decimal epoch seconds when
        # WRITE_DATES_AS_TIMESTAMPS is enabled; accept that form too.
        parsed = datetime.fromtimestamp(value, UTC)
        return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")
    text = _required_text(value, field)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AuditEventError(f"{field} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise AuditEventError(f"{field} must include a timezone")
    return parsed.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _groups(context: Mapping[str, Any]) -> list[str]:
    value = context.get("groups", [])
    if value is None:
        return []
    if not isinstance(value, (list, tuple, set)):
        raise AuditEventError("context.groups must be a collection")
    return sorted({_required_text(item, "context.groups[]") for item in value})


def _table_names(metadata: Mapping[str, Any], io_metadata: Mapping[str, Any]) -> list[str]:
    """Read the Trino 476 TableInfo and QueryInputMetadata resource shapes."""
    names: set[str] = set()
    declared = metadata.get("tables", [])
    if isinstance(declared, list):
        for entry in declared:
            if not isinstance(entry, Mapping):
                continue
            parts = [_optional_text(entry.get(key)) for key in ("catalog", "schema", "table")]
            parts = [part for part in parts if part]
            if parts and all(_IDENTIFIER.fullmatch(part) for part in parts):
                names.add(".".join(parts))
    inputs = io_metadata.get("inputs", [])
    if isinstance(inputs, list):
        for entry in inputs:
            if not isinstance(entry, Mapping):
                continue
            parts = [_optional_text(entry.get(key)) for key in ("catalogName", "schema", "table")]
            parts = [part for part in parts if part]
            if parts and all(_IDENTIFIER.fullmatch(part) for part in parts):
                names.add(".".join(parts))
    return sorted(names)


def _column_names(metadata: Mapping[str, Any], io_metadata: Mapping[str, Any]) -> list[str]:
    """Collect names from Trino 476 ColumnInfo and IO Column/OutputColumnMetadata."""
    names: set[str] = set()
    declared = metadata.get("tables", [])
    if isinstance(declared, list):
        for entry in declared:
            if not isinstance(entry, Mapping):
                continue
            columns = entry.get("columns", [])
            if isinstance(columns, list):
                for column in columns:
                    if isinstance(column, Mapping):
                        name = _optional_text(column.get("column"))
                        if name and _IDENTIFIER.fullmatch(name):
                            names.add(name)
    inputs = io_metadata.get("inputs", [])
    if isinstance(inputs, list):
        for entry in inputs:
            if not isinstance(entry, Mapping):
                continue
            columns = entry.get("columns", [])
            if isinstance(columns, list):
                for column in columns:
                    name = column.get("name") if isinstance(column, Mapping) else column
                    name = _optional_text(name)
                    if name and _IDENTIFIER.fullmatch(name):
                        names.add(name)
    output = io_metadata.get("output")
    if isinstance(output, Mapping):
        columns = output.get("columns", [])
        if isinstance(columns, list):
            for column in columns:
                name = column.get("columnName") if isinstance(column, Mapping) else column
                name = _optional_text(name)
                if name and _IDENTIFIER.fullmatch(name):
                    names.add(name)
    return sorted(names)


def _policy_enforcement(metadata: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    """Return tables with a row filter and table.column names with a mask.

    Trino 476 TableInfo.filters and ColumnInfo.mask carry the policy expressions,
    which may contain literals, so only their presence is recorded.
    """
    filtered: set[str] = set()
    masked: set[str] = set()
    declared = metadata.get("tables", [])
    if not isinstance(declared, list):
        return [], []
    for entry in declared:
        if not isinstance(entry, Mapping):
            continue
        parts = [_optional_text(entry.get(key)) for key in ("catalog", "schema", "table")]
        if not all(part and _IDENTIFIER.fullmatch(part) for part in parts):
            continue
        table = ".".join(parts)  # type: ignore[arg-type]
        filters = entry.get("filters")
        if isinstance(filters, list) and any(_optional_text(item) for item in filters):
            filtered.add(table)
        columns = entry.get("columns", [])
        if isinstance(columns, list):
            for column in columns:
                if not isinstance(column, Mapping):
                    continue
                name = _optional_text(column.get("column"))
                if name and _IDENTIFIER.fullmatch(name) and _optional_text(column.get("mask")):
                    masked.add(f"{table}.{name}")
    return sorted(filtered), sorted(masked)


def _restricted_query_text(query: Any) -> str | None:
    """Discard SQL completely; even deterministic hashes can leak guessable literals."""
    return "[REDACTED]" if isinstance(query, str) and query else None


def classify_status(query_state: Any, error_code: Any = None) -> str:
    """Map Trino query state and error code to the normalized status."""
    if isinstance(error_code, Mapping):
        code = str(error_code.get("name") or error_code.get("code") or "").upper()
    else:
        code = str(error_code or "").upper()
    if code in _AUTHORIZATION_CODES or any(
        marker in code for marker in ("PERMISSION_DENIED", "ACCESS_DENIED", "NOT_AUTHORIZED")
    ):
        return "authorization_denial"
    state = str(query_state or "").upper()
    if state == "FINISHED" and not code:
        return "success"
    if state in {"FAILED", "CANCELED", "CANCELLED"} or code:
        return "sql_failure"
    raise AuditEventError(f"unsupported terminal query state: {query_state!r}")


def normalize_event(
    event: Mapping[str, Any],
    *,
    received_timestamp: str | None = None,
    policy_version: str | None = None,
    policy_digest: str | None = None,
) -> dict[str, Any]:
    """Normalize a Trino 476 QueryCreatedEvent or QueryCompletedEvent.

    Raw SQL, failure messages, stack traces, plans, client addresses and arbitrary
    session properties are excluded. SQL is represented only by the constant
    ``[REDACTED]`` marker. Row-filter and mask expressions are never stored; only
    which tables had a row filter and which columns were masked. Policy metadata is caller-supplied because native Trino events
    do not contain the deployed policy version or digest.
    """
    root = _mapping(event, "event")
    metadata = _mapping(root.get("metadata"), "metadata")
    context = _mapping(root.get("context"), "context")
    event_type = (
        "QueryCompleted"
        if any(key in root for key in ("statistics", "failureInfo", "endTime"))
        else "QueryCreated"
    )
    query_id = _required_text(metadata.get("queryId"), "metadata.queryId")
    user = _required_text(context.get("user") or context.get("originalUser"), "context.user")
    restricted_query = _restricted_query_text(metadata.get("query"))
    io_metadata = _mapping(root.get("ioMetadata") or {}, "ioMetadata")
    row_filter_tables, masked_columns = _policy_enforcement(metadata)

    if event_type == "QueryCreated":
        event_time = _iso_timestamp(root.get("createTime"), "createTime")
        status, error_code, output_rows = "running", None, None
    else:
        event_time = _iso_timestamp(root.get("endTime") or root.get("createTime"), "endTime")
        failure = root.get("failureInfo")
        error_code = None
        if isinstance(failure, Mapping):
            failure_code = failure.get("errorCode")
            code_value = (
                failure_code.get("name") if isinstance(failure_code, Mapping) else failure_code
            )
            error_code = _optional_text(code_value)
        status = classify_status(metadata.get("queryState"), error_code)
        statistics = _mapping(root.get("statistics") or {}, "statistics")
        value = statistics.get("outputRows")
        output_rows = (
            value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
        )

    received = received_timestamp or datetime.now(UTC).isoformat(timespec="microseconds")
    return {
        "query_id": query_id,
        "event_type": event_type,
        "authenticated_user": user,
        "groups": _groups(context),
        "query_text_restricted": restricted_query,
        "tables": _table_names(metadata, io_metadata),
        "columns": _column_names(metadata, io_metadata),
        "row_filter_tables": row_filter_tables,
        "masked_columns": masked_columns,
        "output_rows_if_available": output_rows,
        "status": status,
        "error_code": error_code,
        "event_timestamp": event_time,
        "received_timestamp": _iso_timestamp(received, "received_timestamp"),
        "policy_version": _optional_text(policy_version),
        "policy_digest": _optional_text(policy_digest),
    }


def deduplicate_events(
    events: Iterable[Mapping[str, Any]],
    *,
    quarantine: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Collapse exact duplicates on (query_id, event_type), preserving order.

    A retried delivery that differs only in ``received_timestamp`` is dropped.
    A duplicate with different content is never silently discarded: when a
    ``quarantine`` list is given it is appended there (so one bad record cannot
    block a whole batch) and the first copy is kept; otherwise it raises.
    """
    result: list[dict[str, Any]] = []
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        record = dict(event)
        key = (
            _required_text(record.get("query_id"), "query_id"),
            _required_text(record.get("event_type"), "event_type"),
        )
        previous = seen.get(key)
        if previous is None:
            seen[key] = record
            result.append(record)
        elif _without_receipt(previous) != _without_receipt(record):
            if quarantine is None:
                raise AuditEventError(
                    f"conflicting duplicate for query_id={key[0]!r}, event_type={key[1]!r}"
                )
            quarantine.append(record)
    return result


def _without_receipt(record: Mapping[str, Any]) -> dict[str, Any]:
    return {name: value for name, value in record.items() if name != "received_timestamp"}
