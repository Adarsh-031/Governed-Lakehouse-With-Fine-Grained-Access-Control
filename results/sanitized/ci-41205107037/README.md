# CI evidence, run 41205107037

- Source: GitHub Actions workflow `CI`, run 41205107037, job 124548480942
  ("Code and configuration checks"), branch `feat/issue-26-results-notebook`,
  commit `3aa7c3b` (main `05b7d6a` with #46 merged, plus the summary step).
- Cohort: Synthea 3.4.0, 1,000 patients, seed 42, plus the policy boundary
  fixtures (1,009 patients in the privacy metrics).
- `cases.csv`, `audit-coverage.json`, `issue-24-*.json`: artifact
  `issue-24-privacy-metrics`, written by `scripts/results_summary.py`. Query IDs,
  observed audit rows and patient tokens are not included.
- `test-summary.csv`: pytest totals from the same job log (unit 836 passed,
  `tests/access` 585 passed, `tests/bypass` 12 passed; 0 failed).
