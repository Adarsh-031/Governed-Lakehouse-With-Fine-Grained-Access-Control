# Audit collector (issue #20)

`audit/collector.py` is the first durable hop of the audit path:

```
Trino HTTP event listener --(Bearer token, internal network)--> collector
    --> fsync'd file per event in the restricted spool --> 200
    --> batch writer (#21): normalize + dedupe (#8) --> Iceberg audit table
```

## Guarantees

- **Authenticated.** `POST /v1/events` requires `Authorization: Bearer <AUDIT_INGEST_TOKEN>`,
  compared in constant time. Missing or wrong tokens get `401` and nothing is written.
  The token comes from `scripts/init_secrets.sh` (`data/secrets/runtime.env`); the
  collector refuses to start without one of at least 32 bytes.
- **Persist before acknowledge.** Each event is written to a temporary file, `fsync`ed,
  atomically renamed to `<record_id>.json`, and the spool directory is `fsync`ed before
  the `200` response. A failed write returns `503`, so Trino's listener retries it.
- **Restart-safe.** Acknowledged files are never rewritten. On start-up, leftover
  `.incoming-*` files (never acknowledged) are removed. Record ids are
  `<epoch-ns>-<uuid4>`, so sorted file names give arrival order.
- **At-least-once, not exactly-once.** A retry after a lost response can store the same
  event twice. The batch writer deduplicates on `(query_id, event_type)` (#8).
- **Private.** Spool directory `0700`, files `0600`. Bodies contain raw SQL, so the
  collector never logs them, disables the access log, and exposes no API docs.
- **Bounded.** Bodies over `AUDIT_MAX_BODY_BYTES` (default 1 MiB) get `413`; non-JSON
  or non-object bodies get `400`.

Spool record format:

```json
{"record_id": "...", "received_timestamp": "2026-10-30T13:40:00.000000+00:00", "event": {"...": "native Trino event"}}
```

## Running

Compose runs it as `audit-collector` on the internal `backend` network only (no host
port), from the shared catalog-tools image, with the spool bind-mounted from
`data/audit-spool/` (override with `AUDIT_SPOOL_HOST_DIR`). Run it as your host user so
it can write that directory:

```sh
AUDIT_UID=$(id -u) AUDIT_GID=$(id -g) \
  docker compose --env-file data/secrets/runtime.env up -d --build audit-collector
```

Local development without Docker:

```sh
set -a; . data/secrets/runtime.env; set +a
AUDIT_SPOOL_DIR=data/audit-spool uv run --no-sync python -m uvicorn \
  --factory audit.collector:create_app_from_environment --port 8090 --no-access-log
```

Trino is pointed at `http://audit-collector:8090/v1/events` with the bearer header in
#21 (HTTP event listener, created + completed events, with retries).

## Tests

`uv run --no-sync pytest tests/unit/test_audit_collector.py -v` covers: unauthenticated
rejection, fsync and rename happening before the response, no acknowledgement or partial
file on write failure, restart keeping every acknowledged event, malformed/oversized
bodies, file permissions, and refusing a weak token.

Manual check on 2026-10-30: three authenticated events stored, the collector was killed
with `SIGKILL`, and all three files were present and healthy after restart.
