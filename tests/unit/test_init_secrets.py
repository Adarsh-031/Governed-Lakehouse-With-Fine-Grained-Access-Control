"""Exercise real secret generation without using the developer's credentials."""

import os
import shutil
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import bcrypt
import pytest

from scripts import init_secrets


@pytest.fixture
def local_repo(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copy2(init_secrets.__file__, scripts / "init_secrets.py")
    directory = tmp_path / "data" / "secrets"
    directory.mkdir(parents=True)
    (directory / "README.md").write_text("Keep this documentation.\n")
    return tmp_path


def run_init(root, *args, env=None):
    return subprocess.run(
        [sys.executable, str(root / "scripts" / "init_secrets.py"), *args],
        cwd="/tmp",  # Output must not depend on the caller's working directory.
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def generated(local_repo):
    result = run_init(local_repo)
    assert result.returncode == 0, result.stderr
    return local_repo


def env_values(directory):
    return dict(
        line.split("=", 1)
        for line in (directory / "runtime.env").read_text().splitlines()
        if line and not line.startswith("#")
    )


def snapshot(directory):
    return {name: (directory / name).read_bytes() for name in init_secrets.OUTPUTS}


def test_generated_credentials_and_permissions(generated):
    directory = generated / "data" / "secrets"
    values = env_values(directory)
    example = dict(
        line.split("=", 1)
        for line in (init_secrets.ROOT / ".env.example").read_text().splitlines()
        if line and not line.startswith("#")
    )
    assert values.keys() == example.keys()
    assert not any(example.values())
    assert len(bytes.fromhex(values["HMAC_KEY_HEX"])) == 32
    assert values["HMAC_KEY_VERSION"] == "v1"
    assert len(bytes.fromhex(values["AUDIT_INGEST_TOKEN"])) == 32

    credentials = [
        value
        for name, value in values.items()
        if name.endswith(("_PASSWORD", "_SECRET", "_SECRET_KEY", "_KEY", "_TOKEN"))
        or name.startswith("TRINO_PASSWORD_")
    ]
    assert len(credentials) == len(set(credentials))
    for service in init_secrets.SERVICES:
        assert len(values[f"{service}_STORAGE_ACCESS_KEY"]) == 20
        assert len(bytes.fromhex(values[f"{service}_STORAGE_SECRET_KEY"])) == 32
        assert len(bytes.fromhex(values[f"{service}_OIDC_CLIENT_SECRET"])) == 32
    records = dict(
        line.split(":", 1) for line in (directory / "password.db").read_text().splitlines()
    )
    assert set(records) == set(init_secrets.USERS)
    for user, hashed in records.items():
        assert hashed.startswith("$2y$12$")
        assert bcrypt.checkpw(values[f"TRINO_PASSWORD_{user.upper()}"].encode(), hashed.encode())
        assert not bcrypt.checkpw(b"incorrect-password", hashed.encode())
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    for name in init_secrets.OUTPUTS:
        assert stat.S_IMODE((directory / name).stat().st_mode) == 0o600
    assert {p.name for p in directory.iterdir()} == {*init_secrets.OUTPUTS, "README.md"}
    assert (directory / "README.md").read_text() == "Keep this documentation.\n"


def test_certificate_chain_names_and_key(generated):
    directory = generated / "data" / "secrets"
    for option, name in (
        ("-verify_hostname", "localhost"),
        ("-verify_hostname", "trino"),
        ("-verify_ip", "127.0.0.1"),
        ("-verify_ip", "::1"),
    ):
        subprocess.run(
            [
                "openssl",
                "verify",
                "-CAfile",
                "ca.crt",
                "-purpose",
                "sslserver",
                option,
                name,
                "trino.crt",
            ],
            cwd=directory,
            check=True,
            capture_output=True,
        )
    rejected = subprocess.run(
        [
            "openssl",
            "verify",
            "-CAfile",
            "ca.crt",
            "-verify_hostname",
            "unrelated.example",
            "trino.crt",
        ],
        cwd=directory,
        capture_output=True,
        check=False,
    )
    assert rejected.returncode != 0
    public_key = subprocess.check_output(
        ["openssl", "pkey", "-in", "trino.key", "-pubout"],
        cwd=directory,
    )
    certificate_key = subprocess.check_output(
        ["openssl", "x509", "-in", "trino.crt", "-pubkey", "-noout"],
        cwd=directory,
    )
    assert public_key == certificate_key
    assert (directory / "trino.pem").read_bytes() == (
        (directory / "trino.key").read_bytes()
        + (directory / "trino.crt").read_bytes()
        + (directory / "ca.crt").read_bytes()
    )


def test_upgrade_adds_bootstrap_secret_without_rotating_existing_material(generated):
    directory = generated / "data" / "secrets"
    runtime = directory / "runtime.env"
    runtime.write_text(
        "\n".join(
            line
            for line in runtime.read_text().splitlines()
            if not line.startswith("CATALOG_BOOTSTRAP_OIDC_CLIENT_SECRET=")
        )
        + "\n"
    )
    old_values = env_values(directory)
    old_files = snapshot(directory)
    result = run_init(generated)
    assert result.returncode == 0, result.stderr
    values = env_values(directory)
    assert {name: values[name] for name in old_values} == old_values
    assert len(bytes.fromhex(values["CATALOG_BOOTSTRAP_OIDC_CLIENT_SECRET"])) == 32
    assert all(
        (directory / name).read_bytes() == value
        for name, value in old_files.items()
        if name != "runtime.env"
    )
    upgraded = snapshot(directory)
    assert run_init(generated).returncode == 0
    assert snapshot(directory) == upgraded


def test_rerun_preserves_every_byte_and_force_rotates(generated):
    directory = generated / "data" / "secrets"
    before = snapshot(directory)
    result = run_init(generated)
    assert result.returncode == 0
    assert "preserved" in result.stdout
    assert snapshot(directory) == before
    result = run_init(generated, "--force")
    assert result.returncode == 0, result.stderr
    assert all(before[name] != value for name, value in snapshot(directory).items())
    assert "PRIVATE KEY" not in result.stdout + result.stderr
    assert all(
        value not in result.stdout + result.stderr for value in env_values(directory).values()
    )


def test_partial_set_fails_without_overwriting(local_repo):
    directory = local_repo / "data" / "secrets"
    existing = directory / "runtime.env"
    existing.write_text("HMAC_KEY_HEX=keep-existing-key\n")
    result = run_init(local_repo)
    assert result.returncode != 0
    assert "Incomplete secret set" in result.stderr
    assert existing.read_text() == "HMAC_KEY_HEX=keep-existing-key\n"
    assert not (directory / "password.db").exists()


def test_concurrent_runs_publish_one_set(local_repo):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: run_init(local_repo), range(2)))
    assert all(result.returncode == 0 for result in results)
    assert sum("Created" in result.stdout for result in results) == 1
    assert sum("preserved" in result.stdout for result in results) == 1
    directory = local_repo / "data" / "secrets"
    values = env_values(directory)
    for record in (directory / "password.db").read_text().splitlines():
        user, hashed = record.split(":", 1)
        assert bcrypt.checkpw(values[f"TRINO_PASSWORD_{user.upper()}"].encode(), hashed.encode())


def test_failed_force_keeps_old_set(generated, tmp_path):
    directory = generated / "data" / "secrets"
    before = snapshot(directory)
    tools = tmp_path / "tools"
    tools.mkdir()
    failing = tools / "openssl"
    failing.write_text("#!/bin/sh\necho simulated-private-output >&2\nexit 1\n")
    failing.chmod(0o700)
    result = run_init(generated, "--force", env={**os.environ, "PATH": str(tools)})
    assert result.returncode != 0
    assert "simulated-private-output" not in result.stdout + result.stderr
    assert snapshot(directory) == before
    assert not list(directory.glob(".init-*"))


def test_symlink_output_is_rejected(local_repo, tmp_path):
    victim = tmp_path / "outside"
    victim.write_text("do not overwrite")
    (local_repo / "data" / "secrets" / "runtime.env").symlink_to(victim)
    result = run_init(local_repo, "--force")
    assert result.returncode != 0
    assert victim.read_text() == "do not overwrite"


def test_git_excludes_secrets_but_keeps_documentation():
    names = [f"data/secrets/{name}" for name in init_secrets.OUTPUTS]
    names += ["data/secrets/.init-example/README.md"]
    result = subprocess.run(
        ["git", "check-ignore", "--no-index", "--stdin"],
        cwd=init_secrets.ROOT,
        input="\n".join(names + ["data/secrets/README.md"]),
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.splitlines() == names
