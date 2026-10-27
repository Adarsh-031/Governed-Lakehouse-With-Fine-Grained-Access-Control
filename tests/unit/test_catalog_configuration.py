"""Guard the trust boundary without requiring a running Docker daemon."""

import json
from pathlib import Path

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
