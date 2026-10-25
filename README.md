
# Governed Hospital Lakehouse

Status: repository scaffold, not a running or security-validated platform.

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
3. Copy .env.example to .env and generate secrets locally; never commit them.
4. Select and pin compatible Trino/storage/catalog images.
5. Implement compose.yaml from compose.yaml.example, TLS, credentials, networks,
   health checks, and policies. The example is intentionally not runnable.
6. Ingest a small Synthea sample and validate IDs/foreign keys.
7. Implement and run permission tests before expanding tables and data size.

## Build order
- [ ] Choose catalog implementation and pin service versions.
- [ ] Bring up private storage/catalog and authenticated Trino over TLS.
- [ ] Load patients, encounters, claims, and care_team.
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
No automated security tests or infrastructure integration are implemented yet.

## Repository map
docker/: runtime configuration; ingest/: ETL and classification;
policies/: reviewed access matrix and rules; audit/: collection contract;
sql/: schemas, views, workloads; tests/: access and bypass checks;
bench/: experiment plan; notebooks/: analysis; dashboard/: optional demo;
docs/: submission documents; scripts/: orchestration; results/: evidence.
