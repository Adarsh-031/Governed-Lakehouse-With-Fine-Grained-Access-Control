# Policy-overhead benchmark run 20261030T174823Z

- Source: GitHub Actions run 41099548409 (job 124214012340), workflow `Policy overhead benchmark`, PR #55 merge commit `1e5689a`.
- Runner: instabox-medium, 4 vCPU AMD EPYC, ~7.8 GiB RAM, Docker 28.0.4.
- Data: Synthea 3.4.0, seed 42, 1,000 and 10,000 patients. Both scales ran; none recorded `not_run`.
- Method: enforced role query vs admin baseline with the same `rules.json` filter/masks inlined; 1 cold run after Trino restart + 10 warm runs per query.
- Verification: all 12 queries at both scales returned identical row counts and `checksum(ROW(...))` fingerprints, with 0 unauthorized rows.

| Scale | Warm median overhead (12 queries) | Warm range | Cold median | Cold range |
|---|---|---|---|---|
| 1,000 | -2.1% | -23.3% to +31.7% | +9.4% | -33.3% to +28.6% |
| 10,000 | +1.2% | -11.1% to +10.1% | -4.5% | -36.5% to +17.2% |

Interpretation: with 10 warm repetitions on a shared CI runner, enforced and baseline timings are within run-to-run noise; no consistent policy overhead is measurable at these scales. Cold numbers are single runs and noisier.

These files were recovered from the job log (the workflow prints each CSV/JSON). The uploaded artifact `benchmark-results` (17 files, 30-day retention) also holds the frozen policy evidence from `scripts/policy.py --experiment-dir`.
