# Issue #19 — access tests with an independent oracle

Issue: [Access tests with an independent oracle](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/19).

## What changed and why

`tests/access/` now runs 484 individually reported checks through the official
Trino Python client against an isolated Trino 476/Iceberg stack. Each existing
user authenticates separately over verified HTTPS. Tests prove the committed
`rules.json` implements the matrix at runtime. CI enables the suite explicitly
and produces JUnit results.

Both doctors' patients/encounters are compared with #17's saved token sets and
literal boundary memberships, with observed missing/unauthorized row counts.
Full row multisets catch duplicate inflation too. Researcher masks and allowed
stored values are compared with saved inputs; hidden fields must fail even in
indirect references. All negatives require `PERMISSION_DENIED`. Query shapes,
snapshots, metadata, writes, care-team edits, bronze and impersonation are covered.

The Compose harness moved to `tests/conftest.py` so both suites share isolation
and secret handling. Existing integration fixtures remain function-scoped; the
access module shares one disposable stack. The trusted fixture now saves input
values before catalog access and adds nullable claims and an existing bronze
resource. Existing #18 checks remain as regressions.
Explicit test packages and relative imports also prevent duplicate unit/
integration filenames from colliding during a combined `pytest` run.

## Decisions

1. **Use the matrix as the specification.** Read decisions and schema types
   directly. Keep test identities separate from compiler constants; never
   import `scripts.policy` or derive expected decisions from deployed rules.
2. **Use saved #17 memberships.** Verify the oracle digest against successful
   ingestion and verify source checksums. Never infer permission from live
   care_team, privileged results or the same SQL filter. Literal boundary
   memberships are saved separately before assignment writes.
3. **Implement mask expectations in Python.** Do not reuse `mask_sql` as an
   expected answer. Stored values come from pre-write Arrow inputs, reusing
   ingestion transformations. This independently tests policy behavior, not
   the complete source-to-Iceberg ETL mapping; ingestion tests own that check.
4. **Check exclusion and completeness.** Counts alone can conceal substituted
   unauthorized tokens; sets alone can conceal duplicate rows. Compare
   multisets and record extra/missing counts. Intersect saved memberships with
   each table's input tokens to handle patients without encounters correctly.
5. **Make negative checks non-vacuous.** Require the exact permission error,
   attempt writes on readable tables and provision real forbidden resources.
   Audit/identity aliases prove SQL rules only; production storage grants and
   the restricted identity warehouse keep their separate integration checks.
6. **Preserve policy and evidence.** Use fresh credentials/CA and isolated
   volumes. Snapshot exact committed policy/configuration and oracle digests;
   record query IDs, statements, outcomes and doctor leakage counts privately.
   Reject changed policy/oracle bytes and UTC-date mismatches.
7. **Opt in locally; enforce in CI.** Default collection does not start Docker.
   CI sets `RUN_ACCESS_INTEGRATION=1`. Twenty oracle unit checks reject extra,
   missing, substituted and duplicate rows, ineffective masks, and unknown
   mask semantics.

## Reproduce

```sh
uv run --locked --group dev pytest tests/unit -q
RUN_ACCESS_INTEGRATION=1 uv run --locked --group dev \
  pytest tests/access -v --strict-config --strict-markers
ACCESS_TEST_SOURCE="$PWD/data/raw/synthea/1000-seed42" \
  RUN_ACCESS_INTEGRATION=1 RUN_POLICY_INTEGRATION=1 RUN_TRINO_INTEGRATION=1 \
  uv run --locked --group dev pytest tests/access \
  tests/integration/test_policy.py tests/integration/test_trino.py \
  -q --strict-config --strict-markers --junitxml=/tmp/issue19-access.xml
uv run --locked python scripts/policy.py
uv run --locked --group dev ruff check .
uv run --locked --group dev ruff format --check .
```

See the [access suite README](../tests/access/README.md) for evidence and scope.

## Verified locally on 2026-10-29

- **484 access checks passed** on the committed two-patient source.
- **484 access checks passed** on the generated 1,000-patient source, followed
  by the existing clinical-policy and HTTPS/authentication regressions:
  **486 passed** together in 458.80 seconds.
- **622 unit tests passed**, including 20 new oracle checks. Python lint/format,
  policy/configuration digest checks, JSON/YAML/notebook syntax and Git
  whitespace checks passed. Default access collection skips all 484 tests
  without Docker; CI explicitly enables them.
- Combined default collection/execution passed: **622 passed, 491 skipped**
  (484 access and seven other opt-in integration cases). Package boundaries
  prevent duplicate test-module names from colliding.
- Each access run executed **627 direct Trino queries**: 257 successful and
  370 returning exactly `PERMISSION_DENIED`.
- On the full source, Alice returned **603 patients / 28,509 encounters** and
  Bob **602 patients / 29,276 encounters**. Both tables contained exactly
  **603 / 602 distinct expected tokens**, respectively, with **0 unauthorized
  rows and 0 missing tokens**. These patient totals include nine boundary
  fixtures: three active for Alice, two for Bob; the original source-derived
  sets contain 600 each, with 200 shared.
- Rules stayed at `clinical-v1`, SHA-256
  `3aae819ad48e8821b7d12912c381dfa4e0eaa170c5778a4a768ac3fe3f1b8063`.
  The committed policy and runtime configuration were not changed.

Private evidence is retained in ignored directories
`results/raw/issue19-2026-10-29-2-patients/` and
`results/raw/issue19-2026-10-29-1000-patients/`. It includes oracles, exact policy
inputs, checksums, per-test/query results and JUnit XML. The full run also saves
the test sources used. No generated credentials are copied into these artifacts.
Disposable projects were removed; the development stack was left unchanged.

## Suggested commit message

```text
test(access): verify Trino permissions with an independent oracle
```

## Suggested PR description

```markdown
Closes #19.

Add 484 authenticated Trino checks comparing the committed policy with the
access matrix, saved #17 doctor sets and independent Python mask expectations.
Verify exact values/types, hidden fields, doctor exclusion/completeness, query
shapes and permission-denied results for forbidden reads, writes, bronze,
care-team edits and impersonation.

Share isolated Compose provisioning, preserve private oracle/policy/query
evidence and run the suite in CI with JUnit results. Add 20 oracle unit checks.
Audit/identity aliases test SQL policy only; existing storage and warehouse
isolation tests retain their separate responsibility.

Validation: 484 access checks passed on both the small fixture and 1,000-patient
source; 0 unauthorized doctor rows and 0 missing tokens on both patient-bearing
tables. Existing policy/authentication regressions passed (486 checks total in
the full run). All 622 unit tests, lint/format and policy-drift checks passed.
```
