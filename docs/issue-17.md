# Issue #17 — care-team assignments and restricted identity

Issue: [Generate care_team assignments and restricted identity table](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/17).

## What changed and why

The existing `./scripts/ingest.sh` flow now prepares and loads five tables.
`hospital.silver.care_team` supplies the protected membership data required by
the doctor row filters planned in #18. `identity.identity.patient_identity`
keeps `(patient_token, name)` outside the shared clinical warehouse. Both reuse
#16's `synthea:` HMAC token map, so joins are consistent without exposing source
patient IDs or deriving keys from names.

Bootstrap registers the identity warehouse under `s3://warehouse/identity/` and
reconciles ETL-only managed service grants. The ordinary Trino service has no
identity catalog, REST grant or S3 read authority. The clinical SQL policy is
still the committed no-grants policy; interactive admin does not gain identity
access. No storage policies, dependencies or HMAC secrets were changed.
The normal launcher also exposed generated relative secret paths being parsed
as named Compose volumes. These three mounts now declare `type: bind`, remain
read-only, and refuse automatic host-path creation; existing env files work
without editing credentials.

Each doctor's expected token set is saved privately before catalog access. This
gives later SQL authorization tests a stable source-derived oracle rather than
asking the live authorization table what the expected answer should be. Full
readback and comparisons across both warehouses protect reruns and upgrades
from an existing #16 clinical load.

## Decisions

- **Use `doctor_alice` and `doctor_bob`.** The issue's `doctor_1/doctor_2` are
  placeholders; existing authentication and the access contract use these real
  experiment login names. Generated provider IDs cannot establish login authority.
- **Use a seeded synthetic rule.** Sort canonical source patient IDs, shuffle
  with isolated Python `Random(42)`, share every fifth patient, and assign the
  rest by shuffled index parity. This is reproducible regardless of source row
  order and creates overlap plus doctor-specific exclusions. For 1,000 patients:
  600 per doctor, 200 shared, 400 exclusive to each, 1,200 assignment rows.
  This fixture does not claim to infer clinical relationships or consent.
- **Use fixed UTC half-open validity.** Start defaults to the simulation's
  `2025-01-01` reference date; NULL end means ongoing. Optional `--valid-from`
  and `--valid-to` support finite intervals. Empty/reversed intervals fail.
  Current membership gives access to the patient's history once #18 enforces it;
  encounter dates do not determine membership.
- **Store minimal display names separately.** Join trimmed nonempty `FIRST`,
  `MIDDLE`, `LAST`; preserve NULL when all are missing. Names can repeat and
  never identify the token domain. Do not copy SSNs, addresses or other export
  fields. The clinical Arrow projections remain unchanged.
- **Freeze evidence; fail on drift.** Default oracle:
  `data/staged/issue17/care-team-oracle.json`, mode `0600`, under a directory
  created with umask `077`. It records sorted token sets, rule/seed, validity,
  source checksums and token version; no names, raw patient IDs or keys. Atomic
  no-clobber publication prevents partial evidence. Conflicting existing
  evidence requires an explicit migration. The JSON report includes its digest
  and aggregate counts without printing memberships.
- **Keep retries conservative.** Preflight every existing table in both
  warehouses before any table mutation. Preserve identical rows/snapshots;
  fill missing/empty tables; reject changed schemas, locations, data, seed,
  validity, source or key domain. Table commits are atomic individually; five
  tables are not one transaction. A saved oracle remains candidate evidence
  until the final report confirms complete readback. One trusted writer at a time.
- **Keep the scope at ingestion.** No doctor filters, masks, audit path,
  automatic credential rotation or additional cohort merging are introduced.
  Runtime clinical authorization and SQL validity boundaries remain #18.

## Validation

```sh
uv run --locked --group dev pytest tests/unit -q
RUN_INGEST_INTEGRATION=1 uv run --locked --group dev pytest tests/integration/test_ingestion.py -v
INGEST_TEST_SOURCE="$PWD/data/raw/synthea/1000-seed42" RUN_INGEST_INTEGRATION=1 \
  RUN_SCHEMA_INTEGRATION=1 uv run --locked --group dev pytest \
  tests/integration/test_ingestion.py tests/integration/test_silver_schema.py -v
```

Unit coverage includes a literal expected membership example, row-order/seed
behavior, invalid intervals, name/NULL projection, shared tokens, private oracle
preservation, conflicting evidence, identity location/data drift, bootstrap grant
reconciliation, #16 upgrades and partial retries. Docker checks compare the
saved sets with newly read care-team rows, inspect identity coverage and
locations, verify unchanged snapshots, and exercise actual identity REST/S3
denials. The DDL test retains all-five-user denial coverage. These checks do not
establish runtime clinical row filtering or anonymity.

Observed locally on **2026-10-29**:

- **416 unit tests passed**; repository Python lint/format, JSON/YAML syntax,
  ingestion shell syntax, and Git whitespace checks passed.
- The tiny synthetic fixture passed Docker ingestion acceptance. The complete
  1k source passed the isolated ingestion test and Trino/Iceberg DDL regression
  (**2 passed**), including all-five-user denied-table checks. Disposable test
  stacks used fresh credentials and removed only their own resources.
- The separate finite-validity fixture case passed (**1 passed**), checking
  saved sets before/start-of validity and before/at the exclusive end date.
- The normal `./scripts/ingest.sh` command successfully populated the persistent
  local warehouse: **1,000 patients**, **48,976 encounters**, **203,698 charge
  lines**, **1,200 care-team rows**, and **1,000 identity rows**. All five tables
  reported exact readback, with zero patient/encounter orphans. Care-team counts
  were **600 per doctor**, **200 shared**; start **2025-01-01**, end **NULL**,
  assignment seed **42**, HMAC version **v1**. Existing credentials were preserved.
- The persistent oracle was saved at the documented default path, verified as
  mode **0600** inside mode **0700**, and confirmed ignored by Git. Its token
  lists and the private HMAC key are deliberately absent from this record.
- A second normal launcher run reported **verified existing** for all five
  tables, with exact readback and the same oracle digest; no duplicate rows were
  appended. Snapshot preservation was independently checked in Docker acceptance.

## PR title

`feat(ingest): load care-team assignments and restricted identity`

## PR description

```markdown
Closes #17.

Extend trusted Synthea ingestion to load care_team and the separate restricted
patient_identity table. Seeded assignments use the existing doctor_alice and
doctor_bob logins, include overlap, and save each doctor's expected patient set
privately before catalog access for #18's authorization tests.

Register an ETL-only identity warehouse and retain Trino catalog/S3 denial.
Reuse the clinical HMAC token map; project names only into identity. Preflight
both warehouses before writes, preserve identical reruns and reject membership,
source, schema, location or key-domain drift. Existing #16 clinical tables can
be verified while the missing care-team/identity tables are added.

Validation: 416 unit tests and lint/format checks; Docker ingestion acceptance
on ongoing/finite synthetic fixtures and the full 1,000-patient source;
Trino/Iceberg DDL regression; persistent local loading and an identical rerun.
The acceptance tests compare saved doctor sets, verify exact readback and stable
snapshots, and prove identity REST/S3 denial. Clinical SQL policies remain #18;
the five table commits are not a distributed transaction.
```

## Commit message

```text
feat(ingest): load care-team assignments and restricted identity

Generate seeded doctor memberships with overlap and private expected sets.
Load names separately using the shared patient HMAC token map.
Provision ETL-only identity access and preflight both warehouses on retries.
Cover oracle stability, identity isolation, and existing clinical-load upgrades.

Closes #17
```
