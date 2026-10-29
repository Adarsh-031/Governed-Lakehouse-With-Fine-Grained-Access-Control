"""Guard the trust boundary without requiring a running Docker daemon."""

import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_catalog_keeps_privileged_secrets_out_of_runtime_and_reader():
    config = yaml.safe_load((ROOT / "compose.yaml").read_text())
    services = config["services"]
    for name in ("catalog", "catalog-db", "keycloak", "openfga"):
        assert services[name]["networks"] == ["backend"]
        assert not services[name].get("ports")
    runtime = services["catalog"]["environment"]
    assert runtime["LAKEKEEPER__AUTHZ_BACKEND"] == "openfga"
    assert runtime["LAKEKEEPER__OPENID_AUDIENCE"] == "lakekeeper"
    assert not any(key.startswith(("STORAGE_", "ETL_", "TRINO_")) for key in runtime)
    check = services["catalog-check"]["environment"]
    assert "CATALOG_BOOTSTRAP_OIDC_CLIENT_SECRET" not in check
    assert "STORAGE_ROOT_PASSWORD" not in check
    assert "CATALOG_DB_PASSWORD" not in check
    assert services["catalog"]["depends_on"]["catalog-migrate"]["condition"] == (
        "service_completed_successfully"
    )


def test_oidc_clients_require_individual_secrets_and_catalog_audience():
    realm = json.loads((ROOT / "docker/catalog/realm.json").read_text())
    assert {client["clientId"] for client in realm["clients"]} == {
        "catalog-bootstrap",
        "etl",
        "trino",
        "audit-writer",
    }
    for client in realm["clients"]:
        assert client["secret"].startswith("${")
        assert client["serviceAccountsEnabled"] is True
        assert client["publicClient"] is False
        assert client["directAccessGrantsEnabled"] is False
        assert client["standardFlowEnabled"] is False
        mapper = client["protocolMappers"][0]
        assert mapper["config"]["included.custom.audience"] == "lakekeeper"


def test_identity_bootstrap_revokes_managed_reader_drift_and_is_rerunnable(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "docker/catalog"))
    import bootstrap

    monkeypatch.setattr(bootstrap, "access_token", lambda service: service)
    assignments = [
        {"type": "select", "user": "trino"},
        {"type": "describe", "user": "audit-writer"},
    ]
    calls = []

    def management(method, path, token, **kwargs):
        calls.append((method, path, kwargs))
        if path == "warehouse":
            return {
                "warehouses": [
                    {
                        "name": "identity",
                        "warehouse-id": "identity-id",
                        "storage-profile": {**bootstrap.PROFILE, "key-prefix": "identity"},
                    }
                ]
            }
        if path == "user":
            return {"id": token}
        if method == "GET":
            return {"assignments": list(assignments)}
        for removed in kwargs["json"]["deletes"]:
            assignments.remove(removed)
        assignments.extend(kwargs["json"]["writes"])

    monkeypatch.setattr(bootstrap, "management", management)
    bootstrap.provision_warehouse("bootstrap", "identity", "identity", set())
    assert assignments == [
        {"type": relation, "user": "etl"} for relation in ("create", "describe", "modify", "select")
    ]
    calls.clear()
    bootstrap.provision_warehouse("bootstrap", "identity", "identity", set())
    assert not any(method == "POST" and path.endswith("/assignments") for method, path, _ in calls)


def test_identity_bootstrap_rejects_wrong_storage_profile_before_grants(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "docker/catalog"))
    from unittest.mock import Mock

    import bootstrap

    management = Mock(
        return_value={
            "warehouses": [
                {
                    "name": "identity",
                    "warehouse-id": "wrong",
                    "storage-profile": {**bootstrap.PROFILE, "key-prefix": "silver"},
                }
            ]
        }
    )
    monkeypatch.setattr(bootstrap, "management", management)
    with pytest.raises(RuntimeError, match="different storage profile"):
        bootstrap.provision_warehouse("bootstrap", "identity", "identity", set())
    management.assert_called_once()
