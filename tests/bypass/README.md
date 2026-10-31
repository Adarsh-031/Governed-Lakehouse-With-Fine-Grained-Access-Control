# Bypass investigation (issue #23)

```sh
RUN_BYPASS_INTEGRATION=1 uv run --locked --group dev \
  pytest tests/bypass -v --strict-config --strict-markers
```

Docker/Compose and locked development dependencies are required. Tests skip
without the opt-in flag. CI runs the suite explicitly. Each run uses a fresh
isolated project, random credentials, CA-verified HTTPS and disposable volumes;
the existing developer stack is untouched. `ACCESS_TEST_SOURCE` selects another
manifest-backed Synthea source, as in the [access suite](../access/README.md).

`cases.csv` is the inventory, not a mutable record of the last run. Its statuses
remain `not_run`; measured statuses belong to each run's report. Collection fails
if inventory entries and implemented handlers differ. The original 11 cases
are covered, plus explicit `admin_operations` coverage from the issue's plan.

## Evidence

Pytest prints a new private `results/raw/bypass-<timestamp>-<id>/` directory.
Use `--bypass-results-dir=results/raw/<new-name>` for a chosen destination;
existing directories are refused. Each directory contains:

- `bypass-results.json`: expected outcomes, observed results, identities,
  SQL/query IDs/errors or backend requests/HTTP statuses, per-case status/reason,
  follow-up links, policy version/digest, oracle hashes and sanitized topology.
- `cases.csv`: one outcome and reason for every inventory row, including cases
  not selected with `-k`, skipped during setup, or interrupted before execution.
- `policy/` and three private oracle files: exact committed policy/configuration
  snapshots and the independent pre-write clinical ground truth.

Directories are `0700`; report and oracle files are `0600`. Reports are replaced
atomically after observations. Evidence is ignored by Git. Raw results contain
patient tokens and query literals: retain them privately. Full rendered Compose
configuration and SDK exception messages are not copied because they can contain
credentials. `--junitxml=...` provides normal pytest results.

`blocked` means the tested boundary held, including an allowed query returning
exactly the independently expected rows. `leaked` records unauthorized output,
a demonstrated residual inference/linkage disclosure, or a measured audit-trail
gap. `inconclusive` means an unexpected error, incomplete attempt, or missing
dependency prevents a verdict.
An unexecuted case stays `not_run` with a reason; it cannot become a pass.

Unexpected SQL success or incorrect row results fail pytest. Known residual-risk
measurements can pass pytest while their evidence status remains `leaked` and
links to [#46](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/46).
A green run therefore means the investigation executed as specified; it does
not mean every attempted disclosure was blocked. Create a follow-up issue for
any new leaked case and retain its link with that run's private evidence.

## Issue #24: researcher privacy risk

The two researcher-risk cases now report minimum equivalence-class size (`k_min`),
singleton-record percentage, small-cell counts, exact count differencing, and
auxiliary-data linkage with and without rare-condition signatures. The signature
uses researcher-visible `encounters.reason_code` values whose distinct-patient
prevalence is no more than `max(1, ceil(N * 0.05))`. This is an attack-model
feature, not a masking or suppression control.

For a 1,000-patient Synthea source run, generate the pinned seeded data first:

```sh
./scripts/generate_synthea.sh --patients 1000 --seed 42
ACCESS_TEST_SOURCE="$PWD/data/raw/synthea/1000-seed42" \
  RUN_BYPASS_INTEGRATION=1 uv run --locked --group dev \
  pytest tests/bypass -v --strict-config --strict-markers
```

CI performs this generation and runs the complete bypass suite on the 1,000-patient
source. The fixture adds boundary patients, so use the population count recorded
in the run rather than assuming the final denominator is exactly 1,000. CI uploads
only aggregate-only `issue-24-*.json` summaries for review; the full private
report and row-level evidence remain unshared.

The stable `cases.csv` is still an inventory with `not_run` statuses. The
generated per-run `cases.csv` holds measured statuses, reasons and evidence.
See the [issue #24 methodology and prior baseline](../../docs/issue-24.md).

## Coverage and limits

Backend probes bypass network isolation deliberately. An authenticated read of
the exact object/catalog table is the positive control; anonymous/invalid-token
requests and the authenticated audit-writer service must still be rejected.
SQL denials require `PERMISSION_DENIED`, rather than accepting missing resources,
syntax errors or transport failures. Committed policy bytes are never widened.

Doctor memberships and masked values come from the existing independent access
oracles. Historical checks use source-only snapshots saved **before** adversarial
rows are appended, then prove these differ from current snapshot IDs. They check
both row filtering and researcher masks/hidden fields. CTEs, subqueries and joins
execute; persistent view creation is denied by the SELECT-only policy.

Inference uses exact aggregate counts and single-patient differencing against
saved synthetic ground truth. Linkage uses age band, sex and masked ZIP as
features; tokens only score correctness. Ambiguous keys and incomplete QIs do
not establish identity. Rates use the whole population as denominator and report
complete auxiliary rows and match precision separately. These toy-population
measurements do not estimate real-world identification probability.

The `audit_collector_outage` case now exercises the real Trino listener, stops and
restarts the collector during a known query workload, and reconciles query IDs against
the audit table. It records coverage, missing lifecycle events, duplicates and outage
boundaries in the private run directory. A missing event is retained as a measured audit
gap; no fixed recovery percentage is assumed. See [issue #22](../../docs/issue-22.md).

Unit regressions cover inventory completeness, private persistence, interrupted
attempts, authorization versus infrastructure errors, and linkage scoring:
`uv run --locked --group dev pytest tests/unit/test_bypass.py -v`.

See the [bypass report and decisions](../../docs/bypass-report.md).
