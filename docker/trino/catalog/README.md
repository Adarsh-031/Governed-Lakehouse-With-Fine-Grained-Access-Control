# docker/trino/catalog

Iceberg connector configuration targets Trino 476 and Lakekeeper v0.14.0.
Use the private Iceberg REST endpoint and the release-specific connector, S3,
policy, and listener references in [ADR-001](../../../docs/architecture.md#version-specific-implementation-contract).
Runtime configuration is not implemented yet.
