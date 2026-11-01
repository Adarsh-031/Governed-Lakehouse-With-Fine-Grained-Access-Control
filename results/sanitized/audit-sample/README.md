# Audit log sample

`audit-sample.csv` is a short export of the append-only Iceberg audit table
(`audit.audit.query_events`, admin-only) from a local run of the full stack,
produced by:

```sh
uv run --locked python scripts/export_audit_sample.py --limit 40
```

Each Trino query produces a `QueryCreated` and a `QueryCompleted` row with the
authenticated user, tables and columns touched, which tables had a row filter,
which columns were masked, row count and status (`success`,
`authorization_denial`, `sql_failure`).

What is not in it: SQL text (stored only as `[REDACTED]`, and that column is denied even to admin by `policies/rules.json`), literal values, error
messages, patient tokens or data values. Trino query IDs are replaced with
`q001`, `q002`, ... in the export so the sample cannot be matched to server logs.
