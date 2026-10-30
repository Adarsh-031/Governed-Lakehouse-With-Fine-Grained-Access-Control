"""Batch writer: restricted spool -> normalized, deduplicated Iceberg audit table (#21).

The collector (#20) durably writes one JSON file per Trino event into the spool.
Each pass of this writer:

1. reads up to ``batch_size`` spool files, oldest first (names are time ordered);
2. normalizes each event with :mod:`audit.normalize` (raw SQL is never stored);
3. moves unreadable, invalid, or conflicting records to ``<spool>/quarantine``;
4. drops duplicates of ``(query_id, event_type)`` within the batch and against
   rows already in the table (Trino retries and replays are at-least-once);
5. appends the new rows in one Iceberg commit;
6. only then deletes the spool files it has accounted for.

A crash between steps 5 and 6 leaves files that the next pass recognises as
already written, so a replay never duplicates a row. The writer talks to the
catalog through PyIceberg as the ``audit-writer`` service and never sends SQL to
Trino, so its own writes cannot generate further audit events. Run exactly one
writer per spool.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import pyarrow as pa

from audit.normalize import AuditEventError, deduplicate_events, normalize_event

LOGGER = logging.getLogger("audit.writer")

NAMESPACE = "audit"
TABLE = "query_events"
SPOOL_SUFFIX = ".json"
QUARANTINE_DIR = "quarantine"
DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / "policies" / "version.json"

# Mirrors policies/schema-contract.yaml (audit) and audit/event-contract.json;
# tests/unit/test_audit_writer.py fails if they drift apart.
ARROW_SCHEMA = pa.schema(
    [
        pa.field("query_id", pa.large_string(), nullable=False),
        pa.field("event_type", pa.large_string(), nullable=False),
        pa.field("authenticated_user", pa.large_string(), nullable=False),
        pa.field("groups", pa.list_(pa.large_string())),
        pa.field("query_text_restricted", pa.large_string()),
        pa.field("tables", pa.list_(pa.large_string())),
        pa.field("columns", pa.list_(pa.large_string())),
        pa.field("row_filter_tables", pa.list_(pa.large_string())),
        pa.field("masked_columns", pa.list_(pa.large_string())),
        pa.field("output_rows_if_available", pa.int64()),
        pa.field("status", pa.large_string(), nullable=False),
        pa.field("error_code", pa.large_string()),
        pa.field("event_timestamp", pa.timestamp("us"), nullable=False),
        pa.field("received_timestamp", pa.timestamp("us"), nullable=False),
        pa.field("policy_version", pa.large_string()),
        pa.field("policy_digest", pa.large_string()),
    ]
)
TIMESTAMP_FIELDS = ("event_timestamp", "received_timestamp")


class AuditSink(Protocol):
    """Where normalized rows go; the Iceberg table in production, a fake in tests."""

    def existing_keys(self, query_ids: Sequence[str]) -> set[tuple[str, str]]: ...

    def append(self, rows: list[dict[str, Any]]) -> None: ...


@dataclass
class PassResult:
    written: int = 0
    duplicates: int = 0
    quarantined: int = 0
    files: int = 0
    reasons: dict[str, int] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "written": self.written,
            "duplicates": self.duplicates,
            "quarantined": self.quarantined,
            "quarantine_reasons": dict(sorted(self.reasons.items())),
        }


def load_policy(manifest: Path = DEFAULT_MANIFEST) -> tuple[str | None, str | None]:
    """Policy version/digest recorded with each row (not present in Trino events)."""
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    return data.get("policy_version"), data.get("policy_digest")


def _fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _quarantine(spool: Path, path: Path, reason: str, result: PassResult) -> None:
    target_dir = spool / QUARANTINE_DIR
    target_dir.mkdir(mode=0o700, exist_ok=True)
    # Keep the original bytes for review; a sidecar names the reason, never content.
    os.replace(path, target_dir / path.name)
    reason_path = target_dir / f"{path.stem}.reason"
    fd = os.open(reason_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, f"{reason}\n".encode())
        os.fsync(fd)
    finally:
        os.close(fd)
    _fsync_directory(target_dir)
    result.quarantined += 1
    result.reasons[reason] = result.reasons.get(reason, 0) + 1


def spool_files(spool: Path, limit: int) -> list[Path]:
    """Committed spool records only; the collector's in-flight temp files are skipped."""
    files = sorted(
        path for path in spool.iterdir() if path.is_file() and path.name.endswith(SPOOL_SUFFIX)
    )
    return files[:limit]


def _naive_utc(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC).replace(tzinfo=None)


def to_row(record: dict[str, Any]) -> dict[str, Any]:
    row = {name: record.get(name) for name in ARROW_SCHEMA.names}
    for name in TIMESTAMP_FIELDS:
        row[name] = _naive_utc(row[name])
    return row


def _read_spool_record(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("event"), dict):
        raise AuditEventError("spool record has no event object")
    return data


def process_once(
    spool: Path,
    sink: AuditSink,
    *,
    batch_size: int = 500,
    policy: tuple[str | None, str | None] = (None, None),
) -> PassResult:
    """Run one writer pass; raise (leaving the spool untouched) if the append fails."""
    result = PassResult()
    files = spool_files(spool, batch_size)
    result.files = len(files)
    accepted: dict[tuple[str, str], tuple[Path, dict[str, Any]]] = {}
    duplicates: list[Path] = []
    for path in files:
        try:
            raw = _read_spool_record(path)
            record = normalize_event(
                raw["event"],
                received_timestamp=raw.get("received_timestamp"),
                policy_version=policy[0],
                policy_digest=policy[1],
            )
        except (UnicodeDecodeError, json.JSONDecodeError):
            _quarantine(spool, path, "unreadable_json", result)
            continue
        except AuditEventError:
            _quarantine(spool, path, "invalid_event", result)
            continue
        key = (record["query_id"], record["event_type"])
        if key not in accepted:
            accepted[key] = (path, record)
            continue
        try:
            deduplicate_events([accepted[key][1], record])
        except AuditEventError:
            _quarantine(spool, path, "conflicting_duplicate", result)
            continue
        duplicates.append(path)

    existing = sink.existing_keys(sorted({key[0] for key in accepted}))
    rows: list[dict[str, Any]] = []
    written: list[Path] = []
    for key, (path, record) in accepted.items():
        if key in existing:
            duplicates.append(path)
        else:
            rows.append(to_row(record))
            written.append(path)
    if rows:
        sink.append(rows)
    for path in [*written, *duplicates]:
        path.unlink(missing_ok=True)
    if written or duplicates:
        _fsync_directory(spool)
    result.written = len(written)
    result.duplicates = len(duplicates)
    return result


class IcebergSink:
    """The restricted ``audit.query_events`` table in the ``audit`` warehouse."""

    def __init__(self, catalog: Any) -> None:
        from pyiceberg.exceptions import NamespaceAlreadyExistsError

        try:
            catalog.create_namespace(NAMESPACE)
        except NamespaceAlreadyExistsError:
            pass
        self.table = catalog.create_table_if_not_exists((NAMESPACE, TABLE), schema=ARROW_SCHEMA)
        if not self.table.location().startswith("s3://warehouse/audit/"):
            raise RuntimeError("Audit table is outside the restricted audit/ prefix")

    def existing_keys(self, query_ids: Sequence[str]) -> set[tuple[str, str]]:
        if not query_ids:
            return set()
        from pyiceberg.expressions import In

        self.table.refresh()
        found = self.table.scan(
            row_filter=In("query_id", set(query_ids)),
            selected_fields=("query_id", "event_type"),
        ).to_arrow()
        return set(zip(found["query_id"].to_pylist(), found["event_type"].to_pylist(), strict=True))

    def append(self, rows: list[dict[str, Any]]) -> None:
        self.table.append(pa.Table.from_pylist(rows, schema=ARROW_SCHEMA))

    def summary(self, query_ids: Iterable[str]) -> list[dict[str, Any]]:
        from pyiceberg.expressions import In

        self.table.refresh()
        fields = ("query_id", "event_type", "authenticated_user", "status", "error_code")
        found = self.table.scan(
            row_filter=In("query_id", set(query_ids)), selected_fields=fields
        ).to_arrow()
        return sorted(found.to_pylist(), key=lambda row: (row["query_id"], row["event_type"]))


def audit_catalog() -> Any:
    from pyiceberg.catalog import load_catalog

    return load_catalog(
        "audit",
        **{
            "type": "rest",
            "uri": "http://catalog:8181/catalog",
            "warehouse": "audit",
            "credential": f"audit-writer:{os.environ['AUDIT_WRITER_OIDC_CLIENT_SECRET']}",
            "oauth2-server-uri": (
                "http://keycloak:8080/realms/lakehouse/protocol/openid-connect/token"
            ),
            "scope": "openid",
            "s3.endpoint": "http://storage:9000",
            "s3.region": "us-east-1",
            "s3.access-key-id": os.environ["AUDIT_WRITER_STORAGE_ACCESS_KEY"],
            "s3.secret-access-key": os.environ["AUDIT_WRITER_STORAGE_SECRET_KEY"],
            "s3.force-virtual-addressing": "false",
        },
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--loop", action="store_true", help="run passes until stopped")
    mode.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode.add_argument(
        "--summary",
        metavar="QUERY_ID",
        nargs="+",
        help="print stored rows (no SQL) for these query IDs and exit",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    spool = Path(os.environ.get("AUDIT_SPOOL_DIR", "data/audit-spool"))
    batch_size = int(os.environ.get("AUDIT_WRITER_BATCH_SIZE", "500"))
    interval = float(os.environ.get("AUDIT_WRITER_INTERVAL_SECONDS", "5"))
    policy = load_policy(Path(os.environ.get("AUDIT_POLICY_MANIFEST", DEFAULT_MANIFEST)))
    try:
        sink = IcebergSink(audit_catalog())
        if args.summary:
            print(json.dumps(sink.summary(args.summary), sort_keys=True))
            return 0
        while True:
            try:
                result = process_once(spool, sink, batch_size=batch_size, policy=policy)
            except Exception as error:  # spool is kept; retry next pass
                if not args.loop:
                    raise
                # SDK errors can echo request details; log the type only.
                LOGGER.warning("audit pass failed (%s); spool kept", type(error).__name__)
                time.sleep(interval)
                continue
            # Counts only: event contents and user names stay out of service logs.
            print(json.dumps(result.as_dict(), sort_keys=True), flush=True)
            if not args.loop:
                return 0
            if result.files < batch_size:
                time.sleep(interval)
    except KeyboardInterrupt:
        return 0
    except Exception as error:  # noqa: BLE001 - never expose SDK/credential text
        print(f"Audit writer failed: {type(error).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
