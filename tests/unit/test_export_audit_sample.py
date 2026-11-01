from pathlib import Path

from scripts.export_audit_sample import read_env, sanitize


def test_sanitize_relabels_query_ids_consistently():
    rows = [
        ("20261101_a", "QueryCreated", "doctor_alice", ["x", "y"], None),
        ("20261101_a", "QueryCompleted", "doctor_alice", [], 5),
        ("20261101_b", "QueryCompleted", "researcher", None, 0),
    ]
    out = sanitize(rows)
    assert [r[0] for r in out] == ["q001", "q001", "q002"]
    assert out[0][3] == "x;y"
    assert out[0][4] == ""
    assert not any("20261101" in cell for row in out for cell in row)


def test_read_env_ignores_comments(tmp_path: Path):
    env = tmp_path / "runtime.env"
    env.write_text("# c\nTRINO_PORT=8443\nTRINO_PASSWORD_ADMIN='s'\n", encoding="utf-8")
    assert read_env(env) == {"TRINO_PORT": "8443", "TRINO_PASSWORD_ADMIN": "s"}
