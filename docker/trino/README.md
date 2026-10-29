# Authenticated Trino query boundary

Issue #13 implements Trino 476 as the only published service, at
`127.0.0.1:8443`. All services join the private `backend` network; only Trino
additionally joins `frontend` so Docker can publish its loopback HTTPS port.
Storage, Lakekeeper, databases, Keycloak, and OpenFGA publish no host ports.
HTTP is disabled, password authentication requires TLS, and forwarded headers
are not trusted. The local CA must be trusted explicitly by clients.

## Start and connect

From the repository root:

```sh
./scripts/init_secrets.sh
export TRINO_UID="$(id -u)" TRINO_GID="$(id -g)"
docker compose --env-file data/secrets/runtime.env build catalog-init
docker compose --env-file data/secrets/runtime.env up -d --wait trino
```

The UID/GID overrides let the unprivileged runtime read mode-0600 host secrets;
the default is the pinned image's UID/GID 1000. Keep secret permissions private.
Trino waits for successful catalog bootstrap, which waits for the private
catalog, authentication, migrations, and storage setup.

Use a Trino 476 CLI with the generated password for `doctor_alice`:

```sh
trino --server https://localhost:8443 --user doctor_alice --password \
  --truststore-path data/secrets/ca.crt --execute 'SELECT current_user, current_groups()'
```

With the CLI bundled in the container, use `docker compose --env-file
data/secrets/runtime.env exec trino trino` followed by the same arguments,
replacing the truststore path with `/etc/trino/auth/ca.crt`.
Do not disable certificate verification. Passwords live only in the ignored
runtime env file and hashed password database; do not paste them into shell
commands, logs, or commits.

`doctor_alice` and `doctor_bob` are the existing doctor identities generated in
issue #4; no secret rotation or extra `doctor_1` account is needed. Static
`auth/groups.txt` maps the five existing identities to their reviewed group names.
The committed `policies/rules.json` now grants only the clinical contract's SELECT
permissions (#18). Doctors receive care-team patient filters; researchers receive
ZIP/month masks and hidden fields; billing receives claims without free text;
admin has verification reads without identity or restricted audit text. See
[policy implementation](../../policies/IMPLEMENTATION.md).
The previous empty legacy `principals` section rejected even a principal using
its own username. It is removed in favor of Trino's authenticated user mapping
and the existing empty impersonation rules, which continue to deny changing users.

## Runtime decisions

The [Iceberg catalog](catalog/README.md) is exposed as SQL catalog `hospital` to
preserve the existing client contract. Lakekeeper OAuth2 authenticates the
Trino service; `iceberg.security=SYSTEM` makes system `rules.json` responsible
for hospital-user SQL permissions, filters, and masks. These are separate
security settings. Keep Trino's scoped read-only S3 keys and disable credential
vending, as required by ADR-003.

This is a single coordinator/worker. Its internal address and discovery URI use
loopback HTTPS with the same local CA and generated internal shared secret.
The root filesystem and configuration/secret mounts are read-only. Ephemeral
data and temporary directories are tmpfs. A separate executable tmpfs at
`/data/trino/native` allows JNA/OSHI to load JVM native libraries; the general
temporary/data directories remain non-executable. The local JVM heap is capped
at 1 GiB rather than the image default of 80% of host RAM. This is a small
experiment baseline, not a sizing recommendation for larger workloads.

The health check validates the HTTPS certificate at public `/v1/info` and waits
for `starting=false`. The listener can open before authenticators are loaded,
so HTTP 200 alone is insufficient for readiness. The acceptance test separately
verifies query login.
Worker catalog announcements propagate asynchronously after startup; the
acceptance test retries only `NO_NODES_AVAILABLE` for up to 30 seconds before
checking the exact filtered/masked result.
File rules and a second built-in read-only controller must both approve access.
The latter closes Trino 476's file-policy gap for table procedures. After pulling
#18, recreate Trino with `up -d --wait --force-recreate --no-deps trino` to add
the new controller mount; a restart alone does not change container mounts.
For later policy/group edits, validate with `uv run --locked python scripts/policy.py`
and restart Trino to apply them deterministically. Freeze the policy with
`--experiment-dir results/raw/<run>/policy` before each experiment.
HTTP inside the private storage/catalog/OIDC stack remains the local limitation
described in ADR-003.

## Acceptance tests

```sh
RUN_TRINO_INTEGRATION=1 uv run --locked --group dev \
  pytest tests/integration/test_trino.py -v --strict-config
```

The test creates an isolated Compose project with fresh secrets, a separate CA,
and an ephemeral loopback HTTPS port. It removes only its own containers and
volumes. Compose 2.24.4+ is required for the test override's `!override` port list.

It checks CA-verified CLI and statement-protocol login; HTTP 401 for wrong,
missing, and unknown-user credentials; query-level `PERMISSION_DENIED` for
impersonation; server group resolution; denial of Iceberg access with the exact
committed policy; and
denial of cleartext requests and private-service host publication.

It then creates a test-only policy on the existing two-row synthetic PyIceberg
table: the doctor sees only `[1, "redacted"]`, the admin sees both original
rows, and researcher SELECT and doctor DELETE are refused. This proves the
actual Trino Iceberg connector applies system file filters/masks while reading
Lakekeeper metadata and Silo Parquet. The committed policy is never broadened.
Clinical coverage is in `RUN_POLICY_INTEGRATION=1 uv run --locked --group dev
pytest tests/integration/test_policy.py -v`. It runs the exact committed rules
against ingested data and independent date/NULL/mask fixtures; see
[the decisions and verification record](../../docs/issue-18.md). Audit delivery
remains separate work. CI runs both tests alongside storage/catalog checks.
