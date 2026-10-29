# Access contract (issue #5)

[access-matrix.csv](access-matrix.csv) defines the intended MVP permissions: one
decision per `(group, table, column)`, including denied tables.
[schema-contract.yaml](schema-contract.yaml) fixes the proposed column inventory
and SQL types for DDL (#15), classification (#6), ingestion (#16/#17), policy
(#18), and access tests. These inputs are explicitly compiled by
[`scripts/policy.py`](../scripts/policy.py) into the deployed [rules.json](rules.json).
Issue #18 implements the clinical contract; see [implementation](IMPLEMENTATION.md)
and the [decisions and viva notes](../docs/issue-18.md).

[pii_tags.yaml](../ingest/pii_tags.yaml) classifies every inventory column and
records storage and per-role treatments that must match this matrix. See the
[classification contract](../ingest/README.md#column-classification-contract-issue-6)
for treatment semantics, decisions, and CI checks.

This contract covers synthetic data only. New columns, tables, cohorts, roles,
and warehouses require review; unlisted resources/operations receive no grants.

## CSV interpretation

- `group`: `doctor`, `researcher`, `billing`, or `admin`, bound by the server's
  group provider. Unknown groups receive no grants. Each MVP user has exactly
  one group; reject overlapping membership until precedence is reviewed.
- `table`: a schema-contract key; its `resource` is the exact intended SQL name.
  Identity has an ETL-only warehouse registration; audit reserves a future catalog.
- `column`: exact name; its SQL type comes from the schema contract.
- `operations`: `SELECT` or `NONE`, consistent across each group/table.
- `row_scope`: a predicate below, consistent across each group/table.
- `visibility`: `visible` returns the stored value; `masked` returns `mask_sql`;
  `hidden` denies column references, including predicates, joins, grouping,
  ordering, expressions, and views. Hidden does not mean NULL.
- `mask_sql`: a Trino 476 expression only for masked columns, preserving the
  SQL type and returning NULL for NULL input. Concrete outputs are below.

Denied tables have `NONE`, `NONE`, `hidden`, and no mask on every column. Allowed
tables with hidden fields require explicit permitted projections; `SELECT *`
must not expose hidden fields. Row filters apply before aggregation and on every
patient-bearing table a role can query. Joins, views, CTEs, subqueries, historical
snapshots, and alternate clients must preserve these rules. Iceberg metadata,
system tables, and table functions are separate resources with no implicit grant.

## Exact row scopes

`NONE` means `FALSE`, with table access denied, not an empty-result substitute.
`ALL_MVP` means `TRUE` within the one loaded synthetic dataset in the named
resource. Researchers get all MVP patients/encounters; billing gets all MVP
claims. There is no tenant, payer assignment, or consent column: these are
explicit experiment scopes, not production cohort approvals. Loading additional
populations requires a protected cohort-membership design and review first.

`ACTIVE_CARE_TEAM` is the following predicate on **each** allowed doctor table;
replace `<table>` with its exact outer table name:

```sql
EXISTS (
    SELECT 1 FROM hospital.silver.care_team AS ct
    WHERE ct.patient_token = <table>.patient_token
      AND ct.doctor_user = current_user
      AND ct.valid_from <= CAST(current_timestamp AT TIME ZONE 'UTC' AS DATE)
      AND (ct.valid_to IS NULL OR
           CAST(current_timestamp AT TIME ZONE 'UTC' AS DATE) < ct.valid_to)
)
```

Assignments use UTC dates and half-open `[valid_from, valid_to)` validity; NULL
`valid_to` means ongoing. Missing, future, expired, invalid, or NULL-token
assignments confer no access. Ingestion must reject NULL `valid_from` and
reversed/empty intervals. EXISTS prevents duplicates; overlaps allow both doctors.
Access is based on the assignment **at query time**, includes the patient's
history, and ends at expiry; it is not based on encounter date.

Use existing `doctor_alice`/`doctor_bob` identities, replacing #17's placeholder
`doctor_1/doctor_2`. A narrowly trusted filter-evaluation identity reads care_team.
Trino 476 binds `current_user` to that evaluation identity inside a privileged
filter, so the compiler emits an exact user-and-group rule for each doctor with
a trusted doctor-login literal in this predicate. The internal reader has no
password, interactive query grant, or impersonation grant. Acceptance tests
compare both doctors' results with saved source-derived sets and independent
boundary fixtures. Direct doctor care-team enumeration stays denied.

## Role decisions and reasons

- **Doctor:** SELECT patients and encounters for active care-team patients.
  Demographics, exact birthdate/ZIP, codes, and clinical descriptions are visible
  for treatment. Claims, care_team enumeration, identity, and audit are denied.
  Claims are unnecessary for the MVP clinical workflow.
- **Researcher:** SELECT all MVP patients/encounters with stable HMAC patient
  tokens and clinical codes. Birthdate is hidden; separate age_band is visible.
  ZIP is masked to three digits plus `**`: `02139` and `02139-1234` both become
  `021**`; invalid/non-US values become NULL. Start/stop times are masked to UTC
  month start: `2026-10-28 12:34:56.123456` becomes
  `2026-10-01 00:00:00.000000`, retaining TIMESTAMP(6). Both encounter description
  fields are hidden to avoid free-text identifiers. Claims, care_team, identity,
  and audit are denied as unnecessary for condition counts by age band.
  Generalization/HMAC do not establish anonymity: rare diagnoses/cohorts,
  token linkage, and timing remain residual risks.
- **Billing:** SELECT all MVP claims. Tokens, payer, costs, and **procedure_code
  are visible** because exact codes are needed to validate services/reconcile
  charges. Codes can reveal sensitive treatment: billing is explicitly a trusted
  financial role. `procedure_description` is hidden because free text adds
  unnecessary clinical detail and potential identifiers. Patients, encounters,
  care_team, identity, and audit are denied. Any workflow needing names or
  clinical detail requires separate review.
- **Admin:** SELECT all MVP patients, encounters, claims, and care_team with
  stored values visible for verification. SELECT audit metadata only;
  `query_text_restricted` is hidden even from this routine SQL role because
  literals may contain identifiers/secrets. Identity is denied to **every** MVP
  SQL group, including admin: the current Trino S3 key cannot read `identity/`.
  Admin is not storage root/the bootstrap service and gets no writes/ownership.
  Audit SELECT is a future target requiring a separately reviewed read-only
  warehouse/service path; the current Trino key cannot read `audit/` either.

## Derived fields and storage contract

Only inventory columns enter shared tables. `patient_token` is the existing
versioned HMAC, consistent across tables, never the source patient ID.
Encounter/claim IDs are opaque synthetic keys with consistent foreign keys;
never embed patient identifiers. Identity contains only `(patient_token, name)`.
Source patient IDs, SSN, contact details, and other export columns stay in
restricted bronze inputs or are dropped; no automatic Synthea column passthrough.

Derive age_band at ingestion from completed calendar years on a fixed UTC
`age_reference_date`, recorded in the data card/run manifest. Labels: `00-09`,
`10-19`, `20-29`, `30-39`, `40-49`, `50-59`, `60-69`, `70-79`, `80-89`, `90+`.
NULL/future birthdates produce NULL. Feb 29 anniversaries fall on Mar 1 in
non-leap years. The same input/reference date reproduces the same band; do not
replace DATE birthdate with text or derive age dynamically at query time.

`hospital.silver.*` reserves tables in ADR-003's clinical warehouse.
`identity.identity.patient_identity` is loaded through the separate ETL-only
identity warehouse under `identity/` (#17), without an interactive Trino catalog.
`audit.audit.query_events` still reserves a future catalog/warehouse under
`audit/`. SQL names alone create no registrations/grants. Audit columns match the
[normalized event contract](../audit/event-contract.json); timestamps are stored
as UTC TIMESTAMP(6) throughout. Restrict audit text in ingestion/spool paths too.

## Operations and trust boundaries

All four interactive SQL groups receive only the CSV's SELECT permissions.
Deny INSERT, UPDATE, DELETE, MERGE, TRUNCATE, CREATE/ALTER/DROP, GRANT/REVOKE,
ownership/authorization changes, care_team edits, impersonation, procedures,
unapproved table functions, and session/system changes that could weaken policy.
Approved scalar functions do not imply arbitrary privileged function execution.
Metadata may describe allowed resources only, without hidden-field values or
policy-secret leakage; #18 must test version-specific metadata behavior.

Doctor, researcher, billing, and interactive admin get no bronze access, storage
credentials, direct REST catalog/OIDC service credentials, or private-service
network path. SQL catalog names do not grant catalog API access. Trusted ETL
provisions care_team/identity; the separate audit writer appends audit records;
the bootstrap path manages setup/grants. ADR-002/003 govern these service
authorities; human group names do not inherit them. Host/storage administrators
remain trusted actors outside row/column isolation.

## Verification and review status

Run `uv run --locked --group dev pytest tests/unit/test_access_matrix.py`.
These tests validate specification coverage/consistency and audit-contract
alignment, not runtime enforcement. Issue #18's Docker acceptance checks
separate users, boundary dates, forbidden column references, mask outputs/types,
NULLs, joins, metadata, and time travel. Issue #19's independent
[access suite](../tests/access/README.md) compares real authenticated results
with saved #17 doctor sets, pre-write values and Python mask expectations;
run `RUN_ACCESS_INTEGRATION=1 uv run --locked --group dev pytest tests/access -v`.
YAML/CSV changes require explicit
regeneration and review; run `uv run --locked python scripts/policy.py` to detect drift.
Use the pinned [Trino 476 policy reference](https://github.com/trinodb/trino/blob/476/docs/src/main/sphinx/security/file-system-access-control.md)
and [date/time reference](https://github.com/trinodb/trino/blob/476/docs/src/main/sphinx/functions/datetime.md)
when translating masks/filters; review first-match and omitted-section defaults.

- [x] Every MVP column has a decision for all four groups.
- [x] Billing procedure-code decision and rationale recorded.
- [ ] Teammate reviewed and agreed (issue #5 acceptance requirement).

No teammate approval has been supplied. Before closing #5, record reviewer,
date, and PR review/comment link here or in the PR. Review billing codes,
synthetic cohort scope, query-time care-team expiry, admin audit-text restriction,
and denied identity access. These are proposed decisions, not invented sign-off.
