# docker/storage

Private bucket provisioning, service permissions, and anonymous-access denial.

Selected storage is Silo, a maintained MinIO fork. The exact image and the reason
for using it instead of the unavailable official MinIO images are recorded in
[ADR-001](../../docs/architecture.md#decision-and-exact-images).
Runtime configuration is not implemented yet.
