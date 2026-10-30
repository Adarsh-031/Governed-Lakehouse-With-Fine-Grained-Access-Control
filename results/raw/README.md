# results/raw

Measured benchmark CSV/JSON and test evidence, excluded from Git by default.

Before each SQL access/performance experiment, run
`uv run --locked python scripts/policy.py --experiment-dir results/raw/<run>/policy`.
Retain that directory's policy manifest, exact rules and configuration snapshots
with the run's results. Do not reuse a run directory for changed policies or
edit/reload policy during measurements. See [policy evidence](../../policies/IMPLEMENTATION.md).

The [bypass suite](../../tests/bypass/README.md) creates a private
`bypass-<timestamp>-<id>/` directory with per-case JSON/CSV, query IDs/errors,
policy/configuration snapshots and independent oracles. It copies the committed,
digest-verified policy rather than recompiling it during an experiment. Reports
retain `not_run` for unexecuted cases and explicit reasons for incomplete work.
Raw files can contain patient tokens and SQL literals; keep them private and
use the aggregate [bypass report](../../docs/bypass-report.md) for discussion.

## Audit reliability evidence

The `audit_collector_outage` bypass case writes
`audit_collector_outage.json` inside its private
`bypass-<timestamp>-<id>/` run directory. It contains issued query IDs, observed
audit rows, measured coverage, missing/duplicate counts and UTC outage boundaries.
The stable inventory remains unchanged after execution; actual outcomes belong to
the per-run evidence. Keep this evidence private because query identifiers reveal
workload metadata.
