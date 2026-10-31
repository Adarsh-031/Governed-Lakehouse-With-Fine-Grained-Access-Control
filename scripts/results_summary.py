"""Write a shareable summary of a private bypass run (issue #26).

The bypass suite keeps per-case evidence private because it can contain patient
tokens, SQL literals and query IDs. This script copies only what the results
notebook needs and nothing identifying:

* ``cases.csv``: case, expected, status, reason (fixed strings from the suite).
* ``audit-coverage.json``: counts from ``audit_collector_outage.json`` with
  query IDs and observed event rows removed.
* ``issue-24-*.json``: the aggregate-only privacy metrics, copied unchanged.

Usage: ``python scripts/results_summary.py results/raw/bypass-<run> OUT_DIR``.
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

CASE_FIELDS = ["case", "expected", "status", "reason"]
COVERAGE_FIELDS = [
    "expected_query_count",
    "observed_expected_query_count",
    "coverage_percent",
    "missing_event_count",
    "duplicate_event_count",
    "unexpected_event_count",
    "invalid_event_count",
    "expected_event_types",
    "outage_started_utc",
    "outage_ended_utc",
    "measured_at_utc",
]


def summarize(run: Path, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    written = []

    with (run / "cases.csv").open(newline="") as f:
        rows = [{key: row.get(key, "") for key in CASE_FIELDS} for row in csv.DictReader(f)]
    cases = out / "cases.csv"
    with cases.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CASE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    written.append(cases)

    outage = run / "audit_collector_outage.json"
    if outage.exists():
        evidence = json.loads(outage.read_text())
        summary = {key: evidence[key] for key in COVERAGE_FIELDS if key in evidence}
        summary["missing_query_count"] = len(evidence.get("missing_query_ids", []))
        path = out / "audit-coverage.json"
        path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        written.append(path)

    for metrics in sorted(run.glob("issue-24-*.json")):
        target = out / metrics.name
        shutil.copyfile(metrics, target)
        written.append(target)

    for path in written:
        path.chmod(0o644)
    return written


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    runs = sorted(Path().glob(argv[1])) if "*" in argv[1] else [Path(argv[1])]
    if not runs:
        # An earlier step failed before the bypass suite ran; that step already fails
        # the job, so do not add a second, misleading failure here.
        print(f"No bypass run matches {argv[1]}; nothing to summarize.")
        return 0
    for path in summarize(runs[-1], Path(argv[2])):
        print(f"== {path.name}")
        print(path.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
