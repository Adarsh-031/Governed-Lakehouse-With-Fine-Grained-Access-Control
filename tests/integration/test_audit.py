"""Trino HTTP events -> collector spool -> audit writer -> Iceberg audit table (#21)."""

import json
import os
import ssl
import time

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AUDIT_INTEGRATION") != "1",
    reason="Set RUN_AUDIT_INTEGRATION=1 to run the Docker audit pipeline test.",
)


def run_query(client, sql, user, password, rows=None):
    """Return (query_id, error_name); credentials never appear in diagnostics."""
    auth = httpx.BasicAuth(user, password)
    headers = {"X-Trino-User": user}
    response = client.post("/v1/statement", content=sql, auth=auth, headers=headers)
    assert response.status_code == 200, f"Trino returned HTTP {response.status_code}"
    result = response.json()
    deadline = time.monotonic() + 60
    while "nextUri" in result:
        assert time.monotonic() < deadline, "Trino query did not finish within 60 seconds"
        response = client.get(result["nextUri"], auth=auth, headers=headers)
        assert response.status_code == 200
        result = response.json()
        if rows is not None:
            rows.extend(result.get("data", []))
    return result["id"], result.get("error", {}).get("errorName")


def stored(compose, *query_ids):
    output = compose("run", "--rm", "--no-deps", "audit-writer", "--summary", *query_ids)
    # Compose progress goes to stderr, which the fixture appends; take the JSON line.
    return json.loads(next(line for line in output.splitlines() if line.startswith("[")))


def test_trino_events_reach_restricted_audit_table_once(compose):
    configuration = json.loads(compose("config", "--format", "json"))
    writer = configuration["services"]["audit-writer"]
    assert set(writer["networks"]) == {"backend"}
    assert not writer.get("ports")
    assert "AUDIT_INGEST_TOKEN" not in writer["environment"]
    assert "TRINO_STORAGE_SECRET_KEY" not in writer["environment"]
    assert configuration["services"]["trino"]["depends_on"]["audit-collector"]["condition"] == (
        "service_healthy"
    )

    compose("build", "catalog-init")
    compose("up", "-d", "--wait", "trino")
    compose("run", "--rm", "--no-deps", "catalog-check")
    host, port = compose("port", "trino", "8443").strip().rsplit(":", 1)
    assert host == "127.0.0.1"
    context = ssl.create_default_context(cafile=str(compose.ca))
    client = httpx.Client(
        base_url=f"https://localhost:{port}", verify=context, trust_env=False, timeout=30
    )
    issued = set()

    def issue(sql, user="doctor_alice", rows=None):
        query_id, error = run_query(
            client, sql, user, compose.values[f"TRINO_PASSWORD_{user.upper()}"], rows
        )
        issued.add(query_id)
        return query_id, error

    allowed, error = issue("SELECT 1")
    assert error is None
    failed, error = issue("SELECT no_such_column")
    assert error == "COLUMN_NOT_FOUND"
    # The clinical policy never grants the catalog smoke table to anyone.
    denied, error = issue("SELECT * FROM hospital.catalog_smoke.roundtrip")
    assert error == "PERMISSION_DENIED"

    expected = {
        (allowed, "QueryCreated"): "running",
        (allowed, "QueryCompleted"): "success",
        (failed, "QueryCreated"): "running",
        (failed, "QueryCompleted"): "sql_failure",
        (denied, "QueryCreated"): "running",
        (denied, "QueryCompleted"): "authorization_denial",
    }
    # Completed events are posted asynchronously after the client sees the result.
    deadline = time.monotonic() + 90
    while True:
        compose("run", "--rm", "--no-deps", "audit-writer", "--once")
        rows = stored(compose, allowed, failed, denied)
        keys = {(row["query_id"], row["event_type"]): row for row in rows}
        if set(expected) <= set(keys) or time.monotonic() >= deadline:
            break
        time.sleep(3)
    assert {key: keys[key]["status"] for key in expected if key in keys} == expected, compose(
        "logs", "--no-color", "--tail", "60", "audit-collector", "trino"
    )
    assert all(row["authenticated_user"] == "doctor_alice" for row in rows)
    assert keys[(denied, "QueryCompleted")]["error_code"] == "PERMISSION_DENIED"
    assert keys[(failed, "QueryCompleted")]["error_code"] == "COLUMN_NOT_FOUND"
    assert len(rows) == len(keys), "duplicate (query_id, event_type) rows were stored"

    # Only the admin group can read the audit table through Trino.
    audit_sql = "SELECT query_id, event_type, status FROM audit.audit.query_events"
    for user in ("doctor_alice", "doctor_bob", "researcher", "billing"):
        assert issue(audit_sql, user)[1] == "PERMISSION_DENIED", user
    admin_rows = []
    _, error = issue(f"{audit_sql} WHERE query_id = '{denied}'", "admin", admin_rows)
    assert error is None
    assert sorted(map(tuple, admin_rows)) == [
        (denied, "QueryCompleted", "authorization_denial"),
        (denied, "QueryCreated", "running"),
    ]

    # No recursive records: after the writer has run again, every stored query ID
    # is one this test issued through Trino; the writer itself never queries Trino.
    time.sleep(5)
    compose("run", "--rm", "--no-deps", "audit-writer", "--once")
    all_ids = []
    issue("SELECT DISTINCT query_id FROM audit.audit.query_events", "admin", all_ids)
    assert {row[0] for row in all_ids} <= issued
    assert "audit-writer" not in {
        row["authenticated_user"] for row in stored(compose, *sorted(issued))
    }
    client.close()

    # A replayed delivery (Trino retry or a crash before spool cleanup) is not stored twice.
    spool = compose.audit_spool
    replay = {
        "record_id": "replay",
        "received_timestamp": "2026-10-30T12:00:00+00:00",
        "event": {
            "metadata": {"queryId": allowed, "queryState": "QUEUED"},
            "context": {"user": "doctor_alice", "groups": ["doctor"]},
            "createTime": "2026-10-30T12:00:00Z",
        },
    }
    rows_before = stored(compose, allowed)
    created = next(row for row in rows_before if row["event_type"] == "QueryCreated")
    assert created["status"] == "running"
    (spool / "99999999999999999999-replay.json").write_text(json.dumps(replay))
    compose("run", "--rm", "--no-deps", "audit-writer", "--once")
    assert len(stored(compose, allowed)) == len(rows_before)

    # Every Trino event was valid: nothing was quarantined.
    assert not (spool / "quarantine").exists()
