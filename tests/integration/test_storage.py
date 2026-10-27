"""Opt-in Docker acceptance test; uses isolated throwaway credentials and volumes."""

import json
import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_STORAGE_INTEGRATION") != "1",
    reason="Set RUN_STORAGE_INTEGRATION=1 to run the Docker storage acceptance test.",
)


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
