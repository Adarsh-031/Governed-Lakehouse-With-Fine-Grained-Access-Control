
# Audit path

Trino HTTP events -> authenticated collector -> durable restricted spool ->
deduplicated batch writer -> access-controlled Iceberg audit table.

Enable query-created and query-completed events. Configure bounded retries.
Acknowledge only after durable persistence. Test outage/restart behavior and
measure missing events by reconciling known query IDs. Do not claim losslessness
without demonstrated delivery guarantees.

Exclude audit-writer ingestion queries from recursive ingestion by a dedicated
identity/path, while recording those writes in a separate operational log.
Use query_id + event_type for deduplication. Separate success, SQL failure,
authorization denial, and authentication failure.

Restrict SQL text because literals may contain identifying information.
Append-only permissions are not tamper evidence. Signed batches with externally
anchored digests are a stretch feature; storage admins remain in the threat model.
