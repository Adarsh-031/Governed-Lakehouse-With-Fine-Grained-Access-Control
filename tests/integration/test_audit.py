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


def run_query(client, sql, user, password):
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
    with httpx.Client(
        base_url=f"https://localhost:{port}", verify=context, trust_env=False, timeout=30
    ) as client:
        password = compose.values["TRINO_PASSWORD_DOCTOR_ALICE"]
        allowed, error = run_query(client, "SELECT 1", "doctor_alice", password)
        assert error is None
        # The clinical policy never grants the catalog smoke table to anyone.
        denied, error = run_query(
            client, "SELECT * FROM hospital.catalog_smoke.roundtrip", "doctor_alice", password
        )
        assert error == "PERMISSION_DENIED"

    expected = {
        (allowed, "QueryCreated"): "running",
        (allowed, "QueryCompleted"): "success",
        (denied, "QueryCreated"): "running",
        (denied, "QueryCompleted"): "authorization_denial",
    }
    # Completed events are posted asynchronously after the client sees the result.
    deadline = time.monotonic() + 90
    while True:
        compose("run", "--rm", "--no-deps", "audit-writer", "--once")
        rows = stored(compose, allowed, denied)
        keys = {(row["query_id"], row["event_type"]): row for row in rows}
        if set(expected) <= set(keys) or time.monotonic() >= deadline:
            break
        time.sleep(3)
    assert {key: keys[key]["status"] for key in expected if key in keys} == expected, compose(
        "logs", "--no-color", "--tail", "60", "audit-collector", "trino"
    )
    assert all(row["authenticated_user"] == "doctor_alice" for row in rows)
    assert keys[(denied, "QueryCompleted")]["error_code"] == "PERMISSION_DENIED"
    assert len(rows) == len(keys), "duplicate (query_id, event_type) rows were stored"

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

    # Nothing but quarantined records (none expected here) remains in the spool.
    leftovers = [p.name for p in spool.iterdir() if p.is_file() and p.suffix == ".json"]
    assert leftovers == []
    assert not (spool / "quarantine").exists()
