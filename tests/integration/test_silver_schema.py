"""Execute the committed DDL on Trino 476/Lakekeeper with test-only ETL authority."""

import json
import os
import re
import ssl
import time
from pathlib import Path

import httpx
import pytest
import yaml

from .test_trino import query

ROOT = Path(__file__).resolve().parents[2]
TABLES = yaml.safe_load((ROOT / "policies/schema-contract.yaml").read_text())["tables"]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_SCHEMA_INTEGRATION") != "1",
    reason="Set RUN_SCHEMA_INTEGRATION=1 to run the Docker silver DDL acceptance test.",
)


def test_silver_ddl_types_roundtrip_rerun_and_restricted_identity(compose, tmp_path):
    # Provision with ETL credentials only inside this random, disposable project.
    # Never widen the committed Trino service's read-only clinical credentials.
    catalog = (
        (ROOT / "docker/trino/catalog/iceberg.properties")
        .read_text()
        .replace("credential=trino:", "credential=etl:")
    )
    clinical = tmp_path / "hospital.properties"
    clinical.write_text(catalog)
    identity = tmp_path / "identity.properties"
    identity.write_text(catalog.replace("warehouse=hospital", "warehouse=identity"))
    provisioning_config = tmp_path / "provisioning.properties"
    provisioning_config.write_text(
        (ROOT / "docker/trino/config.properties")
        .read_text()
        .replace(
            "access-control.config-files=/etc/trino/access-control.properties,"
            "/etc/trino/read-only.properties",
            "access-control.config-files=/etc/trino/access-control.properties",
        )
    )
    override = compose.override_file
    override.write_text(
        override.read_text()
        + f"      - {clinical}:/etc/trino/catalog/hospital.properties:ro\n"
        + f"      - {identity}:/etc/trino/catalog/identity.properties:ro\n"
        + f"      - {provisioning_config}:/etc/trino/config.properties:ro\n"
        + "    environment:\n"
        + "      TRINO_OIDC_CLIENT_SECRET: ${ETL_OIDC_CLIENT_SECRET}\n"
        + "      TRINO_STORAGE_ACCESS_KEY: ${ETL_STORAGE_ACCESS_KEY}\n"
        + "      TRINO_STORAGE_SECRET_KEY: ${ETL_STORAGE_SECRET_KEY}\n"
        + "  catalog-init:\n    volumes:\n"
        + f"      - {ROOT / 'tests/integration/schema_setup.py'}:/app/schema_setup.py:ro\n"
    )
    compose("build", "catalog-init")
    compose("up", "-d", "--wait", "catalog")
    compose("run", "--rm", "--no-deps", "catalog-init")
    assert "PASS:" in compose("run", "--rm", "--no-deps", "catalog-init", "/app/schema_setup.py")

    baseline = compose.policy.read_text()
    # This isolated admin stands in for trusted provisioning, not interactive admin.
    compose.policy.write_text(
        json.dumps(
            {
                "catalogs": [{"user": "admin", "catalog": "hospital|identity", "allow": "all"}],
                "schemas": [{"user": "admin", "schema": "silver|identity|gold", "owner": True}],
                "tables": [{"user": "admin", "privileges": ["OWNERSHIP", "SELECT", "INSERT"]}],
                "impersonation": [],
            }
        )
    )
    compose("up", "-d", "--wait", "trino")
    context = ssl.create_default_context(cafile=str(compose.ca))
    with httpx.Client(verify=context, trust_env=False, timeout=30) as client:

        def connect():
            host, port = compose("port", "trino", "8443").strip().rsplit(":", 1)
            assert host == "127.0.0.1"
            client.base_url = f"https://localhost:{port}"

        def execute(sql, *, user="admin", success=True):
            deadline = time.monotonic() + 30
            while True:
                status, rows, error = query(
                    client,
                    sql,
                    user=user,
                    password=compose.values[f"TRINO_PASSWORD_{user.upper()}"],
                )
                if error != "NO_NODES_AVAILABLE" or time.monotonic() >= deadline:
                    break
                time.sleep(1)
            assert status == 200
            if success:
                assert error is None, f"{sql}: {error}"
            else:
                assert error is not None, f"Invalid write succeeded: {sql}"
            return rows, error

        connect()
        ddl = []
        for path in sorted((ROOT / "sql/schema").glob("*.sql")):
            text = re.sub(r"--[^\n]*", "", path.read_text())
            ddl.extend(statement.strip() for statement in text.split(";") if statement.strip())
        for statement in ddl:
            execute(statement)

        locations = {}
        for key, spec in TABLES.items():
            if key == "audit":
                continue
            resource = spec["resource"]
            columns, _ = execute(f"SHOW COLUMNS FROM {resource}")
            assert {row[0]: row[1].upper() for row in columns} == spec["columns"]
            create, _ = execute(f"SHOW CREATE TABLE {resource}")
            definition = create[0][0]
            location = re.search(r"location = '([^']+)'", definition).group(1)
            prefix = "identity" if key == "identity" else "silver"
            assert location.startswith(f"s3://warehouse/{prefix}/")
            assert "format_version = 2" in definition
            assert "format = 'PARQUET'" in definition
            locations[resource] = location
            execute(f"INSERT INTO {resource} (patient_token) VALUES (NULL)", success=False)

        # Synthetic-only fixtures. Assert native values survive Parquet writes/reads.
        token = "v1:" + "a" * 64
        execute(
            "INSERT INTO hospital.silver.patients VALUES "
            f"('{token}', DATE '1980-02-29', '40-49', 'F', NULL, NULL, '02139')"
        )
        execute(
            "INSERT INTO hospital.silver.encounters VALUES "
            f"('enc-1', '{token}', TIMESTAMP '2026-10-28 12:34:56.123456', NULL, "
            "'ambulatory', '00123', NULL, NULL, NULL)"
        )
        execute(
            "INSERT INTO hospital.silver.claims VALUES "
            f"('claim-1', '{token}', 'enc-1', 'synthetic-payer', '00456', NULL, "
            "DECIMAL '123.45', DECIMAL '100.01')"
        )
        execute(
            "INSERT INTO hospital.silver.care_team VALUES "
            f"('doctor_alice', '{token}', DATE '2026-10-01', NULL)"
        )
        execute(
            f"INSERT INTO hospital.silver.care_team VALUES ('doctor_alice', '{token}', NULL, NULL)",
            success=False,
        )
        execute(
            f"INSERT INTO identity.identity.patient_identity VALUES ('{token}', 'Test Patient')"
        )
        # #46 aggregate releases: one synthetic cell each, no patient key.
        execute("INSERT INTO hospital.gold.researcher_demographics VALUES ('40-49', 'F', NULL, 11)")
        execute("INSERT INTO hospital.gold.researcher_geography VALUES ('021**', 11)")
        execute("INSERT INTO hospital.gold.researcher_conditions VALUES ('00456', 11)")
        assert execute("SELECT birthdate, age_band, zip, race FROM hospital.silver.patients")[
            0
        ] == [["1980-02-29", "40-49", "02139", None]]
        # The raw protocol helper does not negotiate parametric datetime support;
        # a VARCHAR projection avoids legacy wire coercion to millisecond precision.
        assert execute(
            "SELECT CAST(start_time AS VARCHAR), stop_time, code FROM hospital.silver.encounters"
        )[0] == [["2026-10-28 12:34:56.123456", None, "00123"]]
        # Cast decimals to VARCHAR to avoid the JSON decoder's binary-float conversion.
        assert execute(
            "SELECT CAST(total_cost AS VARCHAR), CAST(payer_coverage AS VARCHAR) "
            "FROM hospital.silver.claims"
        )[0] == [["123.45", "100.01"]]
        assert execute("SELECT valid_from, valid_to FROM hospital.silver.care_team")[0] == [
            ["2026-10-01", None]
        ]
        assert execute("SELECT name FROM identity.identity.patient_identity")[0] == [
            ["Test Patient"]
        ]
        assert execute(
            "SELECT p.patient_token FROM hospital.silver.patients p "
            "JOIN hospital.silver.encounters e ON e.patient_token = p.patient_token "
            "JOIN hospital.silver.claims c ON c.patient_token = p.patient_token "
            "AND c.encounter_id = e.encounter_id "
            "JOIN hospital.silver.care_team ct ON ct.patient_token = p.patient_token "
            "JOIN identity.identity.patient_identity i ON i.patient_token = p.patient_token"
        )[0] == [[token]]

        for statement in ddl:
            execute(statement)
        for resource, location in locations.items():
            assert execute(f"SELECT count(*) FROM {resource}")[0] == [[1]]
            assert location in execute(f"SHOW CREATE TABLE {resource}")[0][0][0]

        # Restore the exact clinical policy and the second read-only controller.
        compose.policy.write_text(baseline)
        provisioning_config.write_text((ROOT / "docker/trino/config.properties").read_text())
        compose("restart", "trino")
        compose("up", "-d", "--wait", "--no-deps", "trino")
        connect()
        releases = {"researcher_demographics", "researcher_geography", "researcher_conditions"}
        for user in ("admin", "doctor_alice", "doctor_bob", "researcher", "billing"):
            for resource in locations:
                table = resource.rsplit(".", 1)[-1]
                allowed = {
                    "admin": {"patients", "encounters", "claims", "care_team", *releases},
                    "doctor_alice": {"patients", "encounters"},
                    "doctor_bob": {"patients", "encounters"},
                    "researcher": {"patients", "encounters", *releases},
                    "billing": {"claims"},
                }
                if table in allowed[user]:
                    assert execute(f"SELECT count(*) FROM {resource}", user=user)[0] == (
                        [[0]] if user == "doctor_bob" else [[1]]
                    )
                else:
                    assert (
                        execute(f"SELECT * FROM {resource}", user=user, success=False)[1]
                        == "PERMISSION_DENIED"
                    )
