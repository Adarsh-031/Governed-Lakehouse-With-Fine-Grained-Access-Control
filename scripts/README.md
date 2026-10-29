# scripts

Setup, ingestion, policy validation, tests, and benchmark orchestration.

## Acquire reproducible source data (issue #3)

`./scripts/generate_synthea.sh --patients 1000 --seed 42` generates a pinned
Synthea 3.4.0 CSV sample; use `--patients 10000` or `--patients 100000` for scale.
Requires Bash, Python 3.11+, Java 17+, and HTTPS for the first JAR download.
`--java /path/to/bin/java` selects Java without changing your system runtime.
The seed, provider seed, UTC simulation date, configuration and single worker
are fixed. Only the seven MVP source CSVs are retained.

`./scripts/download_mimic_demo.sh` downloads only the public MIMIC-IV Clinical
Database Demo 2.2 subset, validates upstream checksums and patient/admission
references, and preserves the license/README. It requires Bash, Python 3.11+
and HTTPS, with no credentials or Python packages.

Both commands work from any working directory and stage before publishing.
Rerunning verifies matching counts and preserves the original sample. Conflicting
settings or modified CSVs fail; use `--output` for a new destination. CSVs and
manifests live under git-ignored `data/raw/`, with separate source directories.
The [data card](../docs/data-card.md) contains full reproduction instructions,
measured counts, license/citation details, and the bronze-to-silver boundary.
Run offline checks with `uv run --locked --group dev pytest tests/unit/test_source_data.py`.

## Initialize local secrets

Prerequisites: Bash, uv, Python 3.11+ (managed by uv), and OpenSSL 1.1.1+.
From the repository root, run:

```sh
./scripts/init_secrets.sh
```

The launcher installs the locked development dependencies, including Python
`bcrypt`; no Apache utilities or running containers are required. It resolves
the output directory from its own location, so it also works from another
working directory. It prints status only, never credentials.

Generated files, all ignored by Git:

- `data/secrets/runtime.env`: a 32-byte hex HMAC key with version `v1`, audit
  ingest token, Trino internal shared secret, and random Trino passwords for
  `admin`, `doctor_alice`, `doctor_bob`, `researcher`, and `billing`.
  It also contains separate ETL, audit-writer, and Trino S3 access/secret keys
  and OIDC client secrets, storage admin credentials, separate database
  passwords for catalog/Keycloak/OpenFGA, a Keycloak admin password, an OpenFGA
  preshared key, and Lakekeeper's secret-encryption key required by ADR-001.
  It also includes `CATALOG_BOOTSTRAP_OIDC_CLIENT_SECRET`, the separate catalog
  administrator service identity. Rerunning against an issue #4 secret set adds
  only this missing key; existing credentials and TLS/HMAC/encryption keys remain
  unchanged. It does not update secrets already imported into Keycloak.
- `data/secrets/password.db`: `username:bcrypt` records with cost 12 and the
  `$2y$` prefix documented by [Trino 476 password authentication](https://github.com/trinodb/trino/blob/476/docs/src/main/sphinx/security/password-file.md).
- `data/secrets/ca.key` and `ca.crt`: private local CA and self-signed CA
  certificate (10 years).
- `data/secrets/trino.key` and `trino.crt`: separate server key and CA-signed
  server certificate (1 year), with SANs for `localhost`, `trino`, `127.0.0.1`,
  and `::1`, plus server-auth usage.
- `data/secrets/trino.pem`: unencrypted server key followed by the server and
  CA certificates, suitable for [Trino 476's PEM keystore](https://github.com/trinodb/trino/blob/476/docs/src/main/sphinx/security/tls.md).

The directory is mode `0700` and generated files are `0600`. Temporary files
are private and removed after generation. The tracked `data/secrets/README.md`
is preserved. `.env.example` lists variable names with empty values; actual
values stay in the generated file. Load them for host-side tools from the root:

```sh
set -a
. data/secrets/runtime.env
set +a
```

`HMAC_KEY_HEX` encodes 32 random bytes: decode with `bytes.fromhex` before using
`patient_token`. Host paths in the env file are relative to the repository root;
future container configuration must map them to container paths explicitly.
For the storage Compose stack, pass `--env-file data/secrets/runtime.env`;
it wires only root credentials into storage and the eight storage variables
into the short-lived bootstrap/check containers. See the
[storage instructions](../docker/storage/README.md). Future services must likewise
receive only their own credentials. Mount `password.db`, `trino.pem`, and the
public `ca.crt` into Trino, read-only; distribute `ca.crt` to clients and configure
certificate verification. Keep the CA private key and `runtime.env` on the host.

Generation does not itself provision users, clients, databases, or grants. The storage
init job now consumes the S3 variables to create users and apply the policies in
`docker/storage/policies/`. The catalog stack provisions OIDC clients/databases;
Trino consumes the password hashes and server-managed `docker/trino/auth/groups.txt`.
These names are the project's bootstrap contract, not native configuration names
for every service.

## Preservation and rotation

A complete existing set is preserved byte-for-byte on rerun (permissions are
restricted again). A partial or empty set fails without modifying credentials;
restore the missing files from a private backup. Generation is staged before
publishing files, and concurrent invocations are serialized. Publishing uses
individual file replacements; an interruption during that final step can leave
a partial or mixed set that must be restored from backup.

For an explicit **full local reset**:

```sh
./scripts/init_secrets.sh --force
```

This replaces every generated credential, the CA, and certificates. Preserve a
private backup before resetting an existing experiment. The HMAC change alters
patient tokens and needs a coordinated data migration and key-version update;
the Lakekeeper encryption-key change can make persisted catalog secrets
unreadable. Update provisioned service credentials and client CA trust together.
Do not use this command for routine certificate renewal or against an existing
stack without planning those migrations. No `.env` file is modified.

Check exclusion without displaying values:

```sh
git status --short --untracked-files=all
git check-ignore data/secrets/runtime.env data/secrets/password.db data/secrets/trino.pem
uv run --locked --group dev pytest tests/unit -v
```

## Load the silver tables (issue #16)

After `./scripts/init_secrets.sh` and Synthea generation, run `./scripts/ingest.sh`.
This builds and runs the trusted ETL container on the private backend and starts
its dependencies. The final JSON report verifies counts, referential integrity
and exact Iceberg readback. An identical rerun verifies existing rows; changed
data requires an explicit migration. Use `--validate-only` for source checks,
`INGEST_SOURCE_DIR` for another absolute source directory, and
`LAKEHOUSE_ENV_FILE` for another Compose env file. See the
[ingestion mapping, decisions and retry limits](../ingest/README.md#synthea-to-silver-issue-16).

The same command now loads `care_team` and the ETL-only restricted identity
table (#17). It saves each doctor's expected token set privately under
`data/staged/issue17/care-team-oracle.json`; `INGEST_EVIDENCE_DIR` chooses a
different private directory. Optional `--assignment-seed`, `--valid-from`, and
`--valid-to` select the reproducible synthetic assignment settings. See
[care-team rules and oracle handling](../ingest/README.md#care-team-and-restricted-identity-issue-17).
