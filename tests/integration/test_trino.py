"""Real HTTPS authentication and SYSTEM row/mask enforcement on Lakekeeper data."""

import json
import os
import socket
import ssl
import time

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_TRINO_INTEGRATION") != "1",
    reason="Set RUN_TRINO_INTEGRATION=1 to run the Docker Trino acceptance test.",
)


def query(client, sql, *, user="doctor_alice", password=None, headers=None, error_info=None):
    """Follow the statement protocol; never include credentials in diagnostics."""
    for attempt in range(2):
        try:
            response = client.post(
                "/v1/statement",
                content=sql,
                auth=httpx.BasicAuth(user, password) if password is not None else None,
                headers={"X-Trino-User": user, **(headers or {})},
            )
            break
        except httpx.RemoteProtocolError:
            # Trino can close a reused keep-alive connection (seen after 401 responses)
            # before replying; nothing was submitted, so resend once on a new connection.
            if attempt:
                raise
    if response.status_code != 200:
        return response.status_code, [], None
    result = response.json()
    rows = result.get("data", [])
    deadline = time.monotonic() + 60
    while "nextUri" in result:
        assert time.monotonic() < deadline, "Trino query did not finish within 60 seconds"
        response = client.get(
            result["nextUri"],
            auth=httpx.BasicAuth(user, password) if password is not None else None,
            headers={"X-Trino-User": user, **(headers or {})},
        )
        assert response.status_code == 200, f"Query polling returned HTTP {response.status_code}"
        result = response.json()
        rows.extend(result.get("data", []))
    error = result.get("error", {}).get("errorName")
    # Before workers announce their catalogs, Iceberg split scheduling can surface the
    # same startup race as GENERIC_INTERNAL_ERROR "No nodes available"; let callers retry.
    if error == "GENERIC_INTERNAL_ERROR" and "No nodes available" in (
        result.get("error", {}).get("message") or ""
    ):
        error = "NO_NODES_AVAILABLE"
    if error_info is not None:
        error_info.update(result.get("error", {}))
    return 200, rows, error


def test_https_authentication_and_system_policy_on_lakekeeper(compose):
    config = json.loads(compose("config", "--format", "json"))
    for name, service in config["services"].items():
        assert set(service["networks"]) == (
            {"backend", "frontend"} if name == "trino" else {"backend"}
        )
        if name != "trino":
            assert not service.get("ports"), f"{name} publishes a private service"
    assert config["networks"]["backend"]["internal"] is True
    assert config["services"]["trino"]["ports"][0]["host_ip"] == "127.0.0.1"
    compose("build", "catalog-init")
    compose("up", "-d", "--wait", "trino")
    compose("run", "--rm", "--no-deps", "catalog-check")
    address = compose("port", "trino", "8443").strip()
    host, port = address.rsplit(":", 1)
    assert host == "127.0.0.1" and int(port) > 0, "Trino HTTPS port was not published"
    password = compose.values["TRINO_PASSWORD_DOCTOR_ALICE"]
    # Pass the password through stdin, never a process argument or test diagnostic.
    cli = (
        "read -r TRINO_PASSWORD; export TRINO_PASSWORD; "
        "export JAVA_TOOL_OPTIONS=-Djava.io.tmpdir=/data/trino/native; "
        "exec trino --server https://localhost:8443 --user doctor_alice --password "
        '--truststore-path /etc/trino/auth/ca.crt --execute "SELECT 1"'
    )
    assert '"1"' in compose(
        "exec", "-T", "trino", "/bin/bash", "-c", cli, input_text=password + "\n"
    )
    compose(
        "exec",
        "-T",
        "trino",
        "/bin/bash",
        "-c",
        cli,
        input_text="deliberately-wrong-password\n",
        expect_success=False,
    )
    context = ssl.create_default_context(cafile=str(compose.ca))
    with httpx.Client(
        base_url=f"https://localhost:{port}", verify=context, trust_env=False, timeout=30
    ) as client:
        # This is an actual certificate/hostname-verified TLS connection, not --insecure.
        assert query(client, "SELECT 1", password=password) == (200, [[1]], None)
        assert query(client, "SELECT current_user, current_groups()", password=password) == (
            200,
            [["doctor_alice", ["doctor"]]],
            None,
        )
        assert query(client, "SELECT 1", password="deliberately-wrong-password")[0] == 401
        assert query(client, "SELECT 1")[0] == 401
        assert query(client, "SELECT 1", user="unknown_user", password=password)[0] == 401
        assert query(client, "SELECT 1", password=password, headers={"X-Trino-User": "admin"}) == (
            200,
            [],
            "PERMISSION_DENIED",
        )
        table = "hospital.catalog_smoke.roundtrip"
        # The clinical policy never grants the unrelated catalog smoke table.
        assert query(client, f"SELECT * FROM {table}", password=password)[2] == "PERMISSION_DENIED"

        # Permit only this synthetic table in the isolated project's policy copy.
        policy = json.loads(compose.policy.read_text())
        policy["catalogs"] = [{"catalog": "hospital", "allow": "read-only"}]
        policy["tables"] = [
            {
                "group": "doctor",
                "catalog": "hospital",
                "schema": "catalog_smoke",
                "table": "roundtrip",
                "privileges": ["SELECT"],
                "filter": "id = 1",
                "columns": [{"name": "value", "mask": "'redacted'"}],
            },
            {
                "group": "admin",
                "catalog": "hospital",
                "schema": "catalog_smoke",
                "table": "roundtrip",
                "privileges": ["SELECT"],
            },
        ]
        compose.policy.write_text(json.dumps(policy))
        compose("restart", "trino")
        compose("up", "-d", "--wait", "--no-deps", "trino")
        # Docker can reassign an ephemeral port even on an in-place restart.
        host, port = compose("port", "trino", "8443").strip().rsplit(":", 1)
        assert host == "127.0.0.1" and int(port) > 0
        client.base_url = f"https://localhost:{port}"
        # Catalog announcements reach the worker selector asynchronously after startup.
        deadline = time.monotonic() + 30
        while True:
            result = query(client, f"SELECT id, value FROM {table}", password=password)
            if result[2] != "NO_NODES_AVAILABLE" or time.monotonic() >= deadline:
                break
            time.sleep(1)
        assert result == (
            200,
            [[1, "redacted"]],
            None,
        ), compose("logs", "--no-color", "--tail", "100", "trino")
        assert query(
            client,
            f"SELECT id, value FROM {table} ORDER BY id",
            user="admin",
            password=compose.values["TRINO_PASSWORD_ADMIN"],
        ) == (200, [[1, "lakekeeper"], [2, "silo"]], None)
        assert (
            query(
                client,
                f"SELECT * FROM {table}",
                user="researcher",
                password=compose.values["TRINO_PASSWORD_RESEARCHER"],
                headers={"X-Trino-Group": "doctor"},
            )[2]
            == "PERMISSION_DENIED"
        )
        assert query(client, f"DELETE FROM {table}", password=password)[2] == "PERMISSION_DENIED"

    # No plain HTTP listener, even from inside the backend network.
    compose(
        "exec",
        "-T",
        "trino",
        "curl",
        "--fail",
        "--silent",
        "--max-time",
        "5",
        "http://127.0.0.1:8080/v1/info",
        expect_success=False,
    )
    # Sending cleartext to the sole published TLS port cannot log in.
    with socket.create_connection((host, int(port)), timeout=5) as connection:
        connection.sendall(b"POST /v1/statement HTTP/1.1\r\nHost: localhost\r\n\r\n")
        try:
            response = connection.recv(4096)
        except ConnectionResetError:
            response = b""
        assert b"200 OK" not in response
    # Docker's actual runtime port mappings contain no catalog/storage publications.
    output = compose("ps", "--format", "json", "--all").strip()
    containers = (
        json.loads(output)
        if output.startswith("[")
        else [json.loads(line) for line in output.splitlines()]
    )
    for container in containers:
        if container["Service"] != "trino":
            assert not any(p["PublishedPort"] for p in container.get("Publishers") or [])
