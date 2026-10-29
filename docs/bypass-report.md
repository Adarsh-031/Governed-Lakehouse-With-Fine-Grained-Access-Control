# Bypass report — issue #23

The automated investigation covers every case in
[the inventory](../tests/bypass/cases.csv), including one added admin-operation
case. The [suite instructions](../tests/bypass/README.md) describe reproduction,
private evidence and status semantics. Per-run JSON/CSV is the authoritative
result; this document summarizes the implementation and measured limitations.

The validation run completed all 12 inventory cases: **9 blocked, 2 leaked,
1 inconclusive**, with no `not_run` cases. Private evidence is retained locally
under `results/raw/issue23-final/`, including 253 query IDs. The committed policy
was `clinical-v1`, digest
`3aae819ad48e8821b7d12912c381dfa4e0eaa170c5778a4a768ac3fe3f1b8063`.

## Findings

The committed two-patient Synthea source plus nine boundary fixtures produces
11 synthetic patients. The investigation found these residual privacy risks in
the permitted researcher outputs:

- Grouping age band, sex and masked ZIP produced 4 singleton groups among
  11 patients (36.36%). Exact count differencing exposed one selected patient's
  presence: total count minus count excluding that patient was 1.
- An attacker with exact auxiliary age band, sex and ZIP prefix could correctly
  link 3 of 11 synthetic patients to unique released tokens (27.27% population
  linkage rate). Five auxiliary records had complete QIs; all 3 unique matches
  were correct. Ambiguous matches and missing QIs did not count as identification.

Both cases retain `leaked` status even though their measurement tests succeed.
[Follow-up #46](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/46)
tracks both risks, the researcher privacy-contract decision and potential
mitigations. These results describe a small synthetic population under a strong
auxiliary-data model. They do not establish a real-world re-identification rate.

Audit outage loss remains `inconclusive`, with `missing_events: null` and a known
query ID. [Issue #21](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/21)
has not deployed the listener, collector or batch writer. No event-delivery or
losslessness claim can be made from the synthetic audit SQL fixture.

## What changed and why

**One executable case per CSV entry.** Collection compares the inventory against
the handler registry, so adding or removing a case cannot silently lose coverage.
Admin operations were added explicitly because the issue's plan includes them;
all five SQL users, including read-only admin, face the existing forbidden-write,
DDL, grant, session and procedure checks.

**Reuse independent expectations.** The disposable access fixture authenticates
direct Trino clients and verifies the committed policy and saved oracle hashes.
Doctor queries are compared with pre-write memberships and row multisets; masks
use independent Python semantics. Expectations never come from live care-team
queries or privileged query outputs. Existing policy permissions are unchanged.

**Use real positive controls.** Anonymous S3 GET/list probes target a known
Iceberg metadata object verified by an authenticated read. Catalog probes use
the server-provided warehouse prefix and a successful authenticated request to
the same clinical table endpoint. Backend tests run on the private network,
proving authentication/authorization even when network isolation is bypassed.
SQL negatives require `PERMISSION_DENIED`; syntax, missing-resource and transport
errors leave the attempt inconclusive and fail the test.

**Make historical snapshots genuinely old.** The trusted ETL fixture records
snapshot IDs before appending boundary rows. Tests prove they differ from current
IDs and verify source-only historical rows, doctor filters, researcher masks and
hidden-column denials. Current saved memberships still govern historical reads.

**Treat views according to the deployed contract.** The SELECT-only policy
blocks persistent view creation for every SQL identity. Tests also execute joins,
CTEs and derived views to prove those shapes do not expand doctor access. No
temporary grant or replacement policy is introduced to manufacture a view path.

**Keep execution success separate from disclosure status.** A completed risk
measurement may find a leak. An unexpected authorization success fails pytest;
known inference/linkage measurements remain visibly `leaked` with a follow-up.
An unavailable audit pipeline is `inconclusive`, never a fabricated zero-loss
measurement. Source inventory statuses stay `not_run`; only executed reports
carry measured outcomes. Filtered/skipped/unstarted cases retain a reason.

**Preserve evidence privately.** Every attempt records expected and observed
evidence, identity, query ID/error or HTTP request/status under `results/raw/`.
Policy version/digest and exact policy/config snapshots accompany independent
oracles. The reports are atomically replaced with private permissions, refuse
run-directory reuse and survive failed attempts. Full credential-bearing Compose
output and SDK error bodies are excluded. CI runs the suite explicitly and leaves
raw evidence in its private workspace rather than publishing patient-level files.

## Remaining work

Implement #21 and replace the outage dependency result with actual stop/restart,
known-ID reconciliation and measured missing-event counts. Resolve or accept the
researcher residual risks in #46, and repeat the measurements on larger seeded
synthetic populations. The broader threat model still includes administrators
and service-credential compromise, beyond ordinary SQL-user enforcement.

## Validation

- 496 Docker checks passed: 12 bypass cases plus all 484 existing access checks.
- All 641 unit tests passed, including 10 new bypass regressions.
- Repository Ruff lint and formatting checks passed; workflow/Compose YAML parsed.
- Default invocation skipped all 12 bypass cases without starting Docker.
- Filtered-out execution and deliberately missing-source setup both retained all
  12 cases as `not_run` with reasons, rather than manufacturing passes.
- All 13 evidence files were private (`0600` in the `0700` run directory), and
  scanning against generated test credentials found no credential matches.
