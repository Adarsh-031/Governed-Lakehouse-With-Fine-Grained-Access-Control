# tests/unit

Unit tests for token consistency, classification, and audit normalization.

`test_ingestion.py` validates Synthea mappings, exact types/cents, fixed-date age
bands, token reuse, UTC normalization, source integrity, referential checks,
charge/payment reconciliation, schema drift, safe retries, launcher arguments,
and secret-safe errors. The Docker counterpart is
`RUN_INGEST_INTEGRATION=1 uv run --locked --group dev pytest tests/integration/test_ingestion.py -v`;
see [ingestion verification](../../ingest/README.md#verification) for the full 1k run.
