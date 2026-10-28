# tests/access

Positive and negative authorization tests using separately authenticated users.

The oracle is [access-matrix.csv](../../policies/access-matrix.csv), with exact
semantics and examples in the [access contract](../../policies/README.md).
`tests/unit/test_access_matrix.py` checks the specification without Docker;
it does not prove Trino enforces these clinical permissions. #18 and later
runtime tests must cover all permitted/denied group/table/column combinations,
both doctors' independently saved patient sets, UTC assignment boundary dates,
NULLs and mask types/outputs, hidden columns in predicates/joins, SELECT *,
metadata/time travel, and forbidden operations. Future audit/identity warehouses
need their own service-boundary checks before any SQL grant is enabled.
