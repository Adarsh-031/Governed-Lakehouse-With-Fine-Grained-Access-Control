# ingest

Synthea CSV validation, stable patient tokens, relational integrity checks, and Iceberg loading.

## Column classification contract (issue #6)

[pii_tags.yaml](pii_tags.yaml) classifies all 44 columns of the six MVP tables in
[schema-contract.yaml](../policies/schema-contract.yaml), including identity and
audit. Each column has a classification, ingestion/storage `treatment`, four
explicit SQL `roles` treatments, and a rationale. This is a specification for
ingestion and policy implementation; **Trino does not read this file**. Runtime
enforcement remains issue #18. The
[access matrix](../policies/access-matrix.csv) owns operations, row scopes, and
column visibility; tags cannot grant access beyond it.

Classifications describe the content, independently of who may see it:

- `identifier`: pseudonymous patient tokens and opaque record/correlation keys.
- `direct_identifier`: patient names, clinician/user identities, and arbitrary
  audit SQL text that may contain identifying literals or secrets.
- `quasi_identifier`: demographics, geography, timestamps, assignment dates,
  and audit metadata that can support linkage or small-cohort inference.
- `sensitive_clinical`: encounter classes, clinical/procedure codes, and clinical
  descriptions. Free text may also contain direct identifiers.
- `financial`: payer affiliation, costs, and coverage amounts.
- `none`: normalized operational categories/codes and non-secret policy
  version/digest metadata with no intrinsic personal content. Their audit table
  remains restricted; this label does not mean public access.

Storage treatments have a separate meaning from SQL projection treatments:

- `tokenize`: derive the versioned HMAC from the canonical source patient ID
  once and reuse it across all tables, including identity. Never hash an existing
  token again. Encounter/claim keys remain opaque synthetic keys with consistent
  foreign keys; they do not need a second patient-token transformation.
- `restricted_table`: route the name to the separate identity warehouse, never
  the shared patient table. All four SQL groups, including admin, are denied.
- `generalize`: derive the stored `age_band` from birthdate using the fixed UTC
  `age_reference_date` and calendar-year rules in the
  [policy contract](../policies/README.md#derived-fields-and-storage-contract).
- `drop`: omit a source field from the target inventory. SSN is explicitly
  dropped; columns outside the inventory and explicit `source_fields` mappings
  are dropped by default. `source_fields` uses logical names from the original
  tags, not a complete Synthea header mapping.
  Restricted bronze retention is separate and confers no interactive SQL access.
- `keep`: preserve the stored value/type within its approved warehouse. It does
  not mean grant every role access. Restricted audit text, for example, remains
  available only through separately trusted audit processing paths.

For `roles`, `keep` means matrix `visible`; `drop` means matrix `hidden` and denies
all references, including predicates and joins, without deleting stored data.
`generalize` or `mask` means matrix `masked`, with its exact SQL in `masks` for
that role. Every role treatment still requires the matrix's allowed operation
and row scope. In particular, doctors retain query-time active-care-team scope;
they cannot enumerate care_team merely because filters depend on it.

## Decisions and reasons

Exact birthdates and ZIPs stay available for care/admin. Research uses the
separate ten-year `age_band` (with `90+`), ZIP prefix plus `**`, and UTC month-start
encounter times. Invalid ZIPs become NULL; NULLs and SQL types follow the matrix.
Research cannot reference birthdate or either encounter description. Clinical
codes remain available for the agreed research use, so HMAC/generalization does
not establish anonymity: linked records, rare diagnoses, and small cohorts
remain risks.

Procedure codes are classified as **sensitive clinical**, even in claims:
billing permission does not change their content. Exact codes are necessary to
validate services/reconcile charges, so the trusted billing role keeps them and
the financial amounts. Billing cannot read the free-text procedure description.
Doctors/researchers receive no claims access.

Care-team clinician identities are direct identifiers; assignment dates are
quasi-identifiers. Exact dates must survive ingestion for half-open UTC validity
checks. Only admin may enumerate the table; the trusted filter-evaluation path
is separate from ordinary SQL groups.

Audit is part of the MVP inventory and receives the same complete classification.
Authenticated users are direct identifiers; role membership, access patterns,
counts, and times are conservatively quasi-identifiers. Arbitrary query text is
treated as potentially directly identifying and denied to every SQL role, even
admin. Routine admin can inspect the other audit metadata only through the
future separately reviewed audit read path. The current Trino storage key gains
no identity or audit permissions from this file.

## Validation

Run `uv run --locked --group dev pytest tests/unit/test_pii_tags.py tests/unit/test_access_matrix.py`.
The existing CI unit-test step discovers these checks, and its YAML syntax step
also parses this file. Tests reject duplicate YAML keys, missing/extra inventory
columns, unknown classifications/treatments, incomplete role maps, and any
role-treatment or mask disagreement with the matrix. They also protect the
source-ID/name/SSN routing and key clinical/financial/identity decisions. These
are specification checks; they do not prove deployed ingestion or Trino behavior.

## Synthea to silver (issue #16)

After secret initialization and source generation, load the 1k sample with:

```sh
./scripts/ingest.sh
```

Docker Compose builds the locked Python helper, waits for the private storage,
OIDC/catalog dependencies and bootstrap, and runs `python -m ingest.pipeline`
inside the backend network. No host catalog/S3 ports or Trino write grants are
needed. The ingestion container receives only the ETL OIDC/S3 credentials and
HMAC settings, with a read-only source mount and filesystem. The image contains
code and locked dependencies; raw data and host secrets stay outside the image.

`INGEST_SOURCE_DIR=/absolute/path/to/sample ./scripts/ingest.sh` selects another
Synthea export. `LAKEHOUSE_ENV_FILE` selects a different Compose env file.
`./scripts/ingest.sh --validate-only` performs all source checks/transforms and
prints the count report without creating tables or writing data (Compose still
starts dependencies). A host-only offline alternative, after loading the HMAC
variables from the private env file, is `uv run --locked python -m ingest.pipeline
--source data/raw/synthea/1000-seed42 --validate-only`.

### Mapping and validation decisions

- Accept only the pinned Synthea 3.4.0 manifest; verify CSV headers, widths, row
  counts and SHA-256 against the source manifest before reading approved fields.
  MIMIC needs a separate mapping and is deliberately rejected here.
- Patient IDs are canonicalized and namespaced as `synthea:<Id>` before calling
  `patient_token`. Decode the generated 32-byte `HMAC_KEY_HEX`; use the same key
  and `HMAC_KEY_VERSION` across patients, encounters and claims. Names, SSNs,
  addresses and unrelated columns never enter these Arrow tables. #17 must reuse
  this exact ID domain for care_team and restricted identity.
- Use the manifest's simulation `reference_date` as the fixed age reference;
  record it in the JSON report and Iceberg snapshot summary. Calendar years,
  Feb 29 anniversaries, NULL/future births and the `90+` cap follow the access
  contract. Invalid dates fail; invalid ZIPs become NULL; leading zeros survive.
- Parse timezone-qualified encounter timestamps, normalize to UTC, and store
  microseconds without time zone. Reject invalid dates, naive timestamps,
  sub-microsecond precision and reversed encounter intervals. Optional NULLs
  survive. Decimal amounts are exact cents with DECIMAL(18,2) bounds; no floats
  or rounding. Unknown transaction/payment types fail closed.
- Enforce nonempty unique keys, patient/encounter/claim/payer references, and
  matching patients on claim-to-encounter and transaction-to-header joins. Every
  claims header must have charge lines. Validation completes before connecting
  to the catalog; existing-table checks complete before the first table write.

### Claims grain: charge lines, not headers

A native `claims.csv` header can cover many procedures. The silver schema has a
single procedure code/description and amount, so preserve **one row per CHARGE
transaction** instead of selecting an arbitrary procedure or duplicating a header
total across lines. `claim_id` is that charge transaction's opaque `ID`;
`encounter_id` is `APPOINTMENTID`. `PROCEDURECODE`, `NOTES`, and `AMOUNT` supply
procedure code, description, and total cost. The primary payer name is resolved
from the header's `PRIMARYPATIENTINSURANCEID` through `payers.csv`; no insurance
remains NULL. The scalar payer field identifies the **primary** payer;
`payer_coverage` includes payments by primary and secondary insurance.

The [pinned Synthea exporter](https://github.com/synthetichealth/synthea/blob/v3.4.0/src/main/java/org/mitre/synthea/export/CSVExporter.java)
emits monotonically increasing `CHARGEID` for transaction ordering, not a shared
foreign key to a charge. Sort numerically and associate each transaction with
the preceding CHARGE **within its claim**. Validate line code/description
consistency. Sum only `PAYMENT`/`ECHECK` amounts as coverage; exclude copays,
patient cash/check/card payments, adjustments and transfers. Never count transfer
amounts as new costs. This mapping is tied to this exporter version.

The report reconciles patient/encounter counts one-to-one and silver claims to
source CHARGE rows, plus the number of distinct represented claims headers.
For the 1k seed-42 sample that means 1,000 patients, 48,976 encounters and 203,698
charge rows, representing all 83,829 native claims headers. The 660,227
transaction records are not 660,227 independent claims.

### Writes, retries and limits

Use PyIceberg under the ETL service identity to create missing unpartitioned
Iceberg v2/Parquet tables in `hospital.silver`, matching issue #15's SQL types and
required keys. Existing tables must have the exact columns/types/nullability,
version, layout and silver location. No SQL DDL or interactive grants are added.
Identity and care-team loading remain #17; clinical policy enforcement remains #18.

An identical rerun verifies every stored value without appending or changing
snapshots. A run interrupted between table commits can resume missing/empty
tables, provided all existing rows match. Changed data, keys, versions or schema
fail with a migration-required error: there is no implicit overwrite, upsert,
rotation or cohort merge. Each table commit is atomic; the three-table run is
**not** a distributed transaction. Keep interactive grants disabled until the
whole run passes. Run one trusted writer at a time; concurrent ETL is unsupported.
Failed writes may leave unreferenced objects for separately reviewed maintenance.

The final JSON report contains counts, source checksums, key **version**, fixed
age date, and readback status, never the HMAC key or patient rows. SDK exceptions
are reduced to their class name; validation errors contain rule names only.
Preserve this JSON privately with experiment evidence. The pipeline loads data
in memory and compares full readbacks for the 1k baseline; 10k/100k memory and
performance remain unmeasured. HMAC tokens still permit linkage and do not prove
anonymity.

### Verification

```sh
uv run --locked --group dev pytest tests/unit/test_ingestion.py
RUN_INGEST_INTEGRATION=1 uv run --locked --group dev pytest tests/integration/test_ingestion.py -v
INGEST_TEST_SOURCE="$PWD/data/raw/synthea/1000-seed42" RUN_INGEST_INTEGRATION=1 \
  uv run --locked --group dev pytest tests/integration/test_ingestion.py -v
```

The opt-in integration test defaults to a tiny committed synthetic CSV fixture
for CI. The final command loads the full locally generated sample into a fresh,
random Compose project using disposable secrets/volumes. It verifies exact
Iceberg readback, source count reconciliation, safe reruns and rejected token
version drift, then removes its own resources. Unit cases cover leap days,
NULLs, UTC microseconds, cents, leading zeros, malformed data, orphan/mismatched
references, payments/transfers, source integrity, schema drift, partial retries,
and secret-safe failures. The fixture is a handcrafted mapping example, not a
claim that Synthea generated those identifiers.
