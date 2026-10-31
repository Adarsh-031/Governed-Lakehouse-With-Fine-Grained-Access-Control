# notebooks

`results.ipynb` is the results notebook for issue #26. It reads only committed
measurement files and writes every report figure to `results/figures/`:

| Section | Input |
|---|---|
| Policy-overhead benchmark (1,000 and 10,000 patients) | `results/raw/bench-20261030T174823Z/` |
| Access tests, bypass outcomes, audit coverage, privacy before/after #46 | `results/sanitized/ci-<run>/` |

Re-run top to bottom from the repository root:

```bash
uv run --locked --group dev jupyter nbconvert --to notebook --execute --inplace notebooks/results.ipynb
```

Never type a measurement into the notebook. To refresh the CI evidence, download
the `issue-24-privacy-metrics` artifact of a green CI run (it is produced by
`scripts/results_summary.py` and already identifier-free), copy it into a new
`results/sanitized/ci-<run>/` with a README naming the run and commit, add the
run's suite totals as `test-summary.csv`, and point `EVIDENCE` at it.
