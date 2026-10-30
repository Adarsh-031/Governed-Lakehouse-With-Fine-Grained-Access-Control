"""Unit tests for the authenticated, durable audit collector (issue #20)."""

import json
import os
import secrets
import stat

import pytest
from fastapi.testclient import TestClient

from audit import collector
from audit.collector import CollectorConfigError, CollectorSettings, create_app

EVENT = {
    "metadata": {"queryId": "20261030_120000_00001_abcde", "queryState": "FINISHED"},
    "context": {"user": "doctor_alice", "groups": ["doctor"]},
    "createTime": "2026-10-30T12:00:00.000Z",
    "endTime": "2026-10-30T12:00:01.000Z",
    "statistics": {"outputRows": 3},
}


@pytest.fixture
def token():
    return secrets.token_hex(32)


@pytest.fixture
def spool(tmp_path):
    return tmp_path / "spool"


def make_client(token, spool, **overrides):
    return TestClient(create_app(CollectorSettings(token=token, spool_dir=spool, **overrides)))


def spooled(spool):
    return sorted(spool.glob("*.json"))


def auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": "Basic Zm9vOmJhcg=="},
        {"Authorization": "Bearer"},
    ],
)
def test_unauthenticated_post_is_rejected_and_nothing_is_written(token, spool, headers):
    client = make_client(token, spool)
    response = client.post("/v1/events", json=EVENT, headers=headers)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert spooled(spool) == []


def test_authenticated_event_is_persisted_with_private_permissions(token, spool):
    client = make_client(token, spool)
    response = client.post("/v1/events", json=EVENT, headers=auth(token))
    assert response.status_code == 200
    files = spooled(spool)
    assert len(files) == 1
    record = json.loads(files[0].read_text())
    assert record["event"] == EVENT
    assert record["record_id"] == response.json()["record_id"]
    assert record["received_timestamp"].endswith("+00:00")
    assert stat.S_IMODE(files[0].stat().st_mode) == 0o600
    assert stat.S_IMODE(spool.stat().st_mode) == 0o700


def test_event_is_fsynced_and_renamed_before_the_response(token, spool, monkeypatch):
    client = make_client(token, spool)
    steps = []
    real_fsync, real_replace = os.fsync, os.replace

    def tracking_fsync(fd):
        steps.append("fsync")
        return real_fsync(fd)

    def tracking_replace(src, dst):
        steps.append("rename")
        return real_replace(src, dst)

    monkeypatch.setattr(collector.os, "fsync", tracking_fsync)
    monkeypatch.setattr(collector.os, "replace", tracking_replace)
    response = client.post("/v1/events", json=EVENT, headers=auth(token))
    steps.append("response")
    assert response.status_code == 200
    # File data fsync, atomic rename, directory fsync, then the acknowledgement.
    assert steps == ["fsync", "rename", "fsync", "response"]


def test_failed_write_is_not_acknowledged_and_leaves_no_partial_file(token, spool, monkeypatch):
    client = make_client(token, spool)

    def failing_fsync(fd):
        raise OSError("disk full")

    monkeypatch.setattr(collector.os, "fsync", failing_fsync)
    response = client.post("/v1/events", json=EVENT, headers=auth(token))
    assert response.status_code == 503
    assert list(spool.iterdir()) == []


def test_restart_keeps_acknowledged_events_and_never_overwrites(token, spool):
    first = make_client(token, spool)
    acknowledged = [
        first.post("/v1/events", json=EVENT, headers=auth(token)).json()["record_id"]
        for _ in range(5)
    ]
    # Simulate a crash mid-write: an unacknowledged temporary file is left behind.
    (spool / ".incoming-crashed").write_bytes(b'{"partial"')

    second = make_client(token, spool)  # new process on the same spool
    assert not (spool / ".incoming-crashed").exists()
    acknowledged.append(
        second.post("/v1/events", json=EVENT, headers=auth(token)).json()["record_id"]
    )

    ids = [json.loads(path.read_text())["record_id"] for path in spooled(spool)]
    assert ids == acknowledged  # all kept, in arrival order, none overwritten


@pytest.mark.parametrize(
    ("body", "status"),
    [(b"not json", 400), (b"[1, 2]", 400), (b"\xff\xfe", 400)],
)
def test_malformed_events_are_rejected(token, spool, body, status):
    client = make_client(token, spool)
    response = client.post(
        "/v1/events",
        content=body,
        headers={**auth(token), "Content-Type": "application/json"},
    )
    assert response.status_code == status
    assert spooled(spool) == []


def test_oversized_event_is_rejected(token, spool):
    client = make_client(token, spool, max_body_bytes=64)
    response = client.post("/v1/events", json=EVENT, headers=auth(token))
    assert response.status_code == 413
    assert spooled(spool) == []


def test_healthcheck_reports_writable_spool(token, spool):
    client = make_client(token, spool)
    assert client.get("/healthz").json() == {"status": "ok"}


def test_api_docs_are_not_exposed(token, spool):
    client = make_client(token, spool)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_weak_or_missing_token_refuses_to_start(spool, monkeypatch):
    with pytest.raises(CollectorConfigError, match="at least"):
        CollectorSettings(token="short", spool_dir=spool)
    monkeypatch.delenv("AUDIT_INGEST_TOKEN", raising=False)
    with pytest.raises(CollectorConfigError, match="required"):
        collector.settings_from_environment()


def test_environment_settings(spool, monkeypatch, token):
    monkeypatch.setenv("AUDIT_INGEST_TOKEN", token)
    monkeypatch.setenv("AUDIT_SPOOL_DIR", str(spool))
    monkeypatch.setenv("AUDIT_MAX_BODY_BYTES", "2048")
    settings = collector.settings_from_environment()
    assert settings.spool_dir == spool
    assert settings.max_body_bytes == 2048
