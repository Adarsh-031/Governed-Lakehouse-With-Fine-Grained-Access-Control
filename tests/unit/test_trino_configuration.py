"""Prevent accidental removal of the query boundary's security settings."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def properties(path):
    return dict(
        line.split("=", 1)
        for line in (ROOT / path).read_text().splitlines()
        if line and not line.startswith("#")
    )


def test_trino_is_the_only_published_service_and_receives_only_its_secrets():
    config = yaml.safe_load((ROOT / "compose.yaml").read_text())
    published = {
        name: service["ports"]
        for name, service in config["services"].items()
        if service.get("ports")
    }
    assert published == {"trino": ["127.0.0.1:8443:8443"]}
    trino = config["services"]["trino"]
    assert set(trino["networks"]) == {"backend", "frontend"}
    assert all(
        service["networks"] == ["backend"]
        for name, service in config["services"].items()
        if name != "trino"
    )
    assert set(trino["environment"]) == {
        "TRINO_INTERNAL_SHARED_SECRET",
        "TRINO_OIDC_CLIENT_SECRET",
        "TRINO_STORAGE_ACCESS_KEY",
        "TRINO_STORAGE_SECRET_KEY",
    }
    assert trino["depends_on"]["catalog-init"]["condition"] == "service_completed_successfully"
    assert trino["read_only"] is True
    assert all(mount.endswith(":ro") for mount in trino["volumes"])


def test_iceberg_uses_system_policy_and_scoped_service_credentials_over_https_only():
    catalog = properties("docker/trino/catalog/iceberg.properties")
    assert catalog["iceberg.security"] == "SYSTEM"
    assert catalog["iceberg.rest-catalog.uri"] == "http://catalog:8181/catalog"
    assert catalog["iceberg.rest-catalog.security"] == "OAUTH2"
    assert catalog["iceberg.rest-catalog.vended-credentials-enabled"] == "false"
    assert catalog["s3.aws-access-key"] == "${ENV:TRINO_STORAGE_ACCESS_KEY}"
    assert catalog["s3.aws-secret-key"] == "${ENV:TRINO_STORAGE_SECRET_KEY}"
    access = properties("docker/trino/access-control.properties")
    assert access["access-control.name"] == "file"
    assert access["security.config-file"] == "/etc/trino/policies/rules.json"
    server = properties("docker/trino/config.properties")
    assert server["http-server.http.enabled"] == "false"
    assert server["http-server.authentication.allow-insecure-over-http"] == "false"
    assert server["http-server.process-forwarded"] == "false"
    assert server["http-server.https.enabled"] == "true"
    assert server["http-server.authentication.type"] == "PASSWORD"
