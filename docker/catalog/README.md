# Private authenticated Iceberg REST catalog

Issue #12 adds Lakekeeper v0.14.0 with PostgreSQL persistence, Keycloak OIDC,
and OpenFGA authorization as selected in [ADR-001](../../docs/architecture.md).
All services attach only to the internal `backend` network and publish no host
ports. Internal HTTP is a local-host experiment boundary, not end-to-end TLS.

## Start and register

From the repository root, with Docker Compose v2+, uv, and OpenSSL installed:

```sh
./scripts/init_secrets.sh
docker compose --env-file data/secrets/runtime.env config --quiet
docker compose --env-file data/secrets/runtime.env build catalog-init
docker compose --env-file data/secrets/runtime.env up -d --wait catalog
docker compose --env-file data/secrets/runtime.env run --rm --no-deps catalog-init
docker compose --env-file data/secrets/runtime.env run --rm --no-deps catalog-check
```

`up ... catalog` waits for storage initialization, three separate Postgres
databases, Keycloak, OpenFGA database migrations, and Lakekeeper's database/
authorization-model migrations. Lakekeeper uses its own built-in healthcheck.
`catalog-init` uses a separate confidential OIDC bootstrap identity to initialize
the server once, register the warehouse, provision service users, and reconcile
their grants. Reruns preserve the warehouse ID, tables, and objects, check the
existing storage profile, and restore the managed service grants. A plain
`docker compose ... up -d` also runs the one-shot initializer; the opt-in
`catalog-check` job is excluded unless requested.

The helper image uses pinned Python/uv bases and `uv.lock`; no runtime package
downloads or published ports are needed. `.dockerignore` excludes generated
credentials and data from the build context. Helpers run as a non-root user
with a read-only filesystem. Only `catalog-init` receives the bootstrap secret;
neither check jobs nor the catalog runtime receive storage root credentials.

## Warehouse and credential contract for #13

- REST URI: `http://catalog:8181/catalog`.
- Warehouse: `hospital` in the default NIL project.
- Location: `s3://warehouse/silver/`, region `us-east-1`.
- S3 endpoint: `http://storage:9000`, path-style access.
- OAuth token endpoint:
  `http://keycloak:8080/realms/lakehouse/protocol/openid-connect/token`.
- Confidential OIDC clients: `etl`, `trino`, `audit-writer`, and the separate
  administrative `catalog-bootstrap`. Each uses its own generated
  `*_OIDC_CLIENT_SECRET`. Audience is `lakekeeper`; interactive/password grants
  are disabled for these clients.
- ETL has warehouse `describe`, `select`, `create`, and `modify` grants. Trino
  has only `describe` and `select`. The audit writer has no clinical warehouse
  grants; a future audit warehouse needs its own storage profile and grants.

The warehouse stores the scoped ETL S3 credential encrypted in catalog Postgres
using `LAKEKEEPER_ENCRYPTION_KEY`; it never stores the S3 administrator key.
Lakekeeper needs object writes for Iceberg metadata. Client-side writes use ETL
keys; reads for #13 use Trino's existing read-only S3 keys. STS vending and
remote signing are disabled so a read client cannot obtain the catalog's
broader storage authority. Configure #13 with static Trino S3 credentials and
disable its `iceberg.rest-catalog.vended-credentials-enabled` setting. These
trusted service grants do not implement hospital-user row filters or masks.

The `silver/` root deliberately fits #11's existing prefix policies. A bucket-root
warehouse would attempt unauthorized root operations and allow unwanted location
choices. Gold/identity/audit storage domains require separate warehouse profiles
and reviewed grants when added; no storage policy was broadened for this issue.

## Proof and persistence

`catalog-check` uses PyIceberg to create `catalog_smoke.roundtrip` in format v2,
append two synthetic rows with PyArrow, and reload/read the table with fresh ETL
and Trino clients. It checks exact rows, snapshot identity, and metadata/data
locations under `silver/`. Reruns read the existing table without appending
duplicates or replacing data. A read-only invocation creates nothing:

```sh
docker compose --env-file data/secrets/runtime.env run --rm --no-deps \
  catalog-check /app/check.py read
```

It also checks anonymous/invalid-token access, authenticated audit-writer denial,
and rejection of Trino namespace creation and metadata commits. To exercise a completely fresh stack
and prove persistence after replacing the containers:

```sh
RUN_CATALOG_INTEGRATION=1 uv run --locked --group dev pytest tests/integration/test_catalog.py -v
```

The acceptance test generates random credentials and a unique Compose project,
initializes twice, writes a unique namespace/table, stops consumers, recreates
storage and all three databases, then recreates Keycloak/OpenFGA/Lakekeeper.
Before rerunning any bootstrap or migration, it checks the same rows, table
location, metadata file, and snapshot ID. It then repeats bootstrap and reads
again. Cleanup deletes only that test project's containers, network, and volumes.
CI runs this alongside #11's storage acceptance test.

Development state lives in `warehouse`, `catalog_metadata`, `keycloak_metadata`,
and `openfga_metadata`. Retain all four volumes and generated secrets across
restarts; dropping auth state can orphan existing catalog identities/grants.
`docker compose ... down` preserves them; `down --volumes` destroys them.

Keycloak imports the realm only on its first initialization. Changing the env
file does not rotate imported client/admin secrets. Coordinate rotations through
Keycloak's private admin API and update the encrypted warehouse credential via
Lakekeeper's management API when changing ETL S3 keys. Preserve Lakekeeper's
encryption key with database backups; replacing it makes stored credentials
unreadable. `init_secrets.sh --force` is not a live-stack rotation procedure.

For health/status use `docker compose ... ps`. Never print resolved Compose
configuration, token responses, container environments, or request bodies.
The scripts deliberately report sanitized errors. See
[ADR-003](../../docs/architecture.md#adr-003-authenticated-persistent-catalog-and-silver-warehouse)
for decisions and [verification evidence](../../docs/catalog-verification.md)
for the recorded local run.
