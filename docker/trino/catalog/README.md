# docker/trino/catalog

Iceberg connector configuration targets Trino 476 and Lakekeeper v0.14.0.
Use the private Iceberg REST endpoint and the release-specific connector, S3,
policy, and listener references in [ADR-001](../../../docs/architecture.md#version-specific-implementation-contract).
`iceberg.properties` is mounted as `/etc/trino/catalog/hospital.properties`, so
the SQL catalog remains `hospital`, matching the generated client defaults and
the Lakekeeper warehouse. It uses `http://catalog:8181/catalog` and Keycloak's
private token endpoint with Trino's own client credentials.

`iceberg.security=SYSTEM` delegates connector authorization to the system file
access control configured by `../access-control.properties`; its mounted
`policies/rules.json` supplies row filters and column masks. This is separate
from `iceberg.rest-catalog.security=OAUTH2`, which authenticates the engine to
Lakekeeper. See the [Trino 476 authorization contract](https://github.com/trinodb/trino/blob/476/docs/src/main/sphinx/connector/iceberg.md#authorization).

Native S3 uses the private Silo endpoint, path-style access, and Trino's read-only
S3 key. Vended credentials are explicitly disabled so the catalog cannot replace
that identity with its more privileged ETL key. This follows ADR-003.

See [Trino operations and verification](../README.md).
