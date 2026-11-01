"""Export a small, shareable sample of the audit table (issue #28 evidence).

Reads ``audit.audit.query_events`` as ``admin`` over TLS and writes the newest
rows to ``results/sanitized/audit-sample/audit-sample.csv``. The audit writer
already stores no SQL text, literals or error messages, and the policy hides even
the redacted ``query_text_restricted`` column from admin; this export additionally
replaces Trino query IDs with sequence labels (q001, q002, ...) so the sample
cannot be matched against server logs. Usage::

    uv run --locked python scripts/export_audit_sample.py --limit 40
"""

from __future__ import annotations

import argparse
import csv
from collections.abc import Iterable, Sequence
from pathlib import Path

COLUMNS = (
    "query_id",
    "event_type",
    "authenticated_user",
    "status",
    "error_code",
    "tables",
    "columns",
    "row_filter_tables",
    "masked_columns",
    "output_rows_if_available",
    "event_timestamp",
    "policy_version",
)
DEFAULT_OUTPUT = Path("results/sanitized/audit-sample/audit-sample.csv")


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, list | tuple):
        return ";".join(str(item) for item in value)
    return str(value)


def sanitize(rows: Iterable[Sequence[object]]) -> list[list[str]]:
    """Replace query IDs with stable sequence labels; keep everything else."""
    labels: dict[object, str] = {}
    cleaned = []
    for row in rows:
        query_id = row[0]
        if query_id not in labels:
            labels[query_id] = f"q{len(labels) + 1:03d}"
        cleaned.append([labels[query_id], *(_cell(value) for value in row[1:])])
    return cleaned


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--env", type=Path, default=Path("data/secrets/runtime.env"))
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    import trino
    from trino.auth import BasicAuthentication

    env = read_env(args.env)
    connection = trino.dbapi.connect(
        host=env.get("TRINO_HOST", "localhost"),
        port=int(env.get("TRINO_PORT", "8443")),
        user="admin",
        auth=BasicAuthentication("admin", env["TRINO_PASSWORD_ADMIN"]),
        http_scheme="https",
        verify=env.get("TRINO_CA_BUNDLE", "data/secrets/ca.crt"),
        catalog="audit",
        schema="audit",
    )
    try:
        cursor = connection.cursor()
        cursor.execute(
            f"SELECT {', '.join(COLUMNS)} FROM audit.audit.query_events "
            f"ORDER BY event_timestamp DESC LIMIT {int(args.limit)}"
        )
        rows = cursor.fetchall()
    finally:
        connection.close()

    rows.reverse()  # oldest first reads naturally
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(COLUMNS)
        writer.writerows(sanitize(rows))
    print(f"wrote {len(rows)} rows to {args.output}")


if __name__ == "__main__":
    main()
