# Private object storage

Issue #11 implements the storage slice of the real `compose.yaml`. Silo is the
MinIO-compatible distribution selected in [ADR-001](../../docs/architecture.md#decision-and-exact-images).
The permission choices and their reasons are recorded in
[ADR-002](../../docs/architecture.md#adr-002-private-warehouse-bootstrap-and-service-storage-permissions).

## Start and initialize

Prerequisites: Docker Engine with Compose v2+ and generated local secrets.
From the repository root:

```sh
./scripts/init_secrets.sh
docker compose --env-file data/secrets/runtime.env config --quiet
docker compose --env-file data/secrets/runtime.env up -d --wait storage
docker compose --env-file data/secrets/runtime.env run --rm storage-init
docker compose --env-file data/secrets/runtime.env run --rm --no-deps storage-check
```

The first `up` waits for storage health; `storage-init` then creates the private
`warehouse` bucket and the ETL, Trino, and audit-writer users. Initialization can
be repeated without deleting existing objects. A plain `docker compose ... up -d`
starts storage and its one-shot init job; `storage-check` is opt-in via the `test`
profile or an explicit `run`. Future services should depend on successful init
completion as well as storage health.

The API is `http://storage:9000`, region `us-east-1`, with path-style S3 access,
available only to containers on the internal backend network. There are no host
ports, no public-network attachment, and no browser console. Host-side ETL tools
must eventually run on this network; do not publish the API to work around this.
HTTP is confined to this local Docker boundary; storage TLS is future work.

## Credential and location contract

- ETL: `ETL_STORAGE_ACCESS_KEY` / `ETL_STORAGE_SECRET_KEY`, list/read/write/delete
  within `s3://warehouse/bronze/`, `silver/`, `gold/`, and `identity/`.
- Trino: `TRINO_STORAGE_ACCESS_KEY` / `TRINO_STORAGE_SECRET_KEY`, list/read only
  within `s3://warehouse/silver/` and `s3://warehouse/gold/`.
- Audit writer: `AUDIT_WRITER_STORAGE_ACCESS_KEY` /
  `AUDIT_WRITER_STORAGE_SECRET_KEY`, list/read/write under `s3://warehouse/audit/`,
  with no object deletion. Read access is needed for Iceberg metadata.

Lists must specify a prefix including `/`; bucket-root listings are denied.
Region discovery is allowed. Writable prefixes support multipart upload/abort
and part-list operations. None of the service policies permits bucket creation,
administration, anonymous access changes, or access to another service's writable
domain. Static policies are under `policies/`; unlisted permissions are denied.
S3 users are trusted service identities, not hospital-user identities. Per-user
row filters and column masks still belong in Trino.

Root credentials go only to storage and privileged bootstrap/check containers.
Only the eight required storage variables go into those jobs; other runtime
secrets stay on the host. Client configuration/output is kept in private temporary
directories removed on exit. Avoid verbose `docker compose config`, container
environment inspection, `mc --debug`, or shell tracing because they expose secrets.

Bootstrap reapplies managed policies and removes anonymous access on every run.
It refuses extra direct policy grants or group memberships on managed users.
Resolve those grants as an administrator before rerunning. Keeping existing
access IDs while changing secrets updates the users on rerun. Changing access IDs
creates new users; separately revoke obsolete IDs during planned rotation.

## Verification and persistence

`storage-check` uses only synthetic objects in unique `.storage-check-*` paths,
then cleans up those exact objects. It checks known-object anonymous GET and
bucket listing both return HTTP 403, positive service operations work, and denied
requests reflect authorization errors rather than connectivity/missing-object
failures. It also checks cross-prefix denial, no Trino mutations, no audit deletes,
and no bucket/admin privileges. The test job is privileged because it must seed
and remove fixtures; its credentials must not be used by application services.

For a fully isolated acceptance test (also run in CI):

```sh
RUN_STORAGE_INTEGRATION=1 uv run --locked --group dev pytest tests/integration/test_storage.py -v
```

This creates random test-only keys and a unique Compose project, verifies fresh
and repeated initialization, runs permission checks, recreates storage without
initializing again, verifies a persisted object and IAM state, checks anonymous
policy reset and extra-grant refusal, and tears down its own containers/network/
volume. Ordinary unit tests do not require Docker.

Stop the development stack while retaining objects and IAM state:

```sh
docker compose --env-file data/secrets/runtime.env down
```

Do not add `--volumes` unless you intend to erase the warehouse and IAM state.
Never commit generated secrets or raw `docker inspect` output. For experiment
evidence, record only the image digest and sanitized test outcomes. These checks
cover storage permissions, not full-stack security, Iceberg compatibility, audit
immutability, or per-user query enforcement.
