# results/raw

Measured benchmark CSV/JSON and test evidence, excluded from Git by default.

Before each SQL access/performance experiment, run
`uv run --locked python scripts/policy.py --experiment-dir results/raw/<run>/policy`.
Retain that directory's policy manifest, exact rules and configuration snapshots
with the run's results. Do not reuse a run directory for changed policies or
edit/reload policy during measurements. See [policy evidence](../../policies/IMPLEMENTATION.md).
