
# Policy implementation gate

The supplied rules.json intentionally grants no data access. It is a starting
deny configuration, not a completed or universally deny-all Trino policy.
Trino 476 loads it through the system file access control, and the Iceberg
connector uses `iceberg.security=SYSTEM`. Issue #13's acceptance test checks
the no-data-access baseline and applies a separate synthetic test policy to
prove row filtering, column masking, group resolution, and impersonation denial.
This does not implement or validate the clinical permission matrix. See
[ADR-001](../docs/architecture.md#version-specific-implementation-contract)
for the release-specific policy reference.
Explicitly review every operation category, including functions, procedures,
session properties, system information, authorization, and metadata visibility.

1. Keep authenticated identities bound to server-managed groups and deny impersonation.
2. Permit only approved catalogs/schemas/tables and SELECT operations.
3. Protect care_team from edits and unintended enumeration.
4. Apply doctor filters to every patient-bearing table, not just patients.
5. Keep mask output types compatible. Expose age_band separately from birthdate.
6. Test first-match ordering and omitted-section defaults.
7. Generate rules from pii_tags.yaml only through an explicit validated compiler.
8. Verify base tables, views, time travel, metadata, and alternative clients.
9. Version the policy and record its digest with each experiment.
