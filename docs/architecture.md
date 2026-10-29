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
  `policies/rules.json` grants no data access. ADR-004 validates its loading and
  the enforcement path with a separate synthetic filter/mask policy; clinical
  policy coverage remains future work.
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
changing a pin. Storage, catalog, and Trino checks below are implemented;
clinical policies, audit delivery, and broader bypass tests remain future work.

## ADR-002: Private warehouse bootstrap and service storage permissions

Date: 2026-10-27. Status: accepted for the local storage slice.
Addresses [issue #11](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/11).

Use ADR-001's pinned Silo distribution rather than changing the selected server
to upstream MinIO. `compose.yaml` attaches storage and its one-shot init/check
containers only to an `internal: true` backend network, with no published ports
and with the browser disabled. A named `warehouse` volume persists objects and
IAM state. Readiness uses the bundled curl and the server's readiness endpoint;
initialization waits for readiness rather than sleeping a fixed interval.

Create one private `warehouse` bucket and three distinct built-in IAM users from
issue #4's generated S3 keys. Keep root credentials in storage and the privileged,
short-lived bootstrap/check jobs; future ETL, Trino, and audit containers receive
only their own key pair. Do not mount the complete host env file into containers.
Reusing the pinned image's bundled `mc` avoids an additional helper-image pin.

Choose prefix isolation within the bucket to give each writer a separate domain:

- ETL can list/read/write/delete `bronze/`, `silver/`, `gold/`, and `identity/`.
  Deletion supports ingestion retries and Iceberg object cleanup. ETL is a trusted
  data-provisioning path and cannot access `audit/`.
- Trino can list/read only `silver/` and `gold/`. Query consumers need no raw
  exports, identity store, audit records, or S3 mutation rights. An eventual
  identity/audit query requirement needs an explicit policy review.
- The audit writer can list/read/write `audit/`, including metadata required for
  Iceberg commits, but cannot delete objects or access clinical prefixes.
- Each service can discover the bucket region. Lists require an allowed prefix;
  bucket-root enumeration, other prefixes/buckets, bucket creation, anonymous
  policy changes, and administrative APIs receive no grants. Multipart abort
  and part-list operations are confined to writable object prefixes.

The alternative of attaching a built-in bucket-wide readwrite policy is smaller
but would let ETL modify audit data and let the query engine change clinical
objects. Separate buckets would also isolate writers, but prefix policies retain
the single warehouse-bucket contract in issue #11. Future catalog/table locations
must stay under these approved roots; do not relax policies to accommodate random
locations or catalog-wide credentials without reviewing the boundary.

Initialization is rerunnable: preserve the bucket and objects, remove anonymous
access, reapply the named policies and supplied credentials, and refuse unexpected
extra direct policies or group memberships. It does not delete old IAM identities
when an access ID changes; revoke the previous key explicitly during coordinated
rotation. Clients must wait for successful `storage-init` completion, not merely
for storage health.

This is a local single-node experiment using HTTP inside the isolated Docker
network. Host/Docker administrators remain trusted and can inspect container
environments or the volume. Add storage TLS before extending this boundary beyond
the local host. No-delete audit permissions do not prevent overwriting existing
keys and are not immutability or tamper evidence. Audit retention/maintenance must
use a separately reviewed path; routine audit object deletion is deliberately
unavailable to its writer.

The opt-in acceptance test in `tests/integration/test_storage.py` checks readiness,
network configuration, anonymous bucket/object denial, allowed and forbidden
service operations, bootstrap reruns and privilege drift, and object/IAM survival
after container recreation. It uses an isolated Compose project and temporary
credentials and removes only its own resources. This verifies storage behavior;
it does not establish Iceberg/REST interoperability or per-user Trino enforcement.

## ADR-003: Authenticated persistent catalog and silver warehouse

Date: 2026-10-27. Status: accepted and locally verified for issue #12.

Implement ADR-001's Lakekeeper v0.14.0 pin with a dedicated PostgreSQL 17.11
database and `catalog_metadata` volume. Reuse the catalog image's `migrate`
command before `serve`; its migration also installs the OpenFGA authorization
model. Do not rely on process startup or a fixed sleep as readiness. The runtime
waits for healthy dependencies, successful migrations, and storage bootstrap,
and exposes its built-in healthcheck. Nothing in this slice publishes host ports.

Include Keycloak and OpenFGA now, rather than introduce an unauthenticated
catalog that #13 must later retrofit. Keycloak runs its normal `start` profile
with a separate persistent Postgres database; OpenFGA also has its own database
and migration job. A pre-shared API key authenticates Lakekeeper to OpenFGA,
using the already generated `OPENFGA_PRESHARED_KEY`; OIDC client credentials
authenticate ETL/Trino/audit services to Lakekeeper, with issuer and audience
validation. Persist identity and grant state independently so recreating the
catalog does not change service identities or lose authorization.

Use a dedicated `catalog-bootstrap` confidential client for server administration
and warehouse registration. ETL is not the instance administrator. Add its own
random secret to the existing secret generator; upgrading an existing generated
set only appends that missing variable, preserving all other credentials and
key material. Keycloak's import reads secrets from environment placeholders, so
no credentials are embedded in the committed realm. Import is initialization,
not a live credential-rotation mechanism.

Register the `hospital` warehouse in the default NIL project at
`s3://warehouse/silver/`, with Silo's private endpoint, region `us-east-1`, and
path-style S3 access. This establishes a silver warehouse for #13 without
loosening ADR-002's prefix policies. A root warehouse would require extra bucket
privileges and admit clinical/audit location overlap. Issue #17 now registers
the separate identity warehouse with ETL-only managed grants, described below.
Gold and audit remain future registrations with reviewed grants.

Lakekeeper stores the existing scoped ETL storage key encrypted in Postgres;
the catalog runtime receives no S3 administrator credentials. Sharing that key
with the catalog is an explicit trusted clinical-writer decision: the catalog
must write Iceberg metadata. It cannot access audit data or administer storage.
The ETL principal has catalog describe/select/create/modify grants; Trino has
describe/select only; the audit writer has no clinical warehouse grant. Managed
grants are reconciled on bootstrap reruns, with no table/object replacement.

Disable both STS vending and remote signing. With this small static service-key
model, vending or signing with the catalog's ETL authority could undermine
Trino's read-only S3 identity. Clients explicitly carry their own scoped storage
keys. #13 must disable vended credentials and use its existing Trino S3 keys.
Future temporary-credential vending requires a separate permissions review and
negative tests for storage authority. Hospital-user row filters and column masks
remain Trino's responsibility, independent of these service grants.

Use a non-root Python helper image based on pinned Python 3.11.14 and uv 0.13.1,
installing the project's locked runtime dependencies at build time. One helper
performs secret-safe authenticated bootstrap, another proves real PyIceberg
create/append/read. Both run on the backend with a read-only filesystem; only
bootstrap gets the catalog administrator secret. The build context excludes
host secrets and data. This avoids temporary host-port publication and adding a
notebook server simply to run a smoke check.

Internal HTTP (including OIDC token exchange and database transport) is confined
to this single-host private Docker boundary. It is an explicit local limitation;
extending the deployment requires internal TLS. No unauthenticated catalog,
interactive demo credentials, or public admin UI is enabled.

The acceptance test checks exact synthetic rows through fresh ETL and Trino
PyIceberg clients, denies Trino catalog mutations and unauthenticated/audit
clinical access, and recreates storage plus all three databases and their
consumers. It compares the table location, metadata location, snapshot ID, and
data before any post-recreation initialization. This verifies catalog,
identity/grant, and S3 persistence. It does not prove
the Trino connector, hospital-user policies, audit delivery, or end-to-end TLS.
See [catalog operations](../docker/catalog/README.md) and
[local verification evidence](catalog-verification.md).

## ADR-004: HTTPS Trino boundary and system Iceberg authorization

Date: 2026-10-27. Addresses issue #13.

Add the pinned Trino 476 coordinator/worker after successful `catalog-init`.
It is the only service on a frontend network and publishes only
`127.0.0.1:8443`; all service-to-service connections remain on the internal
backend. Docker 29 did not allocate a host port when Trino joined only an
internal network, so an additional frontend network is necessary for the
required host access. Catalog, storage, authentication, and databases remain
backend-only and publish no ports.

Terminate client TLS directly in Trino using issue #4's PEM and local CA, with
HTTP disabled, insecure HTTP authentication disabled, and forwarded headers
ignored. Reuse its bcrypt password file and existing five users. The example
doctor identity is `doctor_alice`, preserving generated credentials rather
than introducing `doctor_1` or rotating secrets. A file group provider supplies
server-managed group bindings; memberships do not themselves grant SQL access.

Remove the empty legacy `principals` policy section: it blocked even an
authenticated principal from using its own username. Use authenticated user
mapping and the explicit empty impersonation rules instead, following Trino
476's deprecation guidance for principal rules. Catalog/schema/table grants
remain empty. No clinical policy is introduced by infrastructure setup.

Use the private Lakekeeper REST endpoint, `hospital` warehouse, and Keycloak
client-credentials flow under the Trino service identity. Mount the source
`iceberg.properties` as `hospital.properties` to retain existing client catalog
defaults. Set `iceberg.security=SYSTEM`: the connector delegates authorization,
including row filters and column masks, to the system file access control
loading `policies/rules.json`. REST OAuth2 authenticates the service and does
not substitute for hospital-user authorization. Retain the scoped, read-only
Trino S3 key with native path-style S3 and explicitly disable credential vending,
as required by ADR-003.

Keep only Trino's own service secrets in its environment; mount the password
database, TLS PEM, and public CA read-only. Host UID/GID overrides preserve
mode-0600 secret files while running without root. The single-node internal
discovery/worker path uses loopback HTTPS, CA trust, and the generated shared
secret. This does not turn the catalog/OIDC/storage HTTP paths into TLS.

Use a read-only root filesystem, dropped capabilities, and tmpfs runtime data.
A dedicated executable tmpfs is required for JNA/OSHI native library loading;
general temporary/data mounts remain non-executable. Cap heap at 1 GiB for the
small local test rather than allocating the image default of 80% of host RAM.
This sizing and loopback internal address must be revisited for larger workloads
or multiple workers. Query audit delivery remains separate work.

The opt-in Trino acceptance test uses fresh credentials, CA, Compose project,
volumes, and an ephemeral loopback port. It checks CA-verified CLI and protocol
login, wrong/missing/unknown credentials, impersonation denial, server groups,
the committed no-data-grants baseline, and private-service publication. An
isolated policy copy then applies a doctor row filter and column mask to the
two-row synthetic Iceberg table, with an unfiltered admin control and negative
researcher/write checks. These checks verify the enforcement path; clinical
policies, bypass coverage, and audit collection require their own tests.

## Restricted identity and assignment provisioning (issue #17)

Bootstrap also registers `identity` at `s3://warehouse/identity/` with the
existing trusted ETL S3 key, matching ADR-002's approved prefix scope. ETL gets
describe/select/create/modify; the managed Trino and audit-writer principals get
no identity grants. Bootstrap validates the full storage profile and reconciles
managed grants on rerun. Vending and remote signing remain disabled. There is
no identity catalog in ordinary Trino and no expansion of its S3 permissions.

Trusted ETL loads minimal name/token rows there and synthetic assignments in
`hospital.silver.care_team`, using one validated HMAC patient map across all five
tables. A source-derived, private expected-membership file is frozen before
catalog access to support later independent authorization checks. The loader
preflights all existing clinical/identity tables before mutations and preserves
identical snapshots on retries; the commits are not a multi-table transaction.
Issue #18 implements the clinical SELECT/filter/mask rules, with a protected
filter reader and a second read-only system controller. See
[policy implementation](../policies/IMPLEMENTATION.md) and
[issue #18 decisions](issue-18.md). See the [issue #17 decision record and
validation evidence](issue-17.md) for the assignment rule, validity, oracle
handling, and explicitly privileged ETL/bootstrap boundary.
