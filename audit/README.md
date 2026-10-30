# Audit normalization

`audit/normalize.py` is a pure-Python normalizer for the JSON representations
of Trino 476 `QueryCreatedEvent` and `QueryCompletedEvent`. It does not start
a server, call Trino, write to object storage, or acknowledge HTTP deliveries.

## Scope

- Map native event metadata/context/statistics/IO/failure fields into
  [event-contract.json](event-contract.json).
- Classify lifecycle and terminal status as `running`, `success`,
  `sql_failure`, or `authorization_denial`.
- Deduplicate by `(query_id, event_type)`. Exact duplicate deliveries collapse;
  conflicting records with the same key fail closed. A changed receive timestamp
  alone does not create a conflict.
- Expose table and column identifiers when they match safe SQL identifier syntax.
- Never persist query text, literal values, failure messages, stack traces, plans,
  client addresses, or arbitrary session settings. When SQL text exists, the
  normalized `query_text_restricted` field contains only `[REDACTED]`.
- Accept policy version/digest as explicit caller inputs. Trino 476's native
  query events do not contain those policy fields, so they remain null unless
  the trusted collector supplies them.

## Trino 476 mapping

The fixtures under [tests/fixtures/trino](../tests/fixtures/trino) are sanitized,
representative JSON payloads shaped from the Trino 476 event SPI classes:
`QueryCreatedEvent`, `QueryCompletedEvent`, `QueryMetadata`,
`QueryContext`, `QueryStatistics`, `QueryFailureInfo`, `TableInfo`,
`QueryInputMetadata`, and `QueryOutputMetadata`. They are not claimed to be
captured from a running production cluster. The event SPI explicitly warns
that JSON serialization compatibility is not guaranteed across releases.

## Pure unit tests

```sh
uv run --locked --group dev pytest tests/unit/test_audit_normalize.py -v
```

Tests cover created/success/failure/authorization-denial mappings, output row
counts, timestamp and identity validation, omission of sensitive failure/plan
fields, SQL redaction, duplicate delivery, conflicting duplicate detection,
and keeping created/completed event types distinct.

## Boundaries

This is not yet a durable collector or a lossless audit pipeline. HTTP listener
delivery, authentication, retry/backoff, durable spool, replay, batch writes,
retention and query-ID reconciliation remain separate implementation work.
Query events cannot prove which patients a user actually viewed, and output row
counts do not prove that a client consumed results. Authentication failures need
separate logging.
