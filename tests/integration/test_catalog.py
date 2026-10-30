"""Real REST/PyIceberg/Silo proof, including database and service recreation."""

import json
import os
import secrets

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_CATALOG_INTEGRATION") != "1",
    reason="Set RUN_CATALOG_INTEGRATION=1 to run the Docker catalog acceptance test.",
)


def test_authenticated_catalog_create_read_and_persistence(compose):
    configuration = json.loads(compose("config", "--format", "json"))
    assert configuration["networks"]["backend"]["internal"] is True
    for name, service in configuration["services"].items():
        if name != "trino":
            assert not service.get("ports")
        assert set(service["networks"]) == (
            {"backend", "frontend"} if name == "trino" else {"backend"}
        )
    assert (
        configuration["services"]["catalog"]["environment"]["LAKEKEEPER__AUTHZ_BACKEND"]
        == "openfga"
    )
    # Keycloak 26 sizes its JVM heap relative to the visible container/host
    # memory by default. Bound the test-only container and heap so the
    # multi-service persistence test does not exhaust a shared self-hosted runner.
    compose.override_file.write_text(
        compose.override_file.read_text()
        + "  keycloak:\\n"
        + "    mem_limit: 768m\\n"
        + "    environment:\\n"
        + '      JAVA_OPTS_KC_HEAP: "-XX:MaxRAMPercentage=65 -XX:InitialRAMPercentage=15"\\n'
    )
    compose("build", "catalog-init")
    compose("up", "-d", "--wait", "catalog")
    for _ in range(2):
        assert "PASS:" in compose("run", "--rm", "--no-deps", "catalog-init")
    namespace = f"check_{secrets.token_hex(6)}"

    def check(mode):
        output = compose(
            "run",
            "--rm",
            "--no-deps",
            "catalog-check",
            "/app/check.py",
            mode,
            "--namespace",
            namespace,
        )
        assert "PASS:" in output
        return next(json.loads(line) for line in output.splitlines() if line.startswith('{"'))

    before = check("create")
    # Stop all consumers before replacing their backing databases, retaining volumes.
    compose("stop", "catalog", "keycloak", "openfga")
    compose(
        "up",
        "-d",
        "--wait",
        "--no-deps",
        "--force-recreate",
        "catalog-db",
        "keycloak-db",
        "openfga-db",
        "storage",
    )
    compose("up", "-d", "--wait", "--no-deps", "--force-recreate", "keycloak", "openfga")
    compose("up", "-d", "--wait", "--no-deps", "--force-recreate", "catalog")
    # No migration, bootstrap, namespace creation or data write before this read.
    assert check("read") == before
    assert "PASS:" in compose("run", "--rm", "--no-deps", "catalog-init")
    assert check("read") == before
