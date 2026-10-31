# Threat model — STRIDE

**Status:** Draft for the local Synthea-based experiment  
**Scope:** The current Trino/Iceberg lakehouse, identity/catalog/storage services, clinical access policy, and implemented audit collection path; fresh outage measurement remains opt-in.  
**Issue:** [#9 — Draft threat model (STRIDE)](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/9)

This is a living engineering threat model, not a penetration-test report, production security certification, or claim of HIPAA compliance. It distinguishes controls present in the repository from protections that are planned or not yet verified. The primary workload is synthetic Synthea data; synthetic patient histories do not establish that every bundled provider/reference attribute is fictional.

## 1. Security objectives and assets

The security objectives are to preserve confidentiality of patient and identity data, integrity of governed records and policy decisions, attributable operations where audit evidence exists, and availability of the query/control plane.

| Asset | Examples and security value | Main properties |
|---|---|---|
| A1 — Source and bronze data | Raw Synthea exports can contain names, source identifiers and detailed clinical/financial history. Bronze is not an interactive-user data domain. | Confidentiality, integrity |
| A2 — Governed Silver data and Iceberg history | Patient tokens, demographics, encounters, claims, care-team assignments, manifests, metadata and old snapshots. | Confidentiality, integrity, availability |
| A3 — Identity linkage and token material | The restricted identity.patient_identity table, source-to-token processing inputs, HMAC key and key version. | Confidentiality, integrity |
| A4 — Authorization configuration | policies/rules.json, policies/access-matrix.csv, schema contract, group bindings, policy version and digest. | Integrity, confidentiality, availability |
| A5 — Authentication and service secrets | Trino password database, TLS private key, CA material, Keycloak client secrets, OpenFGA key, database passwords, Lakekeeper encryption key and scoped S3 credentials. | Confidentiality, integrity |
| A6 — Catalog, authorization and storage state | Lakekeeper metadata, OpenFGA model/tuples, Keycloak realm/database, PostgreSQL volumes, Silo IAM and warehouse objects. | Integrity, availability, confidentiality |
| A7 — Audit and investigation evidence | Normalized event contract, query/event identifiers, policy metadata, private bypass reports and independent oracles. | Integrity, confidentiality, availability |
| A8 — Service availability | Trino HTTPS endpoint and its dependent catalog, identity provider, authorizer, databases and object store. | Availability |

The patient_token is a deterministic, versioned HMAC pseudonym. It supports consistent joins across approved tables and is therefore linkable. HMAC is **pseudonymization, not anonymization**; a key holder or an actor with suitable auxiliary information may link records. The key must remain outside Git and separate from ordinary query users. The restricted identity mapping is a separate ETL-only warehouse, not a view for doctors, researchers, billing or admin.

The current audit contract stores query-level context such as query ID, authenticated user, groups, table/column identifiers, row-filter/mask presence, output row count, status, error code, timestamps and caller-supplied policy version/digest. It redacts SQL text. These events do **not** enumerate every patient returned, prove that a human viewed a result, or capture authentication failures by themselves.

## 2. Actors and capabilities

| Actor | Intended access / trust assumption | Security concern |
|---|---|---|
| Doctor (doctor_alice, doctor_bob) | Interactive Trino SQL; row-filtered to active care-team patients on patients and encounters. | Stolen credentials, attempting another doctor's patients, changing group identity, or using joins/history to widen access. |
| Researcher | Interactive Trino SQL over MVP patients/encounters; birthdate and free text hidden, ZIP and encounter times generalized. | Aggregation, differencing and quasi-identifier linkage despite masks and pseudonyms. |
| Billing | Interactive Trino SQL over claims only; procedure code/cost/payer fields visible, procedure description hidden. | Reuse of financial access to infer clinical or identity information. |
| Admin | Interactive, read-only SQL over approved operational tables; not storage root, catalog bootstrap, or unrestricted identity access. | Mistaking “admin” for infrastructure superuser or using metadata/DDL paths to escalate. |
| Storage/host administrator | Controls Docker host, storage root, persistent volumes, service environments and backups. Trusted outside Trino row/column isolation. | Can inspect secrets/data, alter or replace containers and volumes, and bypass application-layer controls. |
| Outside attacker | No credentials or network privilege assumed. May probe endpoints, steal credentials, exploit a host/service, or use auxiliary data. | Anonymous object/catalog access, credential theft, service compromise, denial of service and re-identification. |
| ETL service identity | Trusted machine principal; reads approved source inputs and writes bronze/Silver/identity prefixes using scoped credentials. | Compromise can poison or replace data and identity linkage; it is more privileged than SQL users. |
| Trino service identity | Machine principal used by the Iceberg REST catalog and scoped object-store reader; does not replace the interactive user's SQL policy. | Compromise can expose granted prefixes or misuse catalog authority. |
| Audit writer / catalog bootstrap | Separate service principals with distinct purposes. Bootstrap administers catalog setup/grants; audit writer is intended for audit objects and has no clinical warehouse grant. | Credential confusion or secret theft can cross a service trust boundary. |
| Policy evaluation identity | Internal Trino identity used only to evaluate care-team row filters; not an interactive user. | Incorrect or exposed mapping could weaken doctor isolation. |

Role names and client-supplied headers are not trusted identity proof. Groups are bound by server-side configuration after authentication. A role is not entitled to another role's permissions merely because it can send a matching header or SQL statement.

## 3. Trust boundaries and data flows

The user query boundary is Trino HTTPS on loopback-published port 8443. The catalog, identity provider, authorizer, databases and object storage remain on the private Docker backend network and do not publish host ports. Internal service-to-service HTTP is confined to this single-host experiment; it is not end-to-end TLS. The host/Docker/storage administrator is outside that network boundary and remains trusted.

~~~text
                 UNTRUSTED CLIENT / OUTSIDE NETWORK
   doctor | researcher | billing | interactive admin | attacker
                              |
                    TLS + password authentication
                    server-managed user/group binding
                              |
                 +------------v-------------+
                 | Trino 476 (HTTPS :8443)  |
                 | SQL access control       |
                 | row filters / masks      |
                 +--+----------+---------+--+
                    |          |         |
          private backend      |         +---- scoped S3 read
                    |          |                (silver/gold)
           +--------v--+   +---v------+             |
           | Lakekeeper|<--| Keycloak |             v
           | REST/OIDC |   | OIDC     |       +------------+
           +-----+-----+   +----------+       | Silo / S3  |
                 |                           | prefixes   |
                 +-------- OpenFGA           +------------+
                 |
        +--------v----------------------------+
        | PostgreSQL metadata / auth state    |
        +-------------------------------------+

 TRUSTED SERVICE PATHS (separate identities and credentials)
 ETL -------- OIDC/catalog + scoped object access --> bronze/Silver/identity
 catalog-bootstrap -------- OIDC -------------------> catalog setup/grants
 audit-writer -------------- scoped object access --> audit prefix (intended)

 HOST / DOCKER / STORAGE ADMINISTRATOR:
 can administer the host, containers, secrets, databases and persistent volumes;
 this actor is outside the Trino and private-network enforcement boundary.
~~~

The main boundaries are:

1. **Human client → Trino:** TLS terminates at Trino. Trino authenticates a named user, maps that identity to a server-managed group, and applies file access control. Interactive users do not receive S3, Lakekeeper, Keycloak client, or database credentials.
2. **Trino → catalog/identity/authorizer:** Trino uses its own OIDC service identity to access Lakekeeper. Keycloak issues machine tokens and OpenFGA evaluates catalog grants. These service permissions authorize Trino as a catalog client; they do not replace hospital-user row filters and column masks.
3. **Service → object storage:** Silo is private and uses separate scoped service keys/prefix policies. ETL, Trino and audit-writer access differ. A bucket policy is not a substitute for SQL policy and vice versa.
4. **Raw source → governed tables:** ETL is a trusted transformation and tokenization path. Raw exports and identity linkage must not be copied into ordinary query domains. HMAC key custody and ETL correctness are critical assumptions.
5. **Query execution → audit evidence:** Native Trino events must be delivered, normalized, durably spooled and written to the audit store. The event schema alone does not prove this pipeline is complete or lossless; the outage case remains inconclusive until the real listener/collector/writer path is exercised.
6. **Host and operator boundary:** An administrator able to control Docker, host files, service environments, object-store root credentials or persistent database volumes can bypass application-level isolation. This experiment does not defend against a malicious root/host administrator.

## 4. STRIDE analysis

Threat IDs are stable references used by the case-to-threat map below. Controls marked as present describe repository configuration or the recorded bypass investigation; they are not a claim that every deployment has been independently audited.

| STRIDE | Threat | Control / mitigation in this repository | Residual risk and validation |
|---|---|---|---|
| S | **S-01: impersonate another hospital user or forge a role.** A doctor or attacker attempts to become another doctor/admin or supplies a forged group header. | TLS-authenticated Trino users, server-managed group provider, explicit user mapping, ignored forwarded headers, and no SQL impersonation grant. The bypass suite tries identity/password/header impersonation. | Stolen/reused passwords, endpoint compromise or a compromised host can act as the victim until credentials are revoked. Authentication failures are not yet captured by the query-event contract and need separate identity-provider/security logging. |
| S | **S-02: impersonate a trusted catalog/storage service or bypass its authentication boundary.** | Separate OIDC clients and generated secrets for bootstrap, ETL, Trino and audit writer; audience validation; private backend network; separate S3 keys and prefix policies; anonymous object/catalog probes. | Theft of a service secret or compromise of Keycloak, host, service environment or persistent volume can impersonate that principal. Internal service HTTP is not TLS in this local experiment. |
| T | **T-01: change governed rows, grants or policy through an interactive SQL identity.** | All interactive groups are read-only; policy denies DDL, DML, grant/revoke, ownership, session/system changes and other privileged operations. Policy source, version and digest are explicit review artifacts. | An authorized ETL writer can change governed data; a maintainer/deployer able to replace policy files or service configuration can change enforcement. SQL restrictions do not protect against a compromised trusted writer or host administrator. |
| T | **T-02: alter or replace stored objects, historical metadata or audit evidence.** | Separate service prefixes/keys and no-delete permissions for audit writer reduce cross-domain access and accidental deletion. Storage and catalog authorization are tested separately from SQL authorization. | No-delete is not immutability: a principal with write permission may overwrite an existing object unless storage enforces version retention/object lock. No signed hash chain or independent tamper-evident audit anchor is established. The audit outage case concerns delivery loss, not an object-overwrite test. |
| R | **R-01: a user/operator denies a query, or a query event is lost before durable audit storage.** | The intended event contract carries query ID, authenticated user, groups, status, timestamps and policy metadata when supplied. Private test evidence preserves observed query IDs and outcomes for bypass investigations. | The #22 bypass case now stops and restarts the real listener/collector/batch-writer path and records query-level coverage and missing lifecycle events in private per-run evidence. No fresh measured result is committed until that Docker case executes. Query events do not enumerate patients returned, and authentication failures need separate logging. A log is not proof of which patient a person viewed. |
| I | **I-01: read private S3 objects or the REST catalog without an approved identity.** | Backend-only network, no storage/catalog host ports, anonymous bucket/object denial, OIDC-protected catalog requests and prefix-scoped S3 keys. | Host/storage administrators and stolen service credentials remain privileged. Network isolation alone does not protect against a process already inside the backend network. |
| I | **I-02: obtain another doctor's patients through direct queries, joins, CTEs, derived views or historical data.** | Doctor row filters use the current active care-team assignment on patient-bearing tables; independent pre-write oracles verify the permitted row set. Join/CTE/derived-query and old-snapshot cases compare returned tokens against independent expectations. | A policy defect, incorrect care-team source, unsafe new table/view, or untested alternate query path can disclose rows. Existing checks do not eliminate inference from data a doctor is legitimately allowed to see. |
| I | **I-03: read bronze/raw data or recover excluded data from an old Iceberg snapshot.** | Ordinary SQL groups receive no bronze grant; storage access is prefix-scoped; time-travel probes use source-only snapshots and independent row expectations. | Trusted ETL and storage/host administrators can access broader source material. Historical retention can preserve data after a current-table correction; deletion/retention governance and privileged administrator behavior need separate controls. |
| I | **I-04: enumerate hidden columns, table metadata, Iceberg metadata tables or internal identity data.** | Explicit table/column policy, hidden-field denial, information-schema/metadata probes, and separate ETL-only identity warehouse. | Metadata behavior is engine/version-specific and must be retested on upgrades. Error text, query plans, logs or new system tables could reveal names or policy details not covered by the current allowlist. |
| I | **I-05: infer a person from rare aggregate groups or exact count differencing.** | The bypass suite reports minimum k, singleton-record percentage, small-cell counts and exact-count differencing against synthetic ground truth. Its 1,000-patient Synthea run publishes aggregate-only metrics separately from private row-level evidence. | **Known leak:** recorded run found 4 singleton groups among 11 synthetic patients (36.36%); exact count differencing exposed the selected patient's presence. No suppression threshold, privacy budget or differential-privacy guarantee is currently implemented. Tracked in [#46](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/46). |
| I | **I-06: re-identify pseudonymous records using quasi-identifiers and auxiliary information.** | HMAC tokens remove source IDs from shared tables; linkage tests compare age band, sex, masked ZIP and rare-condition signatures derived from researcher-visible reason codes against independent synthetic truth. Incomplete and ambiguous keys are excluded. | **Known leak:** recorded run correctly linked 3 of 11 synthetic patients (27.27% of population); 5 auxiliary records had complete quasi-identifiers. HMAC is pseudonymization and deterministic cross-table tokens remain linkable. Tracked in [#46](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/46). |
| D | **D-01: exhaust or interrupt Trino, catalog, identity, authorizer, database, storage or audit dependencies.** | Services are isolated on private networks, have health/readiness checks and explicit dependencies; the audit path is expected to use durable buffering/retry; integration tests exercise service recreation and preserve interrupted outcomes. | No comprehensive query quotas, per-user rate limits, capacity SLO, failover or recovery-time guarantee is demonstrated. The #22 outage case measures audit event loss during a collector interruption; it is not an attacker-generated denial-of-service test. A fresh measured result remains pending until the Docker case executes. |
| E | **E-01: turn a read-only SQL identity into a writer, impersonator or broader group.** | Explicit deny rules cover DDL/DML, grants, ownership, procedures and policy-weakening session operations; user/group binding is server-controlled. Admin is still a read-only SQL role, not infrastructure root. | A Trino/policy bug, unsafe new function/table, misconfigured group provider or compromised admin credentials could bypass the intended boundary. Every new role, resource and operation requires policy review and negative tests. |
| E | **E-02: abuse a more privileged machine identity after crossing a service boundary.** | Bootstrap, ETL, Trino and audit writer use separate principals; catalog and object-store grants are scoped; no service secret is distributed to hospital users; audit writer has no clinical warehouse grant. | ETL and bootstrap necessarily have stronger authority than query users. Compromise of those identities can alter data or grants within their scope. Rotation, host protection, secret custody and operator separation remain critical. |

## 5. Bypass-case traceability

Every row in [tests/bypass/cases.csv](../tests/bypass/cases.csv) maps to at least one STRIDE threat above. The CSV inventory keeps its statuses at not_run by design; measured outcomes belong to a private per-run report, not a committed edit to the inventory.

| Bypass case | Threat IDs | What the case checks / limitation |
|---|---|---|
| anonymous_object_read | I-01, S-02, T-02 | Anonymous object listing/GET is denied and bucket policy inspected. It does not prove that a privileged storage administrator cannot overwrite objects. |
| catalog_direct_access | S-02, I-01, E-02 | Direct REST catalog probes require authentication/authorization; an authenticated positive control distinguishes denial from a missing service. |
| doctor_other_patient | I-02 | Doctor results are compared with independent membership oracles and the other doctor's exclusive patient is targeted directly. |
| identity_impersonation | S-01, E-01 | Attempts forged identities, passwords and group headers; not a test of endpoint compromise or stolen valid credentials. |
| admin_operations | T-01, E-01 | Exercises forbidden writes, DDL, grants and administrative SQL operations for all interactive SQL users, including read-only admin. |
| bronze_query | I-03 | Ordinary SQL users are denied bronze access; trusted ETL can provision the positive-control sentinel. |
| old_snapshot_query | I-03, I-02 | Historical snapshots are compared with pre-write source-only oracles; hidden-field and row-filter expectations still apply. |
| join_or_view_bypass | I-02, E-01 | Joins, CTEs and derived views must not add doctor rows; persistent view creation is denied by the SELECT-only policy. |
| metadata_enumeration | I-04, I-01 | Allowed table/column metadata is compared with policy; hidden fields, Iceberg metadata tables and identity data are probed. |
| aggregate_inference | I-05 | Measures k-anonymity, singleton groups, exact-count differencing and researcher-visible rare-condition signatures. A measured disclosure is retained as leaked, not reported as blocked. |
| quasi_identifier_linkage | I-06 | Measures unique linkage using age band, sex, masked ZIP and a rare-condition signature against synthetic auxiliary data; incomplete/ambiguous keys do not count as identity. |
| audit_collector_outage | R-01, D-01, T-02 | Stops and restarts the real collector during known Trino queries, then reconciles created/completed event rows, coverage, missing events and duplicates. Per-run evidence is private; the test does not establish object immutability or malicious-DoS resistance. |

### Recorded investigation state

The committed [bypass report](bypass-report.md) records one completed 12-case investigation: **9 blocked, 2 leaked, 1 inconclusive**. The two leaked cases are aggregate_inference and quasi_identifier_linkage; audit_collector_outage is inconclusive with missing_events null. These outcomes describe that recorded synthetic run, not a universal guarantee or a fresh execution by this documentation change.

The report describes a deliberately small 11-patient synthetic population (the two-patient source plus nine boundary fixtures). Its measured percentages must not be presented as estimates for real populations. The bypass suite's cases.csv remains the stable inventory and retains not_run; see the [bypass test instructions](../tests/bypass/README.md) for reproduction and private evidence handling.

## 6. Important limitations and security claims we do not make

- **HMAC is pseudonymization, not anonymization.** Deterministic tokens preserve linkability across approved tables. Key compromise, auxiliary data and rare cohorts can undermine privacy.
- **Append-only is not tamper evidence.** A no-delete grant prevents one class of deletion but does not prove an authorized writer cannot overwrite an object. This design has no demonstrated immutable/WORM storage, signed hash chain or independent audit anchor.
- **Logs do not prove which patient was viewed.** Query-level table/column information and output-row counts are not row-level lineage or evidence that a client rendered/read a particular patient record.
- **Audit delivery is not a proven lossless security control.** The event contract and normalizer are not proof of listener delivery, durable retention, successful batch write or authentication-failure coverage. The #22 case now exercises the real listener/collector/writer path; measured loss remains unknown until that Docker test runs and emits private evidence.
- **A green bypass suite is not “no privacy risk.”** Known inference and linkage measurements intentionally retain leaked status even when their measurement code passes. Review [#46](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/46).
- **Infrastructure administrators are trusted.** Host/Docker root, storage root, database operators and maintainers who can replace configuration are outside the ordinary SQL-user boundary.
- **Local internal HTTP is a deployment limitation.** Backend network isolation is not equivalent to service-to-service TLS or a hardened multi-host deployment.
- **Synthetic results are not clinical assurance.** The source data, cohort sizes and attack rates do not establish safety or re-identification risk for a real hospital population.

## 7. Review and change triggers

Review this model whenever a new SQL role, table, column, storage prefix, service identity, function, event field, network publication, image version or data source is added. Re-run the relevant bypass, policy, storage/catalog and audit tests. Changes to HMAC key/version, group membership, row filter, mask, Iceberg retention, object-store grant or audit delivery semantics require review of affected threat IDs and case mappings.

Open or update a follow-up issue when a test discovers a new disclosure, privilege path, integrity weakness or availability dependency. Keep patient-level tokens, query literals, oracle contents and raw bypass reports in private evidence directories; do not commit them to this document or Git.

## References

- [Architecture decisions and trust boundaries](architecture.md)
- [Policy matrix and enforcement contract](../policies/README.md)
- [Bypass inventory](../tests/bypass/cases.csv) and [execution instructions](../tests/bypass/README.md)
- [Recorded bypass findings](bypass-report.md)
- [Audit event contract](../audit/event-contract.json) and [audit implementation limits](../audit/README.md)
- [Researcher inference and linkage follow-up #46](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/46)
- [Audit delivery follow-up #21](https://github.com/Adarsh-031/Governed-Lakehouse-With-Fine-Grained-Access-Control/issues/21)
