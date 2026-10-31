
# Governed Hospital Lakehouse

Status: private object storage and an authenticated Lakekeeper/Postgres catalog
are runnable alongside Trino 476 with HTTPS password login and system file
authorization. Docker acceptance tests cover Iceberg create/read, persistence,
and synthetic row-filter/mask enforcement. Reproducible Synthea CSV generation
and public MIMIC-IV demo acquisition are available. Synthea-to-silver Iceberg
ingestion includes seeded care-team assignments, saved doctor patient sets, and
ETL-only restricted identity. Clinical file policies implement role SELECT grants,
doctor care-team row filters, hidden columns, researcher masks and aggregate-only
researcher releases (`hospital.gold`, cells of at least 11 patients; see
[#46](docs/issue-46.md)). DDL is available
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
8. Check the policy and retain its digest with each experiment:
   `uv run --locked python scripts/policy.py --experiment-dir results/raw/run-001/policy`.
   After pulling #18, recreate Trino to add its second read-only controller mount:
   `docker compose --env-file data/secrets/runtime.env up -d --wait --force-recreate --no-deps trino`.
   Run clinical checks with `RUN_POLICY_INTEGRATION=1 uv run --locked --group dev pytest tests/integration/test_policy.py -v`.
   See [policy implementation](policies/IMPLEMENTATION.md) and
   [issue #18 decisions/viva notes](docs/issue-18.md).
   Verify the independent oracle with `RUN_ACCESS_INTEGRATION=1 uv run --locked
   --group dev pytest tests/access -v`; see [access acceptance](tests/access/README.md)
   and [issue #19 decisions](docs/issue-19.md).
   Run the bypass investigation with `RUN_BYPASS_INTEGRATION=1 uv run --locked
   --group dev pytest tests/bypass -v`; see [suite and evidence](tests/bypass/README.md)
   and [findings and decisions](docs/bypass-report.md). Residual researcher privacy
   risks are measured separately from authorization failures; the #22 audit outage
   case now reconciles known query IDs across a collector stop/restart.

## Build order
- [x] Choose catalog implementation and pin service versions (ADR-001).
- [x] Bring up private storage/catalog and authenticated Trino over TLS.
- [x] Generate a reproducible 1k Synthea source sample and acquire the public MIMIC-IV demo.
- [x] Load and validate patients, encounters, and charge-level claims.
- [x] Generate and load care_team assignments and restricted identity (#17).
- [x] Restrict identities; implement doctor filters and researcher masks/billing projections (#18).
- [x] Implement durable audit collection and query-ID reconciliation (#20-#22); fresh outage evidence is generated by the bypass test.
- [x] Verify authenticated access with independent doctor/mask oracles (#19).
- [x] Automate all bypass cases and report findings/reasons (#23), including the real audit collector outage case (#22).
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
synthetic Iceberg data. Clinical acceptance checks the complete access matrix,
source-derived doctor sets, date boundaries, mask outputs/types, metadata and
forbidden operations. Audit collection and batch writing are implemented; outage coverage and any missing deliveries are measured by the #22 Docker case rather than assumed lossless. Internal services use HTTP
on a private local Docker network; TLS currently covers client-to-Trino and
Trino's single-node internal communication.

Issue #5's [access contract](policies/README.md) now specifies every MVP
role/table/column decision, row scope, and mask output against an explicit
column inventory. Contract checks run in the unit suite; teammate sign-off is
pending. Issue #18 now compiles and enforces that contract; audit admin SELECT
is a reserved policy tested with isolated fixture data, pending its production
read path. Identity remains denied to every interactive role.

## Repository map
docker/: runtime configuration; ingest/: ETL and classification;
policies/: access contract and policy implementation gate; audit/: collection contract;
sql/: schemas, views, workloads; tests/: access and bypass checks;
bench/: experiment plan; notebooks/: analysis; dashboard/: optional demo;
docs/: submission documents; scripts/: orchestration; results/: evidence.
