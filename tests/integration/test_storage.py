"""Opt-in Docker acceptance test; uses isolated throwaway credentials and volumes."""

import json
import os
import secrets
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_STORAGE_INTEGRATION") != "1",
    reason="Set RUN_STORAGE_INTEGRATION=1 to run the Docker storage acceptance test.",
)


@pytest.fixture
def compose(tmp_path):
    values = {
        "STORAGE_ROOT_USER": "test_storage_admin",
        "STORAGE_ROOT_PASSWORD": secrets.token_hex(32),
    }
    for service in ("ETL", "TRINO", "AUDIT_WRITER"):
        values[f"{service}_STORAGE_ACCESS_KEY"] = secrets.token_hex(10).upper()
        values[f"{service}_STORAGE_SECRET_KEY"] = secrets.token_hex(32)
    env_file = tmp_path / "storage.env"
    env_file.touch(mode=0o600)
    env_file.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
    command = [
        "docker",
        "compose",
        "--project-name",
        f"lakehouse-storage-test-{secrets.token_hex(6)}",
        "--file",
        str(ROOT / "compose.yaml"),
        "--env-file",
        str(env_file),
    ]
    # Prevent ambient shell credentials from overriding this isolated env file.
    environment = {key: value for key, value in os.environ.items() if key not in values}

    def run(*args, expect_success=True):
        result = subprocess.run(
            [*command, *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=180,
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


def test_private_storage_bootstrap_permissions_and_persistence(compose):
    configuration = json.loads(compose("config", "--format", "json"))
    assert configuration["networks"]["backend"]["internal"] is True
    for service in configuration["services"].values():
        assert not service.get("ports")
        assert set(service["networks"]) == {"backend"}

    compose("up", "-d", "--wait", "storage")
    # A fresh bootstrap and a repeat must both succeed.
    for _ in range(2):
        compose("run", "--rm", "--no-deps", "storage-init")
    assert "PASS:" in compose("run", "--rm", "--no-deps", "storage-check")

    def admin(script, **kwargs):
        return compose(
            "run",
            "--rm",
            "--no-deps",
            "storage-init",
            "-c",
            f". /bootstrap/common.sh; {script}",
            **kwargs,
        )

    admin(
        'printf persistent > "$MC_CONFIG_DIR/marker"; '
        'quiet mc cp "$MC_CONFIG_DIR/marker" admin/warehouse/gold/.persistence-test'
    )
    compose("up", "-d", "--wait", "--force-recreate", "storage")
    # No init rerun: the bucket, objects, users and IAM policies must survive.
    admin("quiet mc stat admin/warehouse/gold/.persistence-test")
    assert "PASS:" in compose("run", "--rm", "--no-deps", "storage-check")

    # Reruns revoke anonymous bucket access and refuse additive privilege drift.
    admin("quiet mc anonymous set download admin/warehouse")
    compose("run", "--rm", "--no-deps", "storage-init")
    assert "PASS:" in compose("run", "--rm", "--no-deps", "storage-check")
    admin('quiet mc admin policy attach admin readwrite --user "$ETL_STORAGE_ACCESS_KEY"')
    output = compose("run", "--rm", "--no-deps", "storage-init", expect_success=False)
    assert "Unexpected service policy binding" in output
