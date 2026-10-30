"""Unit tests for the spool -> audit table batch writer (no Docker, fake sink)."""

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from audit.collector import persist_event
from audit.writer import ARROW_SCHEMA, QUARANTINE_DIR, load_policy, main, process_once

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "trino"
RECEIVED = "2026-10-29T10:16:00.000000+00:00"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeSink:
    def __init__(self, existing=(), fail=False):
        self.rows = []
        self.existing = set(existing)
        self.fail = fail

    def existing_keys(self, query_ids):
        stored = {(row["query_id"], row["event_type"]) for row in self.rows}
        return {key for key in self.existing | stored if key[0] in set(query_ids)}

    def append(self, rows):
        if self.fail:
            raise OSError("catalog unavailable")
        self.rows.extend(rows)


def spool(tmp_path, *events):
    directory = tmp_path / "spool"
    directory.mkdir(mode=0o700, exist_ok=True)
    for event in events:
        persist_event(directory, event, RECEIVED)
    return directory


def remaining(directory):
    return sorted(p.name for p in directory.iterdir() if p.is_file())


def test_writer_schema_matches_contracts():
    contract = json.loads((ROOT / "audit/event-contract.json").read_text())
    tables = yaml.safe_load((ROOT / "policies/schema-contract.yaml").read_text())["tables"]
    assert ARROW_SCHEMA.names == contract["fields"]
    assert ARROW_SCHEMA.names == list(tables["audit"]["columns"])
    assert tables["audit"]["resource"] == "audit.audit.query_events"


def test_pass_writes_normalized_rows_and_clears_spool(tmp_path):
    directory = spool(
        tmp_path,
        fixture("created.json"),
        fixture("completed_success.json"),
        fixture("completed_access_denied.json"),
    )
    sink = FakeSink()
    result = process_once(directory, sink, policy=("clinical-v1", "abc"))
    assert result.as_dict() == {
        "files": 3,
        "written": 3,
        "duplicates": 0,
        "quarantined": 0,
        "quarantine_reasons": {},
    }
    assert remaining(directory) == []
    statuses = sorted((r["event_type"], r["status"]) for r in sink.rows)
    assert statuses == [
        ("QueryCompleted", "authorization_denial"),
        ("QueryCompleted", "success"),
        ("QueryCreated", "running"),
    ]
    for row in sink.rows:
        assert set(row) == set(ARROW_SCHEMA.names)
        assert isinstance(row["event_timestamp"], datetime)
        assert row["event_timestamp"].tzinfo is None
        assert row["policy_version"] == "clinical-v1"
        assert row["policy_digest"] == "abc"
        assert row["query_text_restricted"] in {None, "[REDACTED]"}
    assert "SELECT" not in json.dumps(sink.rows, default=str)


def test_retried_delivery_in_one_batch_is_written_once(tmp_path):
    event = fixture("completed_success.json")
    directory = spool(tmp_path, event, event)
    sink = FakeSink()
    result = process_once(directory, sink)
    assert (result.written, result.duplicates) == (1, 1)
    assert len(sink.rows) == 1
    assert remaining(directory) == []


def test_replay_of_already_stored_event_is_not_duplicated(tmp_path):
    event = fixture("completed_success.json")
    sink = FakeSink()
    process_once(spool(tmp_path, event), sink)
    # A crash after the commit but before spool cleanup leaves the file behind.
    directory = spool(tmp_path, event)
    result = process_once(directory, sink)
    assert (result.written, result.duplicates) == (0, 1)
    assert len(sink.rows) == 1
    assert remaining(directory) == []


def test_conflicting_duplicate_is_quarantined_and_batch_continues(tmp_path):
    first = fixture("completed_success.json")
    conflict = json.loads(json.dumps(first))
    conflict["statistics"]["outputRows"] = 99
    directory = spool(tmp_path, first, conflict, fixture("created.json"))
    sink = FakeSink()
    result = process_once(directory, sink)
    assert result.written == 2
    assert result.reasons == {"conflicting_duplicate": 1}
    quarantine = directory / QUARANTINE_DIR
    assert len(list(quarantine.glob("*.json"))) == 1
    assert [p.read_text() for p in quarantine.glob("*.reason")] == ["conflicting_duplicate\n"]
    assert remaining(directory) == []


def test_bad_json_and_invalid_events_are_quarantined(tmp_path):
    directory = spool(tmp_path, {"context": {}}, fixture("created.json"))
    (directory / "00000000000000000000-broken.json").write_text("{not json")
    sink = FakeSink()
    result = process_once(directory, sink)
    assert result.written == 1
    assert result.reasons == {"invalid_event": 1, "unreadable_json": 1}
    assert remaining(directory) == []


def test_failed_append_keeps_every_spool_file(tmp_path):
    directory = spool(tmp_path, fixture("created.json"), fixture("completed_success.json"))
    before = remaining(directory)
    with pytest.raises(OSError):
        process_once(directory, FakeSink(fail=True))
    assert remaining(directory) == before


def test_collector_temp_files_are_ignored(tmp_path):
    directory = spool(tmp_path, fixture("created.json"))
    (directory / ".incoming-partial").write_text("{}")
    result = process_once(directory, FakeSink())
    assert result.files == 1
    assert remaining(directory) == [".incoming-partial"]


def test_batch_size_limits_one_pass(tmp_path):
    events = [fixture("created.json"), fixture("completed_success.json")]
    directory = spool(tmp_path, *events)
    sink = FakeSink()
    assert process_once(directory, sink, batch_size=1).written == 1
    assert process_once(directory, sink, batch_size=1).written == 1
    assert remaining(directory) == []


def test_policy_manifest_is_read_from_repository():
    version, digest = load_policy(ROOT / "policies/version.json")
    manifest = json.loads((ROOT / "policies/version.json").read_text())
    assert (version, digest) == (manifest["policy_version"], manifest["policy_digest"])
    assert load_policy(ROOT / "does-not-exist.json") == (None, None)


def test_main_reports_failure_without_details(monkeypatch, capsys):
    monkeypatch.delenv("AUDIT_WRITER_OIDC_CLIENT_SECRET", raising=False)
    assert main([]) == 1
    assert capsys.readouterr().err.strip() == "Audit writer failed: KeyError"
