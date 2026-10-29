# tests/access

Positive and negative authorization tests using separately authenticated users.

The oracle is [access-matrix.csv](../../policies/access-matrix.csv), with exact
semantics and examples in the [access contract](../../policies/README.md).
`tests/unit/test_access_matrix.py` checks the specification without Docker;
it does not prove Trino enforces these clinical permissions.

`RUN_POLICY_INTEGRATION=1 uv run --locked --group dev pytest tests/integration/test_policy.py -v`
executes the committed #18 policy on real Iceberg data. It covers the complete
matrix, both saved doctor sets, independent UTC boundary/duplicate fixtures,
NULLs and mask types/outputs, hidden column references, star projections,
joins/CTEs, metadata/time travel, impersonation, and forbidden operations.
`POLICY_TEST_SOURCE` selects an alternate generated Synthea source. The fixture
records the exact policy manifest alongside its private oracles and results.

Audit/identity alias fixtures test SQL permissions without broadening production
service grants. Audit still needs a reviewed production read path; the separate
schema/ingestion tests verify the real isolated identity warehouse boundary.
