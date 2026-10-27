# Issue #12: local catalog verification

Verified on 2026-10-27 with Docker Engine 29.9.2, Linux amd64. Test runner:
Python 3.12.13; the locked container client runs Python 3.11.14, PyIceberg
0.12.0, and PyArrow 26.0.0. No Trino server was involved in these checks;
the reader uses the service credentials reserved for Trino in #13.

## Live development warehouse

Lakekeeper, its dedicated Postgres, Keycloak/Postgres, OpenFGA/Postgres, and Silo
all reported healthy. The warehouse `hospital` was registered in the default
project at `s3://warehouse/silver/`. No host ports were published. Docker's
`5432/tcp` and similar image-exposed port labels are not host port bindings.

PyIceberg created namespace `catalog_smoke`, table `roundtrip` (Iceberg v2),
and appended these synthetic rows:

```json
[{"id": 1, "value": "lakekeeper"}, {"id": 2, "value": "silo"}]
```

Fresh ETL and Trino service clients loaded and read exactly those rows. The
Trino reader retained its own scoped S3 access key; its namespace creation and
metadata-only commit attempts were denied. Anonymous and invalid-token catalog
requests were rejected; the authenticated audit writer could not access the
clinical warehouse.

The development catalog was stopped, its Postgres container recreated with
`catalog_metadata` retained, and Lakekeeper recreated. Before bootstrap or
migrations were rerun, a read-only check returned the original rows and these
unchanged identifiers:

```text
table location: s3://warehouse/silver/01a17dd6-4344-7720-b8e8-e9159483d2a6
metadata: metadata/00001-01a17dd6-469c-7dc2-8c87-8ba1738c8593.gz.metadata.json
snapshot ID: 2725340821970301846
```

A subsequent `catalog-init` rerun and another read also passed without changing
the snapshot. This synthetic development table remains available for #13.

## Isolated acceptance and quality checks

```sh
RUN_CATALOG_INTEGRATION=1 RUN_STORAGE_INTEGRATION=1 \
  .venv/bin/pytest tests/integration -v --strict-config
```

Result: **2 passed in 100.97 seconds**. The catalog test used a fresh random
Compose project and credentials, bootstrapped twice, and created a unique
namespace/table. It recreated storage plus all three database containers,
then Keycloak, OpenFGA, and Lakekeeper, preserving their volumes. It compared
exact rows, namespace, format version, table location, metadata file, and
snapshot ID before any new initialization, then repeated bootstrap and read
again. Both isolated projects cleaned up only their own resources. The storage
test also rechecked #11's service S3 permissions, drift handling, and persistence.

`.venv/bin/pytest tests/unit -q`: **11 passed**. This includes additive secret
upgrade without rotating prior keys/certificates and configuration checks for
OIDC clients and the catalog trust boundary. Repository lint, formatting,
`git diff --check`, and `docker compose ... config --quiet` also passed.
The workflow now runs both Docker acceptance tests; these are local results,
not a claim that a remote CI run has completed.

## Pulled image digests

These are the registry digests reported locally for the pinned image tags:

```text
quay.io/lakekeeper/catalog:v0.14.0
  sha256:de87eaca929f208ec0ea4594706d3d70cec50620b62fea07c17e18fdb1a4c592
postgres:17.11-bookworm
  sha256:858bb5e98eed02eb8cafbcdc007f9a65d1b613972028944a8ec2133ccaf80d27
quay.io/keycloak/keycloak:26.0.7
  sha256:4388e2379b7e870a447adbe7b80bd61f5fbf04e925832b19669fda4957f05a81
openfga/openfga:v1.14.0
  sha256:f0d5591d7ddda326ac4b84dbc9d9192cb13e97f79957749006ef0774b0d818f6
pgsty/silo:RELEASE.2026-09-03T13-18-01Z
  sha256:b616a0cf8cb281e7e6bb3c9b1fb53875b4016a2878223925541c18f82d6c5ca3
python:3.11.14-slim-bookworm
  sha256:65a93d69fa75478d554f4ad27c85c1e69fa184956261b4301ebaf6dbb0a3543d
ghcr.io/astral-sh/uv:0.13.1
  sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336
```

The interoperability proof covers Lakekeeper/Postgres, service OIDC/OpenFGA,
PyIceberg, and Silo. Trino connector compatibility, hospital-user row filters/
masks, query audit delivery, and end-to-end TLS require their later issues.
