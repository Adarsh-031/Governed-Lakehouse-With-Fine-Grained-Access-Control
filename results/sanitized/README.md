# results/sanitized

Reviewed, shareable result summaries. Nothing here contains patient tokens,
query IDs or SQL literals.

- `ci-<run>/`: identifier-free output of `scripts/results_summary.py` from a green
  CI run (bypass case outcomes, audit coverage counts, issue #24/#46 privacy
  metrics) plus that run's test-suite totals. Read by `notebooks/results.ipynb`.
