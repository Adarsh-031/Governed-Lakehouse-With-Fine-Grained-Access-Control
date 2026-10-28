
# Governed Hospital Lakehouse

Status: private object storage and an authenticated Lakekeeper/Postgres catalog
are runnable alongside Trino 476 with HTTPS password login and system file
authorization. Docker acceptance tests cover Iceberg create/read, persistence,
and synthetic row-filter/mask enforcement. Reproducible Synthea CSV generation
and public MIMIC-IV demo acquisition are available. Synthea-to-silver Iceberg
ingestion is runnable; clinical policies remain to be implemented. Silver and restricted identity DDL is available
with isolated Trino/Iceberg acceptance coverage; see [schema setup](sql/schema/README.md).

## Goal
Shared synthetic hospital tables queried through Trino with authenticated
per-user row filters and column masks. Evaluate access restrictions, audit
coverage, residual leakage, and measured systems overhead.

## Architecture
Synthea -> Python ETL -> Iceberg tables on private S3-compatible storage.
Trino resolves tables through a private REST catalog and enforces policies.
Query events -> external durable collector -> batched audit Iceberg table.
Optional views and dashboard are consumers, not authorization boundaries.

Bronze: restricted raw exports (CSV objects; an Iceberg bronze layer is optional).
Silver: typed, validated shared tables with consistent patient tokens.
Gold: approved analytical fields/views; retain base-table restrictions.
Keep necessary clinical identity data in a separately restricted identity table.

## Local development
1. Install uv separately if needed, then run `uv sync --group dev`.
2. Commit the resulting uv.lock after reviewing resolved dependencies.
3. With Bash and OpenSSL installed, run `./scripts/init_secrets.sh` to generate
   all local credentials and TLS files under `data/secrets/`. Repeating the
   command preserves existing secrets. See [secret setup](scripts/README.md)
   for output files, loading variables, and explicit rotation.
4. Use the service image pins and catalog decision in
   [ADR-001](docs/architecture.md#adr-001-rest-catalog-and-service-versions).
5. Start private storage and initialize its bucket/service permissions:
   `docker compose --env-file data/secrets/runtime.env up -d --wait storage`, then
   `docker compose --env-file data/secrets/runtime.env run --rm storage-init`.
   See [storage setup and checks](docker/storage/README.md).
6. Build the private catalog tools, start the catalog and its authentication/
   database dependencies, register the warehouse, and prove Iceberg writes/reads:
   `docker compose --env-file data/secrets/runtime.env build catalog-init`,
   `docker compose --env-file data/secrets/runtime.env up -d --wait catalog`,
   `docker compose --env-file data/secrets/runtime.env run --rm --no-deps catalog-init`,
   `docker compose --env-file data/secrets/runtime.env run --rm --no-deps catalog-check`.
   See [catalog setup, decisions, and checks](docker/catalog/README.md).
   `compose.yaml.example` retains the complete image inventory.
7. Start the HTTPS query boundary with
   `docker compose --env-file data/secrets/runtime.env up -d --wait trino`.
   See [Trino setup, login, and policy verification](docker/trino/README.md),
   including secret-file UID/GID overrides when your host user is not UID 1000.
   Generate raw sources with `./scripts/generate_synthea.sh --patients 1000 --seed 42`
   (Java 17+), and optionally `./scripts/download_mimic_demo.sh` for the separate
   public clinical demo. See the [data card](docs/data-card.md) for reproduction,
   measured row counts, and larger populations. Load the sample with
   `./scripts/ingest.sh`; see the [ingestion mapping and checks](ingest/README.md#synthea-to-silver-issue-16).
8. Implement and run permission tests before expanding tables and data size.

## Build order
- [x] Choose catalog implementation and pin service versions (ADR-001).
- [x] Bring up private storage/catalog and authenticated Trino over TLS.
- [x] Generate a reproducible 1k Synthea source sample and acquire the public MIMIC-IV demo.
- [x] Load and validate patients, encounters, and charge-level claims.
- [ ] Generate and load care_team assignments and restricted identity (#17).
- [ ] Restrict identities; implement doctor filters and researcher/billing masks.
- [ ] Implement durable audit collection and query-ID reconciliation.
- [ ] Implement positive access and negative bypass tests.
- [ ] Run scale and equivalent-output policy-overhead benchmarks.
- [ ] Produce notebook, data card, threat model, bypass report, 3-5 page report.
- [ ] Record 3-minute demo and prepare 8-10 minute presentation.

## Scope and honesty
HMAC is pseudonymization, not reversible encryption or anonymization.
Generalization does not establish k-anonymity without measurement.
Append-only grants do not establish tamper evidence.
Query logs do not prove exactly which patient records a person viewed.
Secret-generation tests check local credentials, TLS, permissions, and reruns.
Storage acceptance tests check anonymous denial, service S3 permissions, bootstrap
reruns, and persistence. Catalog acceptance tests check authenticated PyIceberg
create/append/read, service grants, and unchanged data/snapshots after recreating
the catalog, storage, and all three databases. Trino acceptance tests check
CA-verified HTTPS authentication, wrong-password rejection, impersonation denial,
server-managed groups, private-service ports, and system row filters/masks on
synthetic Iceberg data. The committed policy still grants no data access;
clinical rules and audit delivery remain unverified. Internal services use HTTP
on a private local Docker network; TLS currently covers client-to-Trino and
Trino's single-node internal communication.

Issue #5's [access contract](policies/README.md) now specifies every MVP
role/table/column decision, row scope, and mask output against an explicit
column inventory. Contract checks run in the unit suite; teammate sign-off is
pending. This defines the target for #18 and does not enable clinical grants.

## Repository map
docker/: runtime configuration; ingest/: ETL and classification;
policies/: access contract and policy implementation gate; audit/: collection contract;
sql/: schemas, views, workloads; tests/: access and bypass checks;
bench/: experiment plan; notebooks/: analysis; dashboard/: optional demo;
docs/: submission documents; scripts/: orchestration; results/: evidence.
