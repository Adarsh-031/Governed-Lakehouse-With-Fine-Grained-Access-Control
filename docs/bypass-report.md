# Bypass test report

**Result: all 12 bypass cases blocked** on the final system (CI run
[41205107037](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/actions/runs/41205107037),
1,000-patient Synthea cohort, seed 42, plus policy boundary fixtures: 1,009
synthetic patients). Two cases leaked in the first investigation; both were
fixed by [#46](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/46)
and re-measured. The audit outage case, inconclusive at first, now measures
100% coverage.

Evidence committed for review (aggregate-only, no patient tokens or query IDs):
[`results/sanitized/ci-41205107037/`](../results/sanitized/ci-41205107037/).
Figures: [`results/figures/access-and-bypass.png`](../results/figures/access-and-bypass.png),
[`privacy-linkage.png`](../results/figures/privacy-linkage.png),
[`audit-coverage.png`](../results/figures/audit-coverage.png).
Notebook: [`notebooks/results.ipynb`](../notebooks/results.ipynb).

## Threat model in one line

An authenticated SQL user (doctor, researcher, billing, read-only admin), an
unauthenticated network client, or a curious analyst with auxiliary data tries
to see rows, columns or identities outside their role. Out of scope: host root,
stolen service credentials, malicious administrators (see
[threat model](threat-model.md)).

## Results by case

| # | Case | Attack attempted | Expected | Before (Oct 29, 11 patients) | Final (1,009 patients) |
|---|------|------------------|----------|------------------------------|------------------------|
| 1 | `anonymous_object_read` | Read/list Iceberg data and metadata objects on storage without credentials | blocked | blocked | **blocked** |
| 2 | `catalog_direct_access` | Call the REST catalog directly, skipping Trino | blocked | blocked | **blocked** |
| 3 | `doctor_other_patient` | Doctor queries patients outside their care team | zero unauthorized rows | blocked | **blocked** |
| 4 | `identity_impersonation` | Log in as another user / set session user | blocked | blocked | **blocked** |
| 5 | `admin_operations` | INSERT, DDL, GRANT, session changes, procedures as every SQL user | blocked | blocked | **blocked** |
| 6 | `bronze_query` | Query raw / restricted layers as an ordinary user | blocked | blocked | **blocked** |
| 7 | `old_snapshot_query` | Time-travel to an older Iceberg snapshot to dodge filters | no unauthorized identifiers | blocked | **blocked** |
| 8 | `join_or_view_bypass` | Joins, CTEs, derived queries and view creation to widen access | no additional access | blocked | **blocked** |
| 9 | `metadata_enumeration` | `SHOW`, `information_schema`, `$`-metadata tables | no sensitive leakage | blocked | **blocked** |
| 10 | `aggregate_inference` | Researcher uses small groups and count differencing to infer one patient | blocked | **leaked** | **blocked** (after #46) |
| 11 | `quasi_identifier_linkage` | Researcher links age band + sex + ZIP (+ rare diagnosis) to outside data | blocked | **leaked** | **blocked** (after #46) |
| 12 | `audit_collector_outage` | Stop the audit collector mid-workload, then restart it | every query audited | inconclusive (no audit pipeline yet) | **blocked**: 100% coverage |

Final tally: **12 blocked, 0 leaked, 0 inconclusive** (first run: 9 blocked,
2 leaked, 1 inconclusive).

Status meaning: `blocked` = the boundary held (including an allowed query
returning exactly the independently expected rows); `leaked` = unauthorized
output or a demonstrated inference/linkage disclosure; `inconclusive` = an error
or missing dependency prevented a verdict. Unexpected SQL success fails pytest.

## What leaked, and the fix

### Before #46

The researcher role could read row-level `patients` with `age_band`, `sex` and a
masked 3-digit ZIP, plus `encounters.reason_code`. On the 1,009-patient cohort:

| Measurement | Value |
|-------------|-------|
| Equivalence classes on (age band, sex, ZIP) | 279, `k_min` = 1 |
| Unique (singleton) patients | 94 (9.3%) |
| Linkage with exact auxiliary age band / sex / ZIP | 94 correct links (9.3%), 100% precision |
| Adding a rare-diagnosis signature (codes held by <= 5% of patients) | 759 unique, 759 correct links (75.2%) |
| Exact count differencing (total 1,009 minus 1,008 excluding target) | target presence reconstructed |

Role-based access control alone did not stop this: every query was permitted.
The leak is in *what* a permitted role may see.

### Mitigation (#46)

- Researcher row-level access reduced to the pseudonymous patient token; quasi-
  identifiers and `reason_code` are denied in `policies/rules.json`.
- Research questions answered from three aggregate releases in `hospital.gold`
  (`researcher_demographics`, `researcher_geography`, `researcher_conditions`),
  built by `ingest/release.py` on every load: distinct-patient counts, no patient
  key, any cell below **11** patients dropped rather than shown.

### After #46

| Measurement | Value |
|-------------|-------|
| Row-level quasi-identifiers visible to research | none |
| Baseline linkage | 0 of 1,009 (0%) |
| Linkage with rare-diagnosis signature | 0 of 1,009 (0%) |
| Exact count differencing | not possible (no patient key, small cells absent) |
| Smallest published cell | 11 (demographics 15, geography 13, conditions 11) |
| Published cells | 18 demographics, 18 geography, 84 conditions |

Trade-off: researchers lose row-level cohort exploration; small subgroups are
invisible.

## Audit under collector outage

The suite stops the audit collector, runs queries while it is down, restarts it,
and reconciles known Trino query IDs against the audit table.

| Measurement | Value |
|-------------|-------|
| Queries issued across the outage | 4 |
| Lifecycle events expected / stored (`QueryCreated`, `QueryCompleted`) | 8 / 8 |
| Missing queries / events | 0 / 0 |
| Duplicate or invalid events | 0 / 0 |
| Coverage | **100%** |

Trino retries 5xx deliveries; the collector fsyncs each event to a spool before
acknowledging, and the writer de-duplicates on query ID and event type before
its single append to the Iceberg audit table. Denied queries are audited too.

## How the tests avoid fooling themselves

- **One handler per inventory row.** Collection fails if `tests/bypass/cases.csv`
  and the implemented handlers differ, so no case can silently disappear.
- **Independent expectations.** Doctor visibility is checked against saved
  pre-write care-team oracles and mask semantics in Python, never against live
  privileged queries.
- **Real positive controls.** Each denied probe is paired with an authenticated
  request to the same object or endpoint that succeeds, so "denied" is not just
  "wrong path".
- **Strict denial codes.** SQL negatives must fail with `PERMISSION_DENIED`;
  syntax, missing-table or transport errors count as inconclusive.
- **Genuinely old snapshots.** Snapshot IDs are recorded before boundary rows are
  appended, and tests confirm they differ from current ones.
- **Private evidence.** Per-run JSON with identities, query IDs and errors stays
  in `results/raw/bypass-*` (mode 0600, git-ignored); only the aggregate summary
  produced by `scripts/results_summary.py` is published.

## Residual risks (not claimed as fixed)

- Results are on synthetic Synthea data with a strong auxiliary-data attacker; they
  are evidence for this design, not a real-world re-identification rate or an
  anonymity proof. No differential-privacy budget is applied.
- Doctors and billing still see row-level data within their scope, by design.
- Read-only admin and service credentials (ETL, catalog, storage) are trusted;
  compromise of the host or those keys is outside this suite.
- Append-only grants on the audit table are not tamper-evident; query logs record
  what was asked, not exactly which records a person looked at.
- HMAC tokens are pseudonyms: anyone holding the key can re-link them.

## Reproduce

```sh
./scripts/generate_synthea.sh --patients 1000 --seed 42
ACCESS_TEST_SOURCE="$PWD/data/raw/synthea/1000-seed42" \
  RUN_BYPASS_INTEGRATION=1 uv run --locked --group dev \
  pytest tests/bypass -v --strict-config --strict-markers
uv run --locked python scripts/results_summary.py 'results/raw/bypass-*' results/raw/summary
```

Each run uses a fresh isolated Compose project with random credentials, so the
developer stack is untouched. Method details: [tests/bypass/README.md](../tests/bypass/README.md),
[issue #24 metrics](issue-24.md), [issue #46 mitigation](issue-46.md).

## Validation of the final run

Same CI job (commit `3aa7c3b`, main with #46 merged): unit tests 836 passed,
live access matrix 585 passed, bypass 12 passed, 0 failed
([test-summary.csv](../results/sanitized/ci-41205107037/test-summary.csv)).
