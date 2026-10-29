# Issue #18 — clinical access policy and viva notes

Issue: [Implement rules.json: groups, grants, row filters and column masks](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/18).

## What changed and why

Previously, Trino could authenticate users and demonstrate filters on a smoke
table, but the committed policy granted no clinical data access. Issue #18
turns the existing **176 group/table/column decisions** into actual file rules.
The authorization boundary is Trino's system access control, applied to the
shared Iceberg base tables; consumers do not need to implement filtering.

- Doctors can read patients and encounters for their current care-team patients.
- Researchers can read the MVP patients and encounters with generalized ZIPs
  and month-level timestamps. Exact birthdate and encounter free text are hidden.
- Billing can read claims with exact procedure codes and decimal amounts,
  while procedure descriptions and all demographic tables are denied.
- Interactive admin can verify clinical tables and care-team assignments. It
  has no write/ownership authority or identity-table access. Audit metadata has
  a reserved SELECT rule, with restricted query text hidden.

`scripts/policy.py` explicitly compiles the CSV/schema/group contract, checks
the committed output, and prints its version/digest. `policies/version.json`
records the digest and hashes of the inputs and controller settings. Experiments
retain matching files with `--experiment-dir`; changing policy in an existing
run directory fails. The benchmark plan now requires this evidence.

## Decisions to explain in a viva

**1. Enforce on base tables inside Trino.** A dashboard filter can be bypassed
with another SQL client. A query-engine policy applies when reading the tables,
including through joins, CTEs and historical snapshots. Direct object-storage
and catalog access remain separate restricted service boundaries.

**2. Combine role and patient membership.** Role-based access decides which
tables and fields a doctor may use. A care-team predicate then decides which
patients that authenticated doctor may see. The same filter is required on
both patients and encounters; filtering only patients would leave direct
encounter queries open.

**3. Keep care_team protected while evaluating the predicate.** Granting doctors
SELECT on care_team would reveal other doctors' memberships. A reserved
`policy_care_team_reader` evaluates the subquery instead. It has no password,
group membership, interactive query grant or impersonation grant. Its reads
are limited to care_team and the outer patient-token columns required by
Trino's correlated-expression permission checks. The helper rules have no row
filter, avoiding recursion.

**4. Bind the caller explicitly in privileged filters.** In Trino 476,
`filter_environment.user` also changes the identity seen by `current_user`.
Using `ct.doctor_user = current_user` there would compare against the internal
reader. The compiler therefore emits separate exact user+group rules for Alice
and Bob, each with its reviewed doctor-login literal. Those literals come from
validated configuration, not SQL/client input. New logins require review and
regeneration. Impersonation remains denied.

**5. Use UTC query-time, half-open validity.** An assignment is active when
`valid_from <= today` and `today < valid_to`, with NULL end meaning ongoing.
Access starts on the start date and stops on the end date. It covers the
patient's history while active, irrespective of encounter date. Missing,
future, expired, reversed and empty assignments confer no access. EXISTS
returns a membership decision without multiplying results for duplicate or
overlapping assignments. Trusted ingestion rejects invalid intervals; the
policy fixture deliberately injects some to verify defensive SQL behavior.

**6. Distinguish masking from hiding.** A mask changes an allowed value: ZIP
`02139` and `02139-1234` become `021**`; invalid ZIPs become NULL. Timestamp
`2026-10-28 12:34:56.123456` becomes month start while remaining TIMESTAMP(6).
NULL remains NULL. A hidden field has `allow: false`, so direct references,
predicates and grouping are refused. SELECT * is also refused when it includes
a hidden field; clients must list permitted columns. Researchers use the existing age_band
instead of an exact birthdate. Billing retains exact procedure codes because
reconciling charges requires them; the privacy contract treats billing as a
trusted financial role.

**7. Review first-match rules and omitted defaults.** Narrow helper/role rules
come before the final table denial. A final catalog denial also blocks the
otherwise implicit system catalog. Empty operation sections explicitly deny
session changes, impersonation, ownership transfer, connector functions and
procedures. Query rules grant execution without cross-user query management.
Trino still permits its built-in functions and users' own query management.
Iceberg's hidden path/partition/file-time columns are explicitly denied.

**8. Add a second read-only controller.** Trino 476's file implementation does
not check table-procedure privileges for `ALTER TABLE ... EXECUTE`. Therefore,
read-only catalog flags alone are insufficient. A second built-in read-only
system controller supplies that denial; every request must pass both controllers.
The isolated schema test temporarily uses provisioning authority, then restores
both production controllers and verifies the clinical permission matrix.

**9. Record a version and exact-byte digest.** The version is a human label;
SHA-256 identifies the exact rules used. Keeping the manifest, rules and input
files with a run allows later results to be traced to a particular policy.
The digest is not a signature or proof against a trusted operator altering
both the evidence and the rules.

## Verification and limits

The new unit tests compare every CSV decision to its first matching rule and
detect contract, group, configuration, output and experiment-evidence drift.
The Docker acceptance test runs the exact committed policy over actual
Trino 476/Lakekeeper/Iceberg data. It compares both doctors' patient sets to
the previously saved source-derived oracle, plus separately saved boundary
expectations. It checks role/column access, mask outputs/types, NULLs, date
boundaries, overlap/duplicates, joins, CTEs, snapshots, metadata filtering,
impersonation and forbidden operations. CI runs it alongside existing HTTPS
and schema tests.

The normal identity warehouse remains ETL-only. The policy test deliberately
registers a fake identity catalog over synthetic fixture data to prove denial
even when the SQL resource exists; the schema regression additionally verifies
denial against the real isolated identity warehouse. Audit uses a fixture alias
to verify its reserved policy. No production audit warehouse/read grant or audit
delivery is introduced.

File column rules are a deny list: schema additions require trusted inventory,
matrix, classification and DDL checks before deployment. They are not a runtime
whitelist for arbitrary columns introduced by a trusted administrator. HMAC,
generalization and passing access tests do not establish anonymity or protection
from trusted host/ETL administrators. Issue #5 teammate sign-off remains pending.

Reproduce checks:

```sh
uv run --locked --group dev pytest tests/unit -q
uv run --locked python scripts/policy.py
RUN_POLICY_INTEGRATION=1 uv run --locked --group dev pytest tests/integration/test_policy.py -v
RUN_TRINO_INTEGRATION=1 RUN_SCHEMA_INTEGRATION=1 uv run --locked --group dev \
  pytest tests/integration/test_trino.py tests/integration/test_silver_schema.py -v
```

For the generated 1k source, set
`POLICY_TEST_SOURCE="$PWD/data/raw/synthea/1000-seed42"` for policy acceptance.
Fixtures and malformed boundary records stay in disposable Docker volumes.

Observed locally on **2026-10-29**:

- **602 unit tests passed** (416 existing plus 186 policy checks).
- Final clinical Docker acceptance passed on the two-patient fixture and on the
  full **1,000-patient** source. Each run verified all **176 matrix decisions**.
  The full source includes **48,976 encounters** and **203,698 charge lines**.
  Both source-derived doctor sets contain **600 patients**, including **200
  shared patients**. The nine additional boundary patients add three active
  Alice memberships and two active Bob memberships, so the final test totals
  are **603** and **602**, respectively; these are not source-population counts.
- Existing HTTPS/authentication and actual identity/silver DDL regressions:
  **2 passed**. Tests used fresh credentials and isolated volumes, restored
  production controller settings after privileged DDL, and cleaned up only
  their disposable stacks. The persistent development warehouse was unchanged.
- Python lint/format, all repository JSON/YAML/notebook syntax, shell syntax,
  generated-policy metadata and Git whitespace checks passed.
- Version: **clinical-v1**. Exact rules SHA-256:
  `3aae819ad48e8821b7d12912c381dfa4e0eaa170c5778a4a768ac3fe3f1b8063`.
  Private evidence was retained under
  `results/raw/issue18-2026-10-29-2-patients/` and
  `results/raw/issue18-2026-10-29-1000-patients/`, including matching policy
  manifests/rules/configuration, source and boundary oracles, aggregate results,
  and the test/fixture scripts. These directories are ignored by Git; token
  memberships and generated credentials are absent from this document.

Deployment still requires recreating the normal Trino container to add the
second controller mount; acceptance used disposable Trino instances. See
[deployment instructions](../policies/IMPLEMENTATION.md#deployment-and-evidence).

## Short viva answer

“I converted our access matrix into an enforced Trino policy. Roles control
tables and fields, and care-team membership controls doctor rows on both patient
tables. A protected internal identity evaluates memberships without exposing
care_team. Research masks preserve SQL types, while forbidden fields cannot be
referenced. I added a read-only controller because Trino's file controller leaves
table procedures unchecked, and recorded a policy digest with experiment
evidence. Tests compare real query results against independent expected sets.”

## Commit message

```text
feat(policy): enforce clinical access matrix in Trino

Compile role SELECT grants, caller-bound care-team filters and typed masks.
Keep hidden columns, identity, metadata and privileged operations restricted.
Add a read-only controller to deny table procedures and freeze policy evidence.
Cover the matrix, doctor oracles, date boundaries and denials on real Iceberg.

Closes #18
```

## PR description

```markdown
Closes #18.

Replace the no-clinical-access baseline with the 176 decisions from the access
matrix: doctor care-team filters on patients/encounters, researcher ZIP/month
masks and hidden fields, billing claims projections, and read-only admin checks.
Identity remains denied to every interactive role; restricted audit text stays
hidden and the audit metadata rule reserves a future production read path.

Bind each doctor's privileged filter to an exact authenticated user/group.
Use an internal reader without login/impersonation authority, UTC half-open
assignment dates and EXISTS semantics. Add Trino's built-in read-only controller
to deny table procedures that the 476 file controller leaves unchecked.

Add an explicit policy compiler/checker, version and SHA-256 manifest, and
immutable per-experiment policy/configuration snapshots. Verify all matrix
decisions, SQL types and mask values, source-derived doctor sets, boundary
fixtures, joins, snapshots, metadata and forbidden operations on real Iceberg.
Update CI, schema/authentication regressions and deployment/viva documentation.

Validation: 602 unit tests; clinical Docker acceptance on the small fixture
and full 1,000-patient source; existing HTTPS/authentication and schema Docker
regressions (2 passed); lint/format, JSON/YAML/notebook and shell syntax,
policy digest and whitespace checks.

Deployment: recreate Trino to add the new read-only controller mount; retain
the policy manifest beside each experiment's results. The production audit
pipeline and issue #5 teammate sign-off remain separate work.
```
