"""Isolated Compose projects: random test credentials, no developer-volume access."""

import os
import secrets
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def compose(tmp_path):
    names = [
        line.split("=", 1)[0]
        for line in (ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    ]
    values = {name: secrets.token_hex(32) for name in names}
    values["STORAGE_ROOT_USER"] = "test_storage_admin"
    for service in ("ETL", "TRINO", "AUDIT_WRITER"):
        values[f"{service}_STORAGE_ACCESS_KEY"] = secrets.token_hex(10).upper()
    env_file = tmp_path / "runtime.env"
    env_file.touch(mode=0o600)
    env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    command = [
        "docker",
        "compose",
        "--project-name",
        f"lakehouse-test-{secrets.token_hex(6)}",
        "--file",
        str(ROOT / "compose.yaml"),
        "--env-file",
        str(env_file),
    ]
    environment = {key: value for key, value in os.environ.items() if key not in values}

    def run(*args, expect_success=True):
        result = subprocess.run(
            [*command, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=360,
            env=environment,
        )
        output = result.stdout + result.stderr
        for value in values.values():
            output = output.replace(value, "<redacted>")
        if expect_success:
            assert result.returncode == 0, output
        else:
            assert result.returncode != 0, "An operation expected to fail succeeded."
        return output

    try:
        yield run
    finally:
        run("down", "--volumes", "--remove-orphans")
