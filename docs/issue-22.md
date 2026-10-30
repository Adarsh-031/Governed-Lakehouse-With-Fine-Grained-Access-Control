# Issue #22 — audit collector outage reliability

Issue: [Audit reliability tests: retries, restarts and outages](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/22).

## Measurement

The `audit_collector_outage` case in `tests/bypass/test_bypass.py` is part of the
existing opt-in Docker bypass suite. It first verifies normal delivery with a baseline
query, stops the real collector, issues three known Trino queries during the outage,
restarts the collector using the same bind-mounted spool, and reconciles the issued IDs
against `QueryCreated` and `QueryCompleted` rows in the audit table.

Query-level coverage requires both lifecycle event types. Missing event pairs,
duplicate rows, unexpected IDs, and invalid records are measured separately by
`audit/reliability.py`. Evidence is written to the private run directory under
`results/raw/bypass-<timestamp>-<id>/audit_collector_outage.json`; the stable
`tests/bypass/cases.csv` inventory remains `not_run` by design. The evidence records
query IDs, normalized audit metadata, counts, and UTC outage boundaries, not SQL text or
credentials.

## Outage behavior and limitations

- Events acknowledged before the collector stops have been fsynced to the host-mounted
  spool and survive a collector container restart.
- While the collector is stopped, it cannot acknowledge new deliveries. Trino's HTTP
  listener retries 5xx responses with bounded exponential backoff, but refused
  connections are not retried. A stop after Trino has started can therefore create a
  delivery gap.
- After restart, deliveries that Trino retries successfully can be recovered. Events that
  never reached durable collector storage and are not retried before the listener retry
  window expires are lost; the reconciliation output makes that loss visible.
- The batch writer deduplicates `(query_id, event_type)`; at-least-once transport must not
  create duplicate audit-table rows.

## Run

```sh
RUN_BYPASS_INTEGRATION=1 uv run --locked --group dev pytest \
  tests/bypass/test_bypass.py -k audit_collector_outage -v --strict-config
```

The test is intentionally a measurement, not an assertion that every event must recover.
A successful test run records the observed coverage and may classify a measured delivery
gap as a reliability finding. No measured percentage is hard-coded or claimed until the
Docker scenario has actually run.
