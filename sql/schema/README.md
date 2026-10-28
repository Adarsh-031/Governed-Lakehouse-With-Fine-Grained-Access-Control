# sql/schema

DDL for shared patients, encounters, claims, and protected care_team tables.

Issue #15 must use the proposed columns/types and resource names in
[schema-contract.yaml](../../policies/schema-contract.yaml), including separately
restricted identity and audit resources. See the
[access contract](../../policies/README.md) for derived age_band, UTC timestamps,
care-team validity, and the storage boundaries. DDL is not implemented here yet.
New columns need an explicit matrix decision for every group before exposure.
