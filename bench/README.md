# bench

Policy-overhead benchmark (issue #25), following [plan.yaml](plan.yaml).

## What it measures

For every role workload in [`sql/queries/`](../sql/queries/) (doctor D1-D4,
researcher R1-R4, billing B1-B4) the runner compares two queries that must
return the same rows:

| Variant | Runs as | How the policy is applied |
|---|---|---|
| `enforced` | the role (`doctor_alice`, `researcher`, `billing`) | Trino injects the `rules.json` row filter and column masks |
| `baseline` | `admin` (no filters or masks on silver) | the same SQL with each table replaced by a subquery that applies the role's filter and masks by hand ([workload.py](workload.py)) |

So the difference is the cost of Trino's policy machinery, not of returning
different data. Before any timing, each pair is checked with an order-insensitive
`count(*)` + `checksum(ROW(...))` over the full result (trailing `LIMIT`
removed). A pair that does not match is recorded in `verification-<scale>.csv`
and is not timed; the run then exits non-zero.

Each scale runs in its own isolated Compose project with throw-away credentials
(the same helper as the integration tests), so the unrestricted baseline never
runs against a developer stack.

- **Cold**: first execution of each query after restarting the Trino container,
  once per variant. The Silo/OS page cache is not dropped and no Trino file cache
  is configured, so cold vs warm mostly reflects JVM, plan and metadata warm-up.
- **Warm**: `repetitions` (10) runs per query and variant, alternating which
  variant goes first.
- Metrics per run: client wall time and Trino's `elapsedTimeMillis`,
  `cpuTimeMillis`, `peakMemoryBytes`, processed rows/bytes, result rows. Summaries
  report median and nearest-rank p95 (with 10 runs p95 is the slowest run).

## Run

```sh
./scripts/generate_synthea.sh --patients 1000 --seed 42
./scripts/generate_synthea.sh --patients 10000 --seed 42
uv run --locked --group dev python -m bench.run --scales 1000 10000
```

Docker, Java 17+ (for Synthea) and about 10 minutes per 10k scale are needed.
Set `LAKEHOUSE_COMPOSE_TIMEOUT` (seconds, default 360) for large ingests.
In GitHub, adding the `run-benchmark` label to a pull request runs
[benchmark.yml](../.github/workflows/benchmark.yml) and uploads the run directory
as an artifact.

## Output: `results/raw/bench-<UTC timestamp>/`

| File | Contents |
|---|---|
| `environment.json` | hardware, Docker, image list, Trino version, git commit, seed, Synthea manifest parameters and hashes, ingest time, per-scale status (`run`, `not_run` with reason, `failed`) |
| `policy/` | frozen `rules.json`, policy manifest and digest (`scripts/policy.py --experiment-dir`) |
| `storage-<scale>.csv` | table rows, stored bytes, data files and snapshot ID per silver table |
| `verification-<scale>.csv` | enforced vs baseline row counts, fingerprint match, `unauthorized_rows` |
| `raw-<scale>.csv` | every timed execution |
| `summary-<scale>.csv` | median / p95 / CPU / memory per query, variant and phase, and enforced overhead vs baseline |

Scales without a generated sample are written as `not_run`, never estimated.
The files contain timings, counts and hashes only, no patient rows.

## Limits

Single-node Trino and storage on one machine: results do not demonstrate
distributed scaling. CI runners are shared, so absolute times vary between runs;
compare enforced vs baseline within one run.
