"""One disposable, unmodified-policy stack for the direct Trino access suite."""

import hashlib
import json
import os
import shutil
import time
from contextlib import ExitStack
from datetime import date
from pathlib import Path

import pytest
import requests
from trino.auth import BasicAuthentication
from trino.dbapi import connect
from trino.exceptions import TrinoQueryError

from .oracle import ROOT, Oracle


def pytest_collection_modifyitems(items):
    if os.environ.get("RUN_ACCESS_INTEGRATION") != "1":
        marker = pytest.mark.skip(
            reason="Set RUN_ACCESS_INTEGRATION=1 for Docker access acceptance."
        )
        for item in items:
            if Path(__file__).parent in Path(str(item.path)).parents:
                item.add_marker(marker)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    access = item.funcargs.get("access")
    if access is not None and report.when == "call":
        access.results.append({"test": item.nodeid, "outcome": report.outcome})


class Access:
    def __init__(self, compose, directory, port):
        self.compose, self.directory, self.port = compose, directory, port
        self.oracle = Oracle(directory)
        self.queries, self.results, self.doctor_results = [], [], {}
        self.connections = {}
        self.oracle_sha256 = {
            name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
            for name in (
                "care-team-oracle.json",
                "policy-boundary-oracle.json",
                "access-data-oracle.json",
            )
        }

    def connection(self, user, *, authenticated_as=None, headers=None):
        login = authenticated_as or user
        session = requests.Session()
        session.trust_env = False
        session.verify = str(self.compose.ca)
        connection = connect(
            host="localhost",
            port=int(self.port),
            user=user,
            auth=BasicAuthentication(login, self.compose.values[f"TRINO_PASSWORD_{login.upper()}"]),
            http_scheme="https",
            verify=str(self.compose.ca),
            http_session=session,
            timezone="UTC",
            source="issue19-access-tests",
            http_headers=headers,
            request_timeout=30,
            # JSON transport keeps evidence independent of optional spooling codecs.
            encoding=None,
        )
        return connection

    def execute(self, sql, user="admin", *, denied=False, authenticated_as=None, headers=None):
        temporary = authenticated_as is not None or headers is not None
        if temporary:
            connection = self.connection(user, authenticated_as=authenticated_as, headers=headers)
        else:
            if user not in self.connections:
                self.connections[user] = self.connection(user)
            connection = self.connections[user]
        deadline = time.monotonic() + 30
        try:
            while True:
                cursor = connection.cursor()
                try:
                    rows = cursor.execute(sql).fetchall()
                except TrinoQueryError as error:
                    if error.error_name == "NO_NODES_AVAILABLE" and time.monotonic() < deadline:
                        time.sleep(1)
                        continue
                    self.queries.append(
                        {
                            "user": authenticated_as or user,
                            "requested_user": user,
                            "statement": sql,
                            "query_id": error.query_id,
                            "error": error.error_name,
                        }
                    )
                    assert denied and error.error_name == "PERMISSION_DENIED", (
                        f"{user}: expected {'PERMISSION_DENIED' if denied else 'success'}, "
                        f"got {error.error_name} ({error.query_id})"
                    )
                    return []
                else:
                    self.queries.append(
                        {"user": user, "statement": sql, "query_id": cursor.query_id, "error": None}
                    )
                    assert not denied, f"{user}: forbidden operation succeeded ({cursor.query_id})"
                    return rows
                finally:
                    cursor.close()
        finally:
            if temporary:
                connection.close()

    def close(self):
        for connection in self.connections.values():
            connection.close()


@pytest.fixture(scope="module")
def access(compose_factory, tmp_path_factory):
    directory = tmp_path_factory.mktemp("issue19-access")
    evidence = directory / "evidence"
    evidence.mkdir(mode=0o700)
    source = Path(os.environ.get("ACCESS_TEST_SOURCE", ROOT / "tests/fixtures/synthea")).resolve()
    assert (source / "manifest.json").is_file(), "Generate the Synthea source first."
    # Snapshot committed bytes directly; no policy compiler provides expectations.
    manifest = json.loads((ROOT / "policies/version.json").read_text())
    policy_bytes = (ROOT / "policies/rules.json").read_bytes()
    assert hashlib.sha256(policy_bytes).hexdigest() == manifest["policy_digest"]
    policy_dir = evidence / "policy"
    policy_dir.mkdir(mode=0o700)
    for name in ("policies/rules.json", "policies/version.json", *manifest["input_sha256"]):
        content = (ROOT / name).read_bytes()
        if name in manifest["input_sha256"]:
            assert hashlib.sha256(content).hexdigest() == manifest["input_sha256"][name]
        path = policy_dir / name.replace("/", "__")
        path.write_bytes(content)
        path.chmod(0o600)
    with ExitStack() as cleanup:
        compose = cleanup.enter_context(compose_factory(directory))
        catalog = directory / "fixture.properties"
        shutil.copyfile(ROOT / "docker/trino/catalog/iceberg.properties", catalog)
        compose.override_file.write_text(
            compose.override_file.read_text()
            + f"      - {catalog}:/etc/trino/catalog/audit.properties:ro\n"
            + f"      - {catalog}:/etc/trino/catalog/identity.properties:ro\n"
            + "  ingest:\n"
            + f'    user: "{os.getuid()}:{os.getgid()}"\n'
            + "    volumes:\n"
            + f"      - {source}:/source:ro\n"
            + f"      - {evidence}:/evidence:rw\n"
            + f"      - {ROOT / 'tests/integration/policy_fixture.py'}:/app/policy_fixture.py:ro\n"
        )
        compose("build", "ingest")
        output = compose("run", "--rm", "ingest", "-m", "ingest.pipeline")
        ingestion = json.loads(next(line for line in output.splitlines() if line.startswith("{")))
        assert ingestion["readback_verified"], "Saved #17 oracle must have a successful load"
        seed_path = evidence / "care-team-oracle.json"
        assert (
            hashlib.sha256(seed_path.read_bytes()).hexdigest()
            == ingestion["care_team_oracle_sha256"]
        )
        seed = json.loads(seed_path.read_text())
        for name, digest in seed["source_sha256"].items():
            assert hashlib.sha256((source / name).read_bytes()).hexdigest() == digest
        compose("run", "--rm", "--no-deps", "ingest", "/app/policy_fixture.py")
        assert compose.policy.read_bytes() == policy_bytes
        compose("up", "-d", "--wait", "trino")
        host, port = compose("port", "trino", "8443").strip().rsplit(":", 1)
        assert host == "127.0.0.1"
        suite = Access(compose, evidence, port)
        cleanup.callback(suite.close)
        # Fail setup if the pre-write fixtures and server disagree about today's
        # UTC date. A run spanning midnight must be repeated with fresh fixtures.
        assert suite.execute("SELECT CAST(current_timestamp AT TIME ZONE 'UTC' AS DATE)") == [
            [date.fromisoformat(suite.oracle.boundary["utc_date"])]
        ]
        print(f"Private access evidence: {evidence}")
        try:
            yield suite
        finally:
            assert compose.policy.read_bytes() == policy_bytes, (
                "Mounted policy changed during tests"
            )
            for name, digest in suite.oracle_sha256.items():
                assert hashlib.sha256((evidence / name).read_bytes()).hexdigest() == digest
            report = {
                "policy_version": manifest["policy_version"],
                "policy_digest": manifest["policy_digest"],
                "boundary_date_utc": suite.oracle.boundary["utc_date"],
                "source_patient_count": len(suite.oracle.data["patients"])
                - len(suite.oracle.boundary["tokens"]),
                "oracle_sha256": suite.oracle_sha256,
                "doctors": suite.doctor_results,
                "tests": suite.results,
                "queries": suite.queries,
            }
            path = evidence / "access-results.json"
            path.write_text(json.dumps(report, indent=2) + "\n")
            path.chmod(0o600)
