# docker/catalog

REST catalog configuration; private endpoint and separate service credentials.

Selected: Lakekeeper v0.14.0 with PostgreSQL persistence, Keycloak OIDC, and
OpenFGA catalog authorization. Exact images, compatibility evidence, and required
deployment checks are in [ADR-001](../../docs/architecture.md#adr-001-rest-catalog-and-service-versions).
Reuse the catalog image for migrations. Runtime configuration is not implemented.
