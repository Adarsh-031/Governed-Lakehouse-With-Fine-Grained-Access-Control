# Audit pipeline: Trino events to the audit table (#21)

```
Trino 476 (HTTP event listener: created + completed)
  -> POST http://audit-collector:8090/v1/events   (bearer AUDIT_INGEST_TOKEN, backend network only)
  -> durable spool, one fsynced file per event      (audit/collector.py, #20)
  -> audit-writer: normalize -> dedupe -> append    (audit/writer.py)
  -> Iceberg table audit.query_events in the "audit" warehouse (s3://warehouse/audit/)
```

## Trino side

`docker/trino/http-event-listener.properties` is registered with
`event-listener.config-files`. Trino retries 5xx answers (the collector returns
503 when it cannot persist) with exponential backoff up to 6 times, but it does
not retry refused connections, so `trino` waits for a healthy `audit-collector`.
Split events are off.

## Writer pass

1. Read up to `AUDIT_WRITER_BATCH_SIZE` (default 500) committed `*.json` spool
   files, oldest first. The collector's `.incoming-*` temp files are ignored.
2. Normalize with `audit/normalize.py`. SQL text, literals, failure messages,
   plans and filter/mask expressions are never stored; policy version/digest come
   from the image's `policies/version.json`.
3. Quarantine to `<spool>/quarantine/` (with a `.reason` sidecar naming only the
   reason): unreadable JSON, invalid events, and conflicting duplicates.
4. Drop duplicates of `(query_id, event_type)` within the batch and against rows
   already in the table.
5. Append all new rows in one Iceberg commit, then delete the accounted-for spool
   files. If the append fails, nothing is deleted and the next pass retries; a
   crash after the commit is caught by step 4 on replay.

Run one writer per spool. The service loops every `AUDIT_WRITER_INTERVAL_SECONDS`
(default 5); logs carry counts only.

## No recursive auditing

The writer uses PyIceberg against the catalog as the `audit-writer` service and
never sends SQL to Trino, so writing audit rows cannot generate query events.

## Access

| Principal | Audit warehouse grants | Storage `audit/` |
| --- | --- | --- |
| audit-writer | describe, select, create, modify | list, get, put; no delete |
| etl, trino | none | none |
| Lakekeeper | registered with the audit-writer key | same |

The only delete the audit-writer key can perform is
`warehouse/audit/*/metadata/test`, the probe file Lakekeeper writes and removes
when it validates a new warehouse. Admin reads through Trino are not wired yet:
Trino's storage key cannot read `audit/` and Trino has no grant on the warehouse.
Inspect rows with:

```sh
docker compose --env-file data/secrets/runtime.env run --rm audit-writer --summary <query_id> ...
```

Append-only storage permissions are not tamper evidence; storage administrators
stay in the threat model.

## Run

```sh
export AUDIT_UID=$(id -u) AUDIT_GID=$(id -g)
docker compose --env-file data/secrets/runtime.env up -d --build trino audit-writer
```

## Tests

- `tests/unit/test_audit_writer.py`: batch logic with a fake sink (dedupe,
  replay, quarantine, failed append keeps the spool, schema matches contracts).
- `tests/integration/test_audit.py` (`RUN_AUDIT_INTEGRATION=1`): a real allowed
  and a real denied Trino query each land as created + completed rows with the
  right status, exactly once, including after a replayed delivery.
