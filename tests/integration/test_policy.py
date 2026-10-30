"""Execute the committed clinical policy on Trino 476 and real Iceberg data."""

import csv
import json
import os
import ssl
import time
from pathlib import Path

import httpx
import pytest
import yaml

from scripts.policy import MEMBERS, READER, record_experiment

from .test_trino import query

ROOT = Path(__file__).resolve().parents[2]
TABLES = yaml.safe_load((ROOT / "policies/schema-contract.yaml").read_text())["tables"]
with (ROOT / "policies/access-matrix.csv").open() as handle:
    MATRIX = list(csv.DictReader(handle))
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_POLICY_INTEGRATION") != "1",
    reason="Set RUN_POLICY_INTEGRATION=1 for the Docker clinical-policy acceptance test.",
)




def _workload_statements(path):
    """Read a role SQL file as statements, omitting its line comments."""
    sql = "\n".join(
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.lstrip().startswith("--")
    )
    return [statement.strip() for statement in sql.split(";") if statement.strip()]


def test_clinical_matrix_filters_masks_and_denials(compose, tmp_path):
    source = Path(os.environ.get("POLICY_TEST_SOURCE", ROOT / "tests/fixtures/synthea")).resolve()
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    manifest = record_experiment(evidence / "policy")
    # Reserved/forbidden catalogs use disposable clinical-warehouse aliases,
    # without changing production warehouse/S3 grants. Registering identity here
    # distinguishes a policy denial from merely lacking an SQL catalog.
    audit_catalog = tmp_path / "audit.properties"
    audit_catalog.write_text((ROOT / "docker/trino/catalog/iceberg.properties").read_text())
    compose.override_file.write_text(
        compose.override_file.read_text()
        + f"      - {audit_catalog}:/etc/trino/catalog/audit.properties:ro\n"
        + f"      - {audit_catalog}:/etc/trino/catalog/identity.properties:ro\n"
        + "  ingest:\n"
        + f'    user: "{os.getuid()}:{os.getgid()}"\n'
        + "    volumes:\n"
        + f"      - {source}:/source:ro\n"
        + f"      - {evidence}:/evidence:rw\n"
        + f"      - {ROOT / 'tests/integration/policy_fixture.py'}:/app/policy_fixture.py:ro\n"
    )
    compose("build", "ingest")
    compose("run", "--rm", "ingest", "-m", "ingest.pipeline")
    assert "PASS:" in compose("run", "--rm", "--no-deps", "ingest", "/app/policy_fixture.py")
    seed_oracle = json.loads((evidence / "care-team-oracle.json").read_text())
    boundary = json.loads((evidence / "policy-boundary-oracle.json").read_text())
    expected = {
        doctor: sorted(set(seed_oracle["expected_patient_tokens"][doctor]) | set(tokens))
        for doctor, tokens in boundary["expected_patient_tokens"].items()
    }
    compose("up", "-d", "--wait", "trino")
    host, port = compose("port", "trino", "8443").strip().rsplit(":", 1)
    assert host == "127.0.0.1"
    context = ssl.create_default_context(cafile=str(compose.ca))
    with httpx.Client(
        base_url=f"https://localhost:{port}", verify=context, trust_env=False, timeout=30
    ) as client:

        def execute(sql, user="admin", *, denied=False, headers=None):
            deadline = time.monotonic() + 30
            error_info = {}
            while True:
                status, rows, error = query(
                    client,
                    sql,
                    user=user,
                    password=compose.values[f"TRINO_PASSWORD_{user.upper()}"],
                    headers=headers,
                    error_info=error_info,
                )
                if error != "NO_NODES_AVAILABLE" or time.monotonic() >= deadline:
                    break
                time.sleep(1)
            assert status == 200
            assert error == ("PERMISSION_DENIED" if denied else None), (
                f"{user}: {sql}: {error}: {error_info.get('message')}"
            )
            return rows

        assert (
            execute("SELECT CAST(current_timestamp AT TIME ZONE 'UTC' AS DATE)")[0][0]
            == boundary["utc_date"]
        )
        # Verify every CSV cell against the real engine, including SQL types on visible
        # and masked fields and denied columns used only in predicates.
        for group, users in MEMBERS.items():
            for user in users:
                for table, spec in TABLES.items():
                    decisions = [r for r in MATRIX if r["group"] == group and r["table"] == table]
                    resource = spec["resource"]
                    if decisions[0]["operations"] == "NONE":
                        execute(f"SELECT count(*) FROM {resource}", user, denied=True)
                        for row in decisions:
                            execute(
                                f"SELECT {row['column']} FROM {resource} LIMIT 1", user, denied=True
                            )
                        continue
                    allowed = [r["column"] for r in decisions if r["visibility"] != "hidden"]
                    for row in decisions:
                        column = row["column"]
                        if row["visibility"] == "hidden":
                            for sql in (
                                f"SELECT {column} FROM {resource}",
                                f"SELECT count(*) FROM {resource} WHERE {column} IS NULL",
                                f"SELECT count(*) FROM {resource} GROUP BY {column}",
                            ):
                                execute(sql, user, denied=True)
                        else:
                            result = execute(
                                f"SELECT typeof({column}) FROM {resource} LIMIT 1", user
                            )
                            assert result == [[spec["columns"][column].lower()]]
                    # Trino 476 refuses a star projection containing a denied field.
                    hidden = any(r["visibility"] == "hidden" for r in decisions)
                    stars = execute(f"SELECT * FROM {resource} LIMIT 1", user, denied=hidden)
                    if not hidden:
                        assert len(stars[0]) == len(allowed)
                    assert len(
                        execute(f"SELECT {', '.join(allowed)} FROM {resource} LIMIT 1", user)[0]
                    ) == len(allowed)
                    shown = execute(f"SHOW COLUMNS FROM {resource}", user)
                    assert [r[0] for r in shown] == allowed

        for doctor, tokens in expected.items():
            for table in ("patients", "encounters"):
                resource = f"hospital.silver.{table}"
                assert execute(
                    f"SELECT DISTINCT patient_token FROM {resource} ORDER BY 1", doctor
                ) == [[t] for t in tokens]
                assert execute(
                    f"WITH x AS (SELECT * FROM {resource}) SELECT count(DISTINCT patient_token) FROM x",
                    doctor,
                ) == [[len(tokens)]]
                snapshot = boundary["snapshots"][table]
                assert execute(
                    f"SELECT DISTINCT patient_token FROM {resource} FOR VERSION AS OF {snapshot} ORDER BY 1",
                    doctor,
                ) == [[t] for t in tokens]
                assert execute(
                    f"SELECT count(*) FROM {resource} WHERE patient_token = '{boundary['tokens']['duplicate']}'",
                    doctor,
                ) == [[1 if doctor == "doctor_alice" else 0]]
            assert execute(
                "SELECT DISTINCT p.patient_token FROM hospital.silver.patients p JOIN hospital.silver.encounters e ON p.patient_token=e.patient_token ORDER BY 1",
                doctor,
            ) == [[t] for t in tokens]
            # Supplying a group header cannot elevate an authenticated doctor.
            execute(
                "SELECT * FROM hospital.silver.care_team",
                doctor,
                denied=True,
                headers={"X-Trino-Group": "admin"},
            )
            for target in (
                "admin",
                "doctor_bob" if doctor == "doctor_alice" else "doctor_alice",
                READER,
            ):
                execute("SELECT 1", doctor, denied=True, headers={"X-Trino-User": target})

        # Literal mask outputs protect against accepting a syntactically valid but
        # ineffective mask. Use SQL typeof and string casts to retain microseconds.
        for name, zip_value in zip(
            boundary["tokens"],
            ("021**", "021**", None, None, None, None, None, "999**", "000**"),
            strict=True,
        ):
            assert execute(
                f"SELECT zip FROM hospital.silver.patients WHERE patient_token='{boundary['tokens'][name]}'",
                "researcher",
            ) == [[zip_value]]
        assert execute(
            "SELECT CAST(start_time AS VARCHAR), stop_time, code, reason_code FROM hospital.silver.encounters WHERE encounter_id='issue18-alice'",
            "researcher",
        ) == [["2026-10-01 00:00:00.000000", None, "00123", "00456"]]
        assert execute(
            "SELECT CAST(stop_time AS VARCHAR) FROM hospital.silver.encounters WHERE encounter_id='issue18-bob'",
            "researcher",
        ) == [["2026-11-01 00:00:00.000000"]]
        assert execute(
            "SELECT start_time FROM hospital.silver.encounters WHERE encounter_id='issue18-missing'",
            "researcher",
        ) == [[None]]
        assert execute(
            "SELECT CAST(start_time AS VARCHAR) FROM hospital.silver.encounters WHERE encounter_id='issue18-alice'",
            "doctor_alice",
        ) == [["2026-10-28 12:34:56.123456"]]
        assert execute(
            "SELECT procedure_code, CAST(total_cost AS VARCHAR) FROM hospital.silver.claims ORDER BY claim_id",
            "billing",
        ) == execute(
            "SELECT procedure_code, CAST(total_cost AS VARCHAR) FROM hospital.silver.claims ORDER BY claim_id"
        )
        assert execute("SELECT query_id FROM audit.audit.query_events") == [["synthetic-query"]]

        # Metadata is filtered to permitted tables/columns; information_schema is
        # special in Trino, so these assertions exercise the real 476 behavior.
        for group, users in MEMBERS.items():
            for user in users:
                permitted = [
                    table
                    for table in ("patients", "encounters", "claims", "care_team")
                    if any(
                        r["group"] == group and r["table"] == table and r["operations"] == "SELECT"
                        for r in MATRIX
                    )
                ]
                assert execute("SHOW TABLES FROM hospital.silver", user) == [
                    [t] for t in sorted(permitted)
                ]
                metadata = execute(
                    "SELECT table_name, column_name FROM hospital.information_schema.columns WHERE table_schema='silver' ORDER BY table_name, ordinal_position",
                    user,
                )
                assert metadata == [
                    [table, col]
                    for table in sorted(permitted)
                    for col in TABLES[table]["columns"]
                    if next(
                        r
                        for r in MATRIX
                        if r["group"] == group and r["table"] == table and r["column"] == col
                    )["visibility"]
                    != "hidden"
                ]
                for suffix in (
                    "files",
                    "manifests",
                    "snapshots",
                    "history",
                    "partitions",
                    "properties",
                    "refs",
                ):
                    execute(f'SELECT * FROM hospital.silver."patients${suffix}"', user, denied=True)
                resource = f"hospital.silver.{permitted[0]}"
                for column in ("$path", "$partition", "$file_modified_time"):
                    execute(f'SELECT "{column}" FROM {resource}', user, denied=True)
                execute(f"SHOW CREATE TABLE {resource}", user, denied=True)
                for sql in (
                    "SELECT * FROM system.runtime.queries",
                    "SET SESSION query_max_run_time = '10m'",
                    "SET SESSION hospital.query_partition_filter_required = false",
                    "CREATE SCHEMA hospital.forbidden",
                    "CREATE TABLE hospital.silver.forbidden (x BIGINT)",
                    "CREATE VIEW hospital.silver.forbidden AS SELECT patient_token FROM hospital.silver.patients",
                    "INSERT INTO hospital.silver.care_team VALUES ('doctor_alice', 'forbidden', DATE '2020-01-01', NULL)",
                    "UPDATE hospital.silver.care_team SET doctor_user = 'doctor_alice'",
                    "DELETE FROM hospital.silver.care_team",
                    "TRUNCATE TABLE hospital.silver.care_team",
                    "DROP TABLE hospital.silver.care_team",
                    "ALTER TABLE hospital.silver.care_team ADD COLUMN forbidden VARCHAR",
                    "ALTER TABLE hospital.silver.care_team SET AUTHORIZATION USER doctor_alice",
                    "GRANT SELECT ON hospital.silver.care_team TO doctor_alice",
                    "REVOKE SELECT ON hospital.silver.care_team FROM doctor_alice",
                    (
                        "CALL hospital.system.register_table(schema_name => 'silver', "
                        "table_name => 'forbidden', table_location => 's3://warehouse/silver/forbidden')"
                    ),
                    "ALTER TABLE hospital.silver.care_team EXECUTE optimize",
                    "SELECT * FROM TABLE(hospital.system.table_changes(schema_name => 'silver', table_name => 'patients', start_snapshot_id => 1, end_snapshot_id => 2))",
                ):
                    execute(sql, user, denied=True)
        # Execute each committed workload through the real Trino HTTPS endpoint
        # using its own provisioned identity; collect result rows returned (not
        # rows scanned) for review and the later benchmark issue.
        workload_users = {
            "doctor_alice": "doctor.sql",
            "researcher": "researcher.sql",
            "billing": "billing.sql",
            "admin": "admin.sql",
        }
        workload_results = {}
        for user, filename in workload_users.items():
            statements = _workload_statements(ROOT / "sql/queries" / filename)
            assert len(statements) == 4, f"{filename} should contain exactly four examples"
            row_counts = []
            for index, statement in enumerate(statements, start=1):
                rows = execute(statement, user)
                row_counts.append({"query": index, "result_rows": len(rows)})
            workload_results[user] = {
                "file": filename,
                "query_count": len(statements),
                "result_row_counts": row_counts,
                "total_result_rows": sum(item["result_rows"] for item in row_counts),
            }

        assert (
            query(client, "SELECT 1", user=READER, password=compose.values["TRINO_PASSWORD_ADMIN"])[
                0
            ]
            == 401
        )
    # The experiment records the exact policy actually mounted; no synthetic rule
    # replacement or runtime grant widening is used during these assertions.
    assert compose.policy.read_bytes() == (evidence / "policy/rules.json").read_bytes()
    report = {
        "policy_version": manifest["policy_version"],
        "policy_digest": manifest["policy_digest"],
        "matrix_rows_verified": len(MATRIX),
        "doctor_counts": {doctor: len(tokens) for doctor, tokens in expected.items()},
        "boundary_date_utc": boundary["utc_date"],
        "role_query_workloads": workload_results,
    }
    (evidence / "policy-results.json").write_text(json.dumps(report, sort_keys=True) + "\n")
    (evidence / "policy-results.json").chmod(0o600)
    print(json.dumps(report, sort_keys=True))
