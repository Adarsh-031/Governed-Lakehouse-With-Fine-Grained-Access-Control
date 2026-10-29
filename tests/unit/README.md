# tests/unit

Unit tests for token consistency, classification, and audit normalization.

`test_ingestion.py` validates Synthea mappings, exact types/cents, fixed-date age
bands, token reuse, UTC normalization, source integrity, referential checks,
charge/payment reconciliation, schema drift, safe retries, launcher arguments,
and secret-safe errors. The Docker counterpart is
`RUN_INGEST_INTEGRATION=1 uv run --locked --group dev pytest tests/integration/test_ingestion.py -v`;
see [ingestion verification](../../ingest/README.md#verification) for the full 1k run.

`test_policy.py` checks every matrix cell against the first effective rule,
explicit default denials, the reserved filter identity, generated-byte/digest
drift, invalid memberships, and preservation of per-experiment evidence. It is
complemented by the real-engine `tests/integration/test_policy.py` test.

`test_access_oracle.py` checks independent ZIP/month mask expectations and
rejects leaked, missing, substituted, and duplicate rows. Its real-engine
counterpart is `RUN_ACCESS_INTEGRATION=1 uv run --locked --group dev pytest
tests/access -v`; see [access acceptance](../access/README.md).
