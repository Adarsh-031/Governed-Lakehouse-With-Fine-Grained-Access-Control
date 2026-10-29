# Silver and restricted identity DDL (issue #15)

The SQL files create the five issue #15 tables with exactly the columns/types in
[schema-contract.yaml](../../policies/schema-contract.yaml). They introduce no
grants, views, source-data loading, row filters, or masks. Audit DDL belongs to
the separate audit implementation; its inventory is not part of this change.
New columns need an explicit matrix decision for every group before exposure.

## Apply through a trusted provisioning connection

Run `00_silver.sql`, `10_patients.sql`, `20_encounters.sql`, `30_claims.sql`, and
`40_care_team.sql` in that order against the `hospital` clinical warehouse.
Execute `50_identity.sql` **separately**, only through a trusted connection to the
restricted `identity` warehouse exposed as the `identity` SQL catalog. Each file
uses fully qualified names; do not substitute the clinical catalog for identity.
Use a CA-verified Trino CLI/client, for example `trino <trusted connection options>
--file sql/schema/10_patients.sql`, once that provisioning connection exists.

The committed Compose stack deliberately cannot apply these files: its Trino
service has read-only clinical catalog/S3 credentials, no identity registration,
and no interactive data grants. Do not grant interactive admin DDL or give the
normal Trino service ETL credentials to run this setup. Before applying outside
the acceptance test, provision a separately trusted SQL execution path with:

- The hospital warehouse at `s3://warehouse/silver/`, plus the separate identity
  warehouse at `s3://warehouse/identity/`, following ADR-003.
- Warehouse create/modify authority and matching scoped S3 write credentials.
- SQL schema/table creation permissions confined to this provisioning path;
  identity must remain unavailable to every MVP interactive SQL group.

The trusted PyIceberg [ingestion path](../../ingest/README.md#synthea-to-silver-issue-16)
now creates/validates patients, encounters, claims and care_team directly using ETL
authority, plus patient_identity in the separate identity warehouse. Production
bootstrap registers that ETL-only warehouse (#17). Ingestion does not execute
these SQL files or register a Trino identity catalog. Trusted SQL provisioning
for the DDL test remains separate from ordinary interactive SQL.
The DDL omits explicit locations so Lakekeeper assigns paths inside each
warehouse's storage boundary. Creating a schema/table name is not an access grant.
Clinical policies remain issue #18; do not expose loaded data before those checks.

`IF NOT EXISTS` permits safe reruns without replacing data or table locations.
It does **not** migrate or validate an already existing incompatible schema.
Compare `SHOW COLUMNS` and `SHOW CREATE TABLE` against the contract before loading;
review any schema evolution explicitly rather than dropping/recreating tables.

## Types, keys, and layout decisions

- `patient_token` is the non-null, versioned HMAC logical patient key in every
  table. Encounter and claim IDs are non-null opaque synthetic record keys;
  claims also require `encounter_id`. There are no raw patient IDs, names, SSNs,
  addresses, email addresses, or phone numbers in the clinical tables.
- The protected `care_team.doctor_user` is the required clinician login used
  for authorization, an explicit direct-identifier exception in the existing
  classification/access contract. It is not a patient identity field, and its
  presence does not permit doctors to enumerate the table.
- Birthdate and care-team validity remain `DATE`; UTC event values remain
  `TIMESTAMP(6)` without time zone. Codes/ZIPs are `VARCHAR` to retain leading
  zeros; financial amounts use `DECIMAL(18,2)`. `age_band` is a separate nullable
  stored field, derived at ingestion using the fixed reference date and rules in
  the [access contract](../../policies/README.md#derived-fields-and-storage-contract).
- `doctor_user`, `patient_token`, and `valid_from` cannot be NULL in care_team.
  `valid_to` may be NULL for an ongoing assignment; validity is half-open
  `[valid_from, valid_to)` in UTC dates. Missing optional clinical fields remain
  nullable, including unknown birthdate/age_band and an ongoing encounter's stop.
- Trino/Iceberg does not enforce primary-key uniqueness, foreign keys, interval
  ordering, token format, age-band derivation, or UTC normalization here. Trusted
  ingestion (#16/#17) must validate these, including rejecting empty/reversed
  care-team intervals and orphan references. SQL `NOT NULL` is enforced; it is
  not a substitute for those checks.
- All five tables explicitly use Iceberg v2 and Parquet. Start unpartitioned for
  the 1k MVP baseline; introduce partitioning after measured workload/scale
  evidence, rather than high-cardinality patient partitions or premature tuning.

Syntax and supported types/properties follow the pinned
[Trino 476 Iceberg connector](https://github.com/trinodb/trino/blob/476/docs/src/main/sphinx/connector/iceberg.md),
including its `NOT NULL` support. `name` exists only in
`identity.identity.patient_identity`, alongside the same patient token.

## Verification

```sh
uv run --locked --group dev pytest tests/unit/test_schema_ddl_contract.py
RUN_SCHEMA_INTEGRATION=1 uv run --locked --group dev pytest tests/integration/test_silver_schema.py -v
```

The opt-in Docker test and CI use fresh credentials, isolated volumes, a random
Compose project, and an ephemeral loopback HTTPS port. A **test-only** catalog
override uses the existing ETL identity/S3 scope to execute DDL; production
bootstrap creates the separate identity warehouse with ETL grants only. The test confirms the ordinary
Trino catalog service cannot access that warehouse. It never edits the committed
runtime credentials or policies and removes its project/volumes on exit.

The test executes every SQL file in Trino, checks exact columns/types and
warehouse paths, round-trips synthetic rows through all five Iceberg tables,
checks NULL rejection/optional NULLs, dates, microseconds, decimal cents, leading
zeros, and joins on consistent tokens/record IDs. It reruns DDL and verifies data
and locations survive. Finally it restores the exact no-grants policy and checks
all five interactive users are denied every created table, including identity.
These checks do not establish ingestion correctness or clinical policy enforcement.
