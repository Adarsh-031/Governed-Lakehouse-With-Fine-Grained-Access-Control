"""Unit tests for the optional Streamlit demo query client."""

import importlib.util
import secrets
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from trino.auth import BasicAuthentication

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "dashboard" / "app.py"


def load_app(monkeypatch):
    """Load the query client without starting Streamlit or requiring it in CI."""
    for key, value in {
        "TRINO_HOST": "localhost",
        "TRINO_PORT": "8443",
        "TRINO_CATALOG": "hospital",
        "TRINO_SCHEMA": "silver",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setitem(sys.modules, "streamlit", MagicMock())
    spec = importlib.util.spec_from_file_location("dashboard_app", APP)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    monkeypatch.setitem(sys.modules, "dashboard_app", module)
    spec.loader.exec_module(module)
    return module


def test_environment_settings_validate_port_and_identifiers(monkeypatch):
    app = load_app(monkeypatch)
    assert app.get_settings_from_environment() == ("localhost", 8443, "hospital", "silver")

    monkeypatch.setenv("TRINO_PORT", "99999")
    with pytest.raises(ValueError, match="TRINO_PORT"):
        app.get_settings_from_environment()

    monkeypatch.setenv("TRINO_PORT", "8443")
    monkeypatch.setenv("TRINO_CATALOG", "hospital; DROP TABLE patients")
    with pytest.raises(ValueError, match="TRINO_CATALOG"):
        app.get_settings_from_environment()


@pytest.mark.parametrize(
    "sql",
    ["", "DELETE FROM hospital.silver.patients", "INSERT INTO x VALUES (1)"],
)
def test_ui_guardrail_rejects_non_read_statements(monkeypatch, sql):
    app = load_app(monkeypatch)
    with pytest.raises(ValueError, match="read-only"):
        app.execute_query(
            app.TrinoSettings(user="doctor_alice", password=secrets.token_urlsafe(18)),
            sql,
        )


def test_query_uses_supplied_user_password_https_and_ca(monkeypatch):
    app = load_app(monkeypatch)
    provided_password = secrets.token_urlsafe(24)
    fake_cursor = MagicMock()
    fake_cursor.description = [("current_user",), ("groups",)]
    fake_cursor.fetchall.return_value = [("doctor_alice", ["doctor"])]
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cursor
    connect = MagicMock(return_value=fake_conn)
    monkeypatch.setattr(app.trino.dbapi, "connect", connect)
    monkeypatch.setenv("TRINO_CA_BUNDLE", "/tmp/test-ca.pem")

    columns, rows = app.execute_query(
        app.TrinoSettings(user=" doctor_alice ", password=provided_password),
        "SELECT current_user, current_groups()",
    )

    assert columns == ["current_user", "groups"]
    assert rows == [("doctor_alice", ["doctor"])]
    kwargs = connect.call_args.kwargs
    assert kwargs["user"] == "doctor_alice"
    assert kwargs["auth"] == BasicAuthentication("doctor_alice", provided_password)
    assert kwargs["http_scheme"] == "https"
    assert kwargs["verify"] == "/tmp/test-ca.pem"
    fake_conn.close.assert_called_once()


def test_no_shared_password_environment_variable_is_used(monkeypatch):
    app = load_app(monkeypatch)
    monkeypatch.setenv("TRINO_PASSWORD", secrets.token_urlsafe(18))
    provided_password = secrets.token_urlsafe(24)
    fake_conn = MagicMock()
    fake_cursor = MagicMock()
    fake_cursor.description = []
    fake_cursor.fetchall.return_value = []
    fake_conn.cursor.return_value = fake_cursor
    connect = MagicMock(return_value=fake_conn)
    monkeypatch.setattr(app.trino.dbapi, "connect", connect)

    app.execute_query(
        app.TrinoSettings(user="billing", password=provided_password),
        "SELECT 1",
    )
    assert connect.call_args.kwargs["auth"] == BasicAuthentication("billing", provided_password)


def test_role_query_examples_use_explicit_columns_and_expected_denials(monkeypatch):
    app = load_app(monkeypatch)
    users = ("doctor_alice", "doctor_bob", "researcher", "billing", "admin")

    for user in users:
        examples = app.query_examples_for_user(user)
        assert "Current identity and groups" in examples
        assert any(name.startswith("Expected denial:") for name in examples)
        assert all("SELECT *" not in sql.upper() for sql in examples.values())

    doctor = app.query_examples_for_user("doctor_alice")
    assert "My care-team patients — allowed" in doctor
    assert "hospital.silver.patients" in doctor["My care-team patients — allowed"]
    assert "hospital.silver.claims" in doctor["Expected denial: claims table"]

    researcher = app.query_examples_for_user("researcher")
    research_sql = researcher["Research cohort — suppressed release (cells >= 11 patients)"]
    assert "hospital.gold.researcher_demographics" in research_sql
    assert "zip" in researcher["Expected denial: row-level demographics"]
    assert "birthdate" not in research_sql.lower()
    assert "hospital.silver.claims" in researcher["Expected denial: claims table"]

    billing = app.query_examples_for_user("billing")
    assert "procedure_code" in billing["Claims for reconciliation — allowed"]
    assert "procedure_description" not in billing["Claims for reconciliation — allowed"]
    assert "hospital.silver.patients" in billing["Expected denial: patient demographics"]

    admin = app.query_examples_for_user("admin")
    assert (
        "identity.identity.patient_identity"
        in admin["Expected denial: restricted identity catalog"]
    )


def test_unknown_user_has_no_query_examples(monkeypatch):
    app = load_app(monkeypatch)
    with pytest.raises(ValueError, match="No query examples"):
        app.query_examples_for_user("unknown_user")


def test_trino_user_error_message_is_preserved(monkeypatch):
    app = load_app(monkeypatch)
    message = "Access Denied: Cannot select from table claims"
    assert app.trino_user_error_message(SimpleNamespace(message=message)) == message
