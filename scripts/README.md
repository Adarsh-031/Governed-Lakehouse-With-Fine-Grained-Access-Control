# scripts

Setup, ingestion, policy validation, tests, and benchmark orchestration.

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
For a future Compose stack, pass `--env-file data/secrets/runtime.env` and wire
only the necessary secrets to each service. Mount only `password.db` and
`trino.pem` into Trino, read-only; distribute `ca.crt` to clients and configure
certificate verification. Keep the CA private key and `runtime.env` on the host.

Generation does not provision S3 users, OIDC clients, databases, grants, or
server-managed groups. Runtime service setup must consume these variables and
apply the separately reviewed permissions. These names are the project's
bootstrap contract, not native configuration names for every service.

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
