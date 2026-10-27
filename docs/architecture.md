# Architecture

Shared protected base tables; optional role views. Trino is the user query boundary.
ETL and audit writers are separate trusted service paths. Private storage/catalog,
TLS authentication, explicit policies, and separate secrets are required.

## ADR-001: REST catalog and service versions

Date: 2026-10-27. Status: accepted for the initial local experiment.
Addresses [issue #1](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/1).

### Context and alternatives

The catalog must support Trino's Iceberg REST connector, authenticated service
identities, and durable metadata across container recreation. Row filters and
column masks remain Trino's responsibility.

- **Apache Iceberg REST fixture:** implements the reference REST API and can use a
  JDBC backend. Useful for connector tests, but its test-fixture server is not our
  deployment/authentication boundary. Selecting it would leave us to design that
  boundary. See the [fixture source](https://github.com/apache/iceberg/blob/apache-iceberg-1.10.0/open-api/src/testFixtures/java/org/apache/iceberg/rest/RESTCatalogServer.java)
  and [compatibility kit](https://github.com/apache/iceberg/blob/apache-iceberg-1.10.0/open-api/README.md).
- **Lakekeeper:** supports Trino via Iceberg REST, OIDC machine identities,
  OpenFGA catalog permissions, and PostgreSQL persistence. Its
  [tagged authenticated example](https://github.com/lakekeeper/lakekeeper/blob/v0.14.0/examples/access-control-simple/docker-compose.yaml)
  supplies a concrete integration baseline. Costs: PostgreSQL, an identity
  provider, and an authorizer must also be operated.
- **Nessie REST:** also supports [Trino via Iceberg REST](https://projectnessie.org/nessie-latest/trino/),
  [OIDC authentication](https://projectnessie.org/nessie-latest/authentication/), and
  [persistent version stores](https://projectnessie.org/nessie-latest/configuration/).
  It is a viable alternative; its branch/merge model adds concepts this experiment
  does not need. Use the Iceberg REST API, rather than confusing it with Nessie's
  separate native API, if reconsidered.

### Decision and exact images

Choose **Lakekeeper with PostgreSQL**, Keycloak for OIDC client credentials, and
OpenFGA for catalog authorization. Keep catalog permissions limited to trusted
Trino, ETL, and audit-writer service identities. Hospital users authenticate to
Trino; a shared Trino catalog identity does not carry their row/column permissions.

The image inventory is also recorded in `x-images` in
[compose.yaml.example](../compose.yaml.example):

- Trino: `trinodb/trino:476`.
- Object storage (Silo, a maintained MinIO fork):
  `pgsty/silo:RELEASE.2026-09-03T13-18-01Z`.
- REST catalog and migration job: `quay.io/lakekeeper/catalog:v0.14.0`.
- Catalog, Keycloak, and OpenFGA databases: `postgres:17.11-bookworm`, with
  separate databases, credentials, and persistent volumes.
- OIDC provider: `quay.io/keycloak/keycloak:26.0.7`.
- Catalog authorizer and its migration job: `openfga/openfga:v1.14.0`.

Reuse the storage image's bundled `mc` for bucket setup. No extra unversioned
curl/helper image is required. The project audit collector/writer has no image
yet; pin its base image and build revision when implemented. Optional dashboard
and notebook containers are not part of this service selection.

The Lakekeeper [v0.14.0 release](https://github.com/lakekeeper/lakekeeper/releases/tag/v0.14.0)
and its tagged example are the compatibility evidence: that example pairs Trino
476, this Silo release, PostgreSQL 17, Keycloak 26.0.7, and OpenFGA 1.14.
We replace its development catalog tag with v0.14.0 and narrow OpenFGA to 1.14.0.
PostgreSQL 17.11 is a patch update within the same major version, selected for its
[published fixes](https://www.postgresql.org/docs/17/release-17-11.html); compatibility
of that patch choice is an inference pending the local smoke test.

**Storage deviation from issue #1:** upstream MinIO is now
[archived and distributed as source only](https://github.com/minio/minio/blob/master/README.md).
On 2026-10-27, manifest checks for the older example's official Docker Hub tag
returned access denied, and the same release on Quay returned no manifest.
Choose [Silo](https://github.com/pgsty/silo), which preserves S3/STS compatibility
and is used in Lakekeeper v0.14.0's tagged example, rather than pinning an
unavailable image or adding a project-owned MinIO build. This is an explicit
change of storage distribution; deployment must still verify its behavior.

These are a fixed upstream-example baseline, not a claim that they are the newest
or security-approved releases. Review release/security changes before deployment
and update the set together if needed. Exact tags prevent silent version upgrades;
record pulled image digests with experiment results for byte-level reproducibility.
Registry manifest checks on 2026-10-27 confirmed all six selected image tags are
available for Linux amd64 and arm64. This checks availability, not interoperability.

### Version-specific implementation contract

Use the **Trino 476** source/docs when implementing policies and audit mapping;
the moving `current` documentation can describe different properties and defaults.

- REST connector: `connector.name=iceberg`, `iceberg.catalog.type=rest`, a private
  Lakekeeper URI ending in `/catalog`, and an explicitly bootstrapped warehouse.
  Configure `iceberg.rest-catalog.security=OAUTH2`, credentials, and an explicit
  `iceberg.rest-catalog.oauth2.server-uri` pointing to the IdP token endpoint.
  See [476 REST catalog properties](https://github.com/trinodb/trino/blob/476/docs/src/main/sphinx/object-storage/metastores.md#iceberg-rest-catalog).
- Storage: 476 uses `fs.native-s3.enabled=true`, with `s3.endpoint`, `s3.region`,
  and `s3.path-style-access=true` for Silo's S3 API. Scope each service's storage access
  separately; never distribute storage/catalog credentials to hospital users.
  See [476 S3 properties](https://github.com/trinodb/trino/blob/476/docs/src/main/sphinx/object-storage/file-system-s3.md).
- Policies: validate `filter`, `filter_environment`, column `mask` and
  `mask_environment`, first-match ordering, and omitted-section defaults against
  [476 file access control](https://github.com/trinodb/trino/blob/476/docs/src/main/sphinx/security/file-system-access-control.md).
  Existing `policies/rules.json` remains an unvalidated starting configuration.
- Audit: 476 includes the HTTP listener (`event-listener.name=http`),
  `http-event-listener.log-created`, `log-completed`, `connect-ingest-uri`,
  `connect-http-headers`, and bounded retry settings. Register the properties file
  with `event-listener.config-files`; both query event switches default to false.
  Check [476 listener properties](https://github.com/trinodb/trino/blob/476/docs/src/main/sphinx/admin/event-listeners-http.md)
  and the [476 event SPI](https://github.com/trinodb/trino/tree/476/core/trino-spi/src/main/java/io/trino/spi/eventlistener)
  before mapping native payloads to `audit/event-contract.json`. Delivery,
  authentication-failure coverage, and normalized fields still require testing.

### Consequences and validation boundary

Persist S3 objects, all database state, and the audit spool independently.
Run catalog/authorizer migrations before serving traffic; retain Lakekeeper's
secret-encryption key outside git across restarts. Catalog, storage, databases,
authorizer, and audit ingestion stay private. Configure TLS, authenticated access,
and explicit grants; do not copy the upstream example's demo secrets, public
ports, or development-mode authentication settings.

Issue #1 records selection and version contracts only. `compose.yaml.example`
still has no running services. Before claiming local compatibility, bring up the
secured stack, create/write/read a small Iceberg v2 table through the REST catalog,
recreate containers while preserving volumes, and verify the table remains
readable. Then test identity isolation, row filters/masks, and created/completed
audit delivery. Record the versions/digests and results; rerun these checks when
changing a pin. None of these runtime/security checks has been performed yet.
