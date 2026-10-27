"""Generate a private, coherent set of local development credentials."""

import argparse
import fcntl
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import bcrypt

ROOT = Path(__file__).resolve().parents[1]
OUTPUTS = (
    "runtime.env",
    "password.db",
    "ca.key",
    "ca.crt",
    "trino.key",
    "trino.crt",
    "trino.pem",
)
# These are local test identities, not group bindings or authorization grants.
USERS = ("admin", "doctor_alice", "doctor_bob", "researcher", "billing")
SERVICES = ("ETL", "AUDIT_WRITER", "TRINO")


def openssl(*args: str, cwd: Path) -> None:
    # Capture output so neither key material nor verbose subprocess logs escape.
    subprocess.run(["openssl", *args], cwd=cwd, check=True, capture_output=True)


def generate(directory: Path) -> None:
    values = {
        "TRINO_HOST": "localhost",
        "TRINO_PORT": "8443",
        "TRINO_SCHEME": "https",
        "TRINO_CATALOG": "hospital",
        "TRINO_SCHEMA": "gold",
        "TRINO_CA_BUNDLE": "data/secrets/ca.crt",
        "TRINO_TLS_PEM": "data/secrets/trino.pem",
        "TRINO_PASSWORD_FILE": "data/secrets/password.db",
        "HMAC_KEY_HEX": secrets.token_hex(32),
        "HMAC_KEY_VERSION": "v1",
        "AUDIT_INGEST_TOKEN": secrets.token_hex(32),
        "TRINO_INTERNAL_SHARED_SECRET": secrets.token_hex(32),
        "STORAGE_ROOT_USER": "local_storage_admin",
        "STORAGE_ROOT_PASSWORD": secrets.token_hex(32),
        "LAKEKEEPER_ENCRYPTION_KEY": secrets.token_hex(32),
        "CATALOG_DB_PASSWORD": secrets.token_hex(32),
        "CATALOG_BOOTSTRAP_OIDC_CLIENT_SECRET": secrets.token_hex(32),
        "KEYCLOAK_DB_PASSWORD": secrets.token_hex(32),
        "OPENFGA_DB_PASSWORD": secrets.token_hex(32),
        "KEYCLOAK_ADMIN_PASSWORD": secrets.token_hex(32),
        "OPENFGA_PRESHARED_KEY": secrets.token_hex(32),
    }
    for service in SERVICES:
        # S3 access IDs are 20 characters; secrets have 256 bits of entropy.
        values[f"{service}_STORAGE_ACCESS_KEY"] = secrets.token_hex(10).upper()
        values[f"{service}_STORAGE_SECRET_KEY"] = secrets.token_hex(32)
        values[f"{service}_OIDC_CLIENT_SECRET"] = secrets.token_hex(32)

    hashes = []
    for user in USERS:
        password = secrets.token_hex(24)  # 48 ASCII bytes, below bcrypt's 72-byte limit.
        values[f"TRINO_PASSWORD_{user.upper()}"] = password
        # Trino 476 documents the $2y$ format. Python bcrypt accepts this variant.
        hashed = bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12))
        hashed = b"$2y$" + hashed[4:]
        hashes.append(f"{user}:{hashed.decode()}\n")
    (directory / "password.db").write_text("".join(hashes), encoding="ascii")
    (directory / "runtime.env").write_text(
        "# Generated local credentials. Do not commit, log, or share.\n"
        + "".join(f"{name}={value}\n" for name, value in values.items()),
        encoding="ascii",
    )

    openssl(
        "req",
        "-x509",
        "-newkey",
        "rsa:3072",
        "-nodes",
        "-sha256",
        "-days",
        "3650",
        "-subj",
        "/CN=Hospital Lakehouse Local CA",
        "-keyout",
        "ca.key",
        "-out",
        "ca.crt",
        "-addext",
        "basicConstraints=critical,CA:TRUE,pathlen:0",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
        cwd=directory,
    )
    openssl(
        "req",
        "-new",
        "-newkey",
        "rsa:3072",
        "-nodes",
        "-sha256",
        "-subj",
        "/CN=localhost",
        "-keyout",
        "trino.key",
        "-out",
        "trino.csr",
        cwd=directory,
    )
    (directory / "server.ext").write_text(
        "basicConstraints=critical,CA:FALSE\n"
        "keyUsage=critical,digitalSignature,keyEncipherment\n"
        "extendedKeyUsage=serverAuth\n"
        "subjectAltName=DNS:localhost,DNS:trino,IP:127.0.0.1,IP:::1\n",
        encoding="ascii",
    )
    openssl(
        "x509",
        "-req",
        "-in",
        "trino.csr",
        "-CA",
        "ca.crt",
        "-CAkey",
        "ca.key",
        "-set_serial",
        f"0x{secrets.token_hex(16)}",
        "-days",
        "365",
        "-sha256",
        "-extfile",
        "server.ext",
        "-out",
        "trino.crt",
        cwd=directory,
    )
    # Trino's PEM keystore contains its unencrypted key and certificate chain.
    (directory / "trino.pem").write_bytes(
        (directory / "trino.key").read_bytes()
        + (directory / "trino.crt").read_bytes()
        + (directory / "ca.crt").read_bytes()
    )
    openssl("verify", "-CAfile", "ca.crt", "-purpose", "sslserver", "trino.crt", cwd=directory)


def initialize(directory: Path, *, force: bool = False) -> bool:
    """Return whether credentials were generated; preserve a complete existing set."""
    os.umask(0o077)
    if directory.is_symlink():
        raise ValueError("The secrets directory must not be a symlink.")
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    # Lock the directory itself, leaving no persistent lock file with credentials.
    lock_fd = os.open(directory, os.O_RDONLY)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        existing = []
        for name in OUTPUTS:
            path = directory / name
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError(f"Refusing non-regular output: {name}")
            if path.exists():
                existing.append(path)
        if existing and not force:
            if len(existing) != len(OUTPUTS) or any(p.stat().st_size == 0 for p in existing):
                raise ValueError(
                    "Incomplete secret set; restore it from backup or use --force to rotate ALL "
                    "credentials. Existing files were preserved."
                )
            for path in existing:
                path.chmod(0o600)
            # Add issue #12's bootstrap identity without rotating existing encryption,
            # database, S3, HMAC, or TLS material from issue #4.
            runtime = directory / "runtime.env"
            content = runtime.read_text(encoding="ascii")
            if not any(
                line.startswith("CATALOG_BOOTSTRAP_OIDC_CLIENT_SECRET=")
                for line in content.splitlines()
            ):
                with tempfile.NamedTemporaryFile(mode="w", dir=directory, delete=False) as file:
                    file.write(content.rstrip("\n") + "\n")
                    file.write(f"CATALOG_BOOTSTRAP_OIDC_CLIENT_SECRET={secrets.token_hex(32)}\n")
                os.replace(file.name, runtime)
            return False
        if shutil.which("openssl") is None:
            raise ValueError("OpenSSL is required; install it before generating secrets.")
        # Finish generation before replacing anything. Failed runs leave the old set intact.
        with tempfile.TemporaryDirectory(prefix=".init-", dir=directory) as staging:
            temporary = Path(staging)
            generate(temporary)
            for name in OUTPUTS:
                (temporary / name).chmod(0o600)
            for name in OUTPUTS:
                os.replace(temporary / name, directory / name)
        return True
    finally:
        os.close(lock_fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--force",
        action="store_true",
        help="rotate ALL local credentials and the CA, including the HMAC/encryption keys",
    )
    args = parser.parse_args()
    try:
        created = initialize(ROOT / "data" / "secrets", force=args.force)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        # OpenSSL stderr is deliberately excluded; only non-secret diagnostics are printed.
        message = (
            "OpenSSL generation/verification failed."
            if isinstance(error, subprocess.CalledProcessError)
            else str(error)
        )
        print(f"Secret initialization failed: {message}", file=sys.stderr)
        return 1
    print(
        "Created local secrets in data/secrets/."
        if created
        else "Existing secrets preserved in data/secrets/."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
