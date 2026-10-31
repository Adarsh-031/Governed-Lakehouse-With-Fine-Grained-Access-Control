"""Isolated Compose projects: random test credentials, no developer-volume access."""

import os
import secrets
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest

from scripts.init_secrets import initialize

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def isolated_compose(tmp_path):
    directory = tmp_path / "secrets"
    previous_umask = os.umask(0o077)
    try:
        initialize(directory)
    finally:
        os.umask(previous_umask)
    env_file = directory / "runtime.env"
    values = dict(
        line.split("=", 1)
        for line in env_file.read_text().splitlines()
        if line and not line.startswith("#")
    )
    for variable, name in (
        ("TRINO_TLS_PEM", "trino.pem"),
        ("TRINO_PASSWORD_FILE", "password.db"),
        ("TRINO_CA_BUNDLE", "ca.crt"),
    ):
        values[variable] = str(directory / name)
    # Collector and writer share a private host spool owned by the test runner.
    audit_spool = tmp_path / "audit-spool"
    audit_spool.mkdir(mode=0o700)
    values["AUDIT_SPOOL_HOST_DIR"] = str(audit_spool)
    values["AUDIT_UID"] = str(os.getuid())
    values["AUDIT_GID"] = str(os.getgid())
    env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    policy = tmp_path / "rules.json"
    shutil.copyfile(ROOT / "policies/rules.json", policy)
    policy.chmod(0o644)  # Contains no credentials; readable by the container UID.
    override = tmp_path / "compose.test.yaml"
    override.write_text(
        "services:\n  trino:\n"
        f'    user: "{os.getuid()}:{os.getgid()}"\n'
        "    ports: !override\n"
        '      - "127.0.0.1::8443"\n'
        "    volumes:\n"
        f"      - {policy}:/etc/trino/policies/rules.json:ro\n"
    )
    command = [
        "docker",
        "compose",
        "--project-name",
        f"lakehouse-test-{secrets.token_hex(6)}",
        "--file",
        str(ROOT / "compose.yaml"),
        "--file",
        str(override),
        "--env-file",
        str(env_file),
    ]
    environment = {key: value for key, value in os.environ.items() if key not in values}

    def run(*args, expect_success=True, input_text=None):
        result = subprocess.run(
            [*command, *args],
            check=False,
            capture_output=True,
            text=True,
            input=input_text,
            # Large benchmark ingests (bench/run.py) can raise this per-command limit.
            timeout=int(os.environ.get("LAKEHOUSE_COMPOSE_TIMEOUT", "360")),
            env=environment,
        )
        output = result.stdout + result.stderr
        if expect_success and result.returncode != 0:
            for service in (
                "catalog",
                "keycloak",
                "openfga",
                "catalog-db",
                "keycloak-db",
                "openfga-db",
                "storage",
                "audit-collector",
                "audit-writer",
                "trino",
            ):
                diagnostics = subprocess.run(
                    [*command, "logs", "--no-color", "--tail", "40", service],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env=environment,
                )
                output += f"\n--- {service} logs ---\n"
                output += diagnostics.stdout + diagnostics.stderr
        for name, value in values.items():
            if name.startswith("TRINO_PASSWORD_") or name.endswith(
                ("_PASSWORD", "_SECRET", "_SECRET_KEY", "_ACCESS_KEY", "_KEY", "_TOKEN")
            ):
                output = output.replace(value, "<redacted>")
        if expect_success:
            if result.returncode != 0:
                pytest.fail(output, pytrace=False)
        else:
            assert result.returncode != 0, "An operation expected to fail succeeded."
        return output

    run.values = values
    run.policy = policy
    run.override_file = override
    run.ca = directory / "ca.crt"
    run.audit_spool = audit_spool
    try:
        yield run
    finally:
        run("down", "--volumes", "--remove-orphans")


@pytest.fixture(scope="session")
def compose_factory():
    return isolated_compose


@pytest.fixture
def compose(tmp_path, compose_factory):
    with compose_factory(tmp_path) as project:
        yield project
