# Authenticated access acceptance (issue #19)

```sh
RUN_ACCESS_INTEGRATION=1 uv run --locked --group dev \
  pytest tests/access -v --strict-config --strict-markers
```

The default source is the committed two-patient Synthea fixture. Add
`ACCESS_TEST_SOURCE="$PWD/data/raw/synthea/1000-seed42"` to verify that population.
Docker/Compose and locked dev dependencies are required. Without the opt-in
flag, tests skip without starting Docker. CI enables them explicitly.
One disposable stack serves **484 individually reported checks**, using fresh
credentials, a CA and an ephemeral loopback HTTPS port. Cleanup removes only
that project's containers and volumes.

## Independent expectations

- [access-matrix.csv](../../policies/access-matrix.csv) defines each decision;
  [schema-contract.yaml](../../policies/schema-contract.yaml) fixes resources/types.
- Issue #17's private `care-team-oracle.json` supplies both doctors' saved
  source-derived sets. Its digest must match the successful ingestion report,
  and its source checksums are verified. No live care-team or privileged SQL
  query supplies expected memberships.
- The trusted fixture saves `access-data-oracle.json` from source-derived Arrow
  inputs plus adversarial fixture values **before catalog access**. Separate
  literal boundary sets cover UTC start/end dates, ongoing/overlapping/duplicate
  assignments and future/expired/invalid/missing assignments.
- `oracle.py` implements ZIP and month masks independently in Python, without
  importing the policy compiler, interpreting deployed rules or executing the
  matrix's `mask_sql`. New masks need an explicit independent implementation.
- Expected memberships are intersected with each table's saved input population;
  a patient need not have encounters. Full row **multisets** detect duplicated,
  missing, substituted and unauthorized data. Values, NULLs and types are checked.

Provisioning shares ingestion transformations for stored values. This suite
proves authorization; separate ingestion tests validate source mappings. Sharing
the matrix is intentional: it is the specification under test. Independence
lies in the expected memberships and value semantics.

## Runtime coverage

The official Trino Python DB-API client authenticates separately as the five
existing users over CA-verified HTTPS. Actual `current_user` and server groups
are checked. Tests query Trino directly.

Coverage includes all 176 matrix decisions (220 user-specific cells because
there are two doctors), exact allowed projections/types, hidden fields used
indirectly, star queries, doctor sets on patients/encounters, CTEs, subqueries,
joins, snapshots, metadata and forbidden operations. Write negatives target a
table each user can SELECT. Every negative requires `PERMISSION_DENIED`;
missing tables, invalid syntax and transport failures fail the test.

Committed rules are copied byte-for-byte and never widened/replaced. Test-only
audit/identity aliases and a real bronze sentinel ensure resources exist. These
fixtures test SQL permissions in a disposable clinical warehouse; they do not
establish production audit reading or replace real identity/storage isolation
tests. The earlier #18 integration test remains as a regression.

## Evidence

Pytest prints the private evidence directory during setup (visible with `-s`,
`--capture=tee-sys`, or on failure). It contains the three saved oracles, policy
and configuration bytes/digests, and `access-results.json`: individual outcomes,
query IDs/statements, and observed doctor/table counts including
`unauthorized_rows` and `missing_tokens`. Policy/oracle bytes are checked again
at teardown. Evidence files are `0600` in private directories; do not upload
memberships or generated secrets publicly. `--junitxml=...` gives CI test results.

See [issue #19 decisions and verification](../../docs/issue-19.md). The oracle's
regression checks run without Docker in `tests/unit/test_access_oracle.py`.
