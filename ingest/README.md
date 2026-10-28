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
