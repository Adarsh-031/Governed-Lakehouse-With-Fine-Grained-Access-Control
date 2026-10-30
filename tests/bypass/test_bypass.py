"""Every inventory case produces evidence, including measured residual risks."""

import json
import ssl
import time
from collections import Counter
from datetime import UTC, datetime

import httpx
import pytest

from audit.reliability import reconcile_query_events

from tests.access.oracle import USERS, assert_rows, decisions, masked_value
from tests.access.test_access import forbidden_operations
from tests.integration.test_trino import query

from .report import ROOT, linkage_metrics, load_cases


def compare(attempt, actual, expected, table, columns):
    try:
        assert_rows(actual, expected, table, columns)
    except AssertionError:
        attempt.expect(
            False, expected="Exact independent row multiset", observed={"rows": len(actual)}
        )
    attempt.observe(
        expected="Exact independent row multiset",
        observed={"rows": len(actual), "unexpected_or_missing": 0},
    )


def backend(attempt, mode):
    compose = attempt.access.compose
    output = compose("run", "--rm", "--no-deps", "catalog-check", "/app/bypass_probe.py", mode)
    evidence = json.loads(next(line for line in output.splitlines() if line.startswith("{")))
    attempt.observe(**evidence)
    return evidence


def private_topology(attempt):
    topology = attempt.report.context["topology"]
    safe = topology["backend_internal"] and all(
        not service["ports"] and service["networks"] == ["backend"]
        for name, service in topology["services"].items()
        if name != "trino"
    )
    attempt.expect(
        safe, expected="Only Trino publishes a host port; backend is internal", observed=topology
    )


def anonymous_object_read(attempt):
    private_topology(attempt)
    evidence = backend(attempt, "anonymous_object_read")
    for request in evidence["requests"]:
        attempt.expect(
            request["http_status"] == 403 and request["error"] == "AccessDenied",
            expected="HTTP 403 AccessDenied for a known metadata object and bucket listing",
            observed=request,
            leak=request["http_status"] == 200,
        )
    policy = attempt.access.compose(
        "run",
        "--rm",
        "--no-deps",
        "storage-init",
        "-c",
        ". /bootstrap/common.sh; mc anonymous get-json admin/warehouse",
    )
    attempt.observe(identity="storage bootstrap (inspection only)", bucket_policy=policy.strip())


def catalog_direct_access(attempt):
    private_topology(attempt)
    evidence = backend(attempt, "catalog_direct_access")
    for request in evidence["requests"]:
        statuses = (403, 404) if request["identity"] == "audit-writer" else (401, 403)
        attempt.expect(
            request["http_status"] in statuses,
            expected=list(statuses),
            observed=request,
            leak=request["http_status"] == 200,
        )


def doctor_other_patient(attempt):
    for user in ("doctor_alice", "doctor_bob"):
        for table in ("patients", "encounters"):
            rows = attempt.sql(f"SELECT patient_token FROM hospital.silver.{table}", user)
            expected = attempt.access.oracle.rows(user, table, ["patient_token"])
            compare(attempt, rows, expected, table, ["patient_token"])
            wanted = attempt.access.oracle.tokens(user, table)
            unauthorized = sum(row[0] not in wanted for row in rows)
            attempt.observe(
                identity=user,
                table=table,
                unauthorized_rows=unauthorized,
                expected_tokens=len(wanted),
            )
            # Directly target the other doctor's exclusive boundary patient.
            name = "bob" if user == "doctor_alice" else "alice"
            token = attempt.access.oracle.boundary["tokens"][name]
            attempt.expect(
                attempt.sql(
                    f"SELECT patient_token FROM hospital.silver.{table} WHERE patient_token='{token}'",
                    user,
                )
                == [],
                expected="No rows for the other doctor's exclusive patient",
                observed={"target": name},
            )


def identity_impersonation(attempt):
    for user in USERS:
        for target in (*USERS, "policy_care_team_reader"):
            if user != target:
                attempt.sql("SELECT current_user", target, authenticated_as=user, denied=True)
        if user != "admin":
            attempt.sql(
                "SELECT * FROM hospital.silver.care_team",
                user,
                headers={"X-Trino-Group": "admin"},
                denied=True,
            )
    access = attempt.access
    with httpx.Client(
        base_url=f"https://localhost:{access.port}",
        verify=ssl.create_default_context(cafile=str(access.compose.ca)),
        trust_env=False,
        timeout=30,
    ) as client:
        for identity, password in (
            ("doctor_alice", None),
            ("doctor_alice", "wrong-password"),
            ("policy_care_team_reader", access.compose.values["TRINO_PASSWORD_ADMIN"]),
        ):
            status, _, _ = query(client, "SELECT 1", user=identity, password=password)
            attempt.expect(
                status == 401,
                expected="HTTP 401",
                observed={"identity": identity, "http_status": status},
                leak=status == 200,
            )
    attempt.observe(
        reason="Authentication failures precede query creation; no query ID or #21 authentication log is available."
    )


def admin_operations(attempt):
    for user in USERS:
        for statement in forbidden_operations(user).values():
            attempt.sql(statement, user, denied=True)


def bronze_query(attempt):
    for user in USERS:
        attempt.sql("SELECT * FROM hospital.bronze.access_probe", user, denied=True)
    attempt.observe(
        reason="Trusted ETL created a real bronze sentinel before the tests; admin is also read-only and denied bronze."
    )


def old_snapshot_query(attempt):
    oracle = attempt.access.oracle
    boundary_tokens = set(oracle.boundary["tokens"].values())
    for table in ("patients", "encounters"):
        snapshot = oracle.boundary["historical_snapshots"][table]
        attempt.expect(
            snapshot != oracle.boundary["snapshots"][table],
            expected="Noncurrent snapshot",
            observed={"table": table, "historical_snapshot": snapshot},
            leak=False,
        )
        resource = f"hospital.silver.{table} FOR VERSION AS OF {snapshot}"
        columns = (
            ["patient_token", "zip"] if table == "patients" else ["patient_token", "start_time"]
        )
        for user in ("admin", "doctor_alice", "doctor_bob", "researcher"):
            expected = [
                row for row in oracle.rows(user, table, columns) if row[0] not in boundary_tokens
            ]
            attempt.expect(
                bool(expected),
                expected="Nonempty historical positive control",
                observed={"user": user, "rows": len(expected)},
                leak=False,
            )
            actual = attempt.sql(f"SELECT {', '.join(columns)} FROM {resource}", user)
            compare(attempt, actual, expected, table, columns)
        hidden = "birthdate" if table == "patients" else "description"
        attempt.sql(f"SELECT {hidden} FROM {resource}", "researcher", denied=True)


def join_or_view_bypass(attempt):
    for user in ("doctor_alice", "doctor_bob"):
        expected = attempt.access.oracle.tokens(user, "patients") & attempt.access.oracle.tokens(
            user, "encounters"
        )
        joined = (
            "SELECT DISTINCT p.patient_token FROM hospital.silver.patients p "
            "JOIN hospital.silver.encounters e ON p.patient_token=e.patient_token"
        )
        statements = [
            joined,
            f"WITH v AS ({joined}) SELECT * FROM v",
            f"SELECT * FROM ({joined}) v",
        ]
        for statement in statements:
            rows = attempt.sql(statement, user)
            attempt.expect(
                {row[0] for row in rows} == expected,
                expected={"tokens": len(expected), "additional_access": 0},
                observed={"tokens": len(rows)},
            )
    # Persistent views cannot be created under the committed SELECT-only policy.
    for user in USERS:
        table = "claims" if user == "billing" else "patients"
        attempt.sql(
            f"CREATE VIEW hospital.silver.bypass_view SECURITY DEFINER AS SELECT patient_token FROM hospital.silver.{table}",
            user,
            denied=True,
        )
    attempt.observe(
        reason="Stored view creation is denied; CTE/derived views and joins execute against the independent oracle."
    )


def metadata_enumeration(attempt):
    for user in USERS:
        permitted = [
            name
            for name in ("patients", "encounters", "claims", "care_team")
            if decisions(user, name)[0]["operations"] == "SELECT"
        ]
        rows = attempt.sql("SHOW TABLES FROM hospital.silver", user)
        attempt.expect(
            rows == [[table] for table in sorted(permitted)],
            expected=sorted(permitted),
            observed=rows,
        )
        rows = attempt.sql(
            "SELECT table_name, column_name FROM hospital.information_schema.columns WHERE table_schema='silver' ORDER BY table_name, ordinal_position",
            user,
        )
        expected = [
            [table, row["column"]]
            for table in sorted(permitted)
            for row in decisions(user, table)
            if row["visibility"] != "hidden"
        ]
        attempt.expect(rows == expected, expected=expected, observed=rows)
        table = "claims" if user == "billing" else "patients"
        for suffix in (
            "files",
            "manifests",
            "snapshots",
            "history",
            "partitions",
            "properties",
            "refs",
        ):
            attempt.sql(f'SELECT * FROM hospital.silver."{table}${suffix}"', user, denied=True)
        for column in ("$path", "$partition", "$file_modified_time"):
            attempt.sql(f'SELECT "{column}" FROM hospital.silver.{table}', user, denied=True)
        attempt.sql(f"SHOW CREATE TABLE hospital.silver.{table}", user, denied=True)
        attempt.sql("SELECT * FROM identity.identity.patient_identity", user, denied=True)


def aggregate_inference(attempt):
    columns = ["age_band", "sex", "zip"]
    source = attempt.access.oracle.rows("researcher", "patients", columns)
    expected = Counter(source)
    rows = attempt.sql(
        "SELECT age_band, sex, zip, count(*) FROM hospital.silver.patients GROUP BY 1, 2, 3",
        "researcher",
    )
    observed = {tuple(row[:3]): row[3] for row in rows}
    attempt.expect(
        observed == expected,
        expected="Aggregate cells match independent masked ground truth",
        observed={"groups": len(observed)},
    )
    total = attempt.sql("SELECT count(*) FROM hospital.silver.patients", "researcher")[0][0]
    target = attempt.access.oracle.boundary["tokens"]["alice"]
    excluded = attempt.sql(
        f"SELECT count(*) FROM hospital.silver.patients WHERE patient_token <> '{target}'",
        "researcher",
    )[0][0]
    attempt.expect(
        total == len(source) and total - excluded == 1,
        expected="Ground truth confirms single-patient differencing",
        observed={"total": total, "excluded": excluded, "difference": total - excluded},
    )
    singletons = sum(count == 1 for count in observed.values())
    attempt.observe(
        population=len(source),
        singleton_groups=singletons,
        singleton_rate=singletons / len(source),
        differencing_disclosure=True,
    )
    attempt.row.update(
        status="leaked",
        reason="Measured residual inference risk: exact counts permit single-patient differencing; no suppression or privacy budget is implemented.",
    )


def quasi_identifier_linkage(attempt):
    columns = ["patient_token", "age_band", "sex", "zip"]
    rows = attempt.sql(
        "SELECT patient_token, age_band, sex, zip FROM hospital.silver.patients", "researcher"
    )
    compare(
        attempt,
        rows,
        attempt.access.oracle.rows("researcher", "patients", columns),
        "patients",
        columns,
    )
    # Simulated attacker auxiliary data uses pre-write ground truth, not a
    # privileged SQL query or patient_token as a linkage feature.
    auxiliary = [
        [
            row["patient_token"],
            row["age_band"],
            row["sex"],
            masked_value("patients", "zip", row["zip"]),
        ]
        for row in attempt.access.oracle.data["patients"]
    ]
    metrics = linkage_metrics(rows, auxiliary)
    attempt.observe(
        quasi_identifiers=columns[1:],
        metrics=metrics,
        attacker_model="Exact auxiliary age band, sex and ZIP prefix; synthetic population only.",
    )
    if metrics["correct_links"]:
        attempt.row.update(
            status="leaked",
            reason="Measured residual linkage risk: unique released QIs identify synthetic ground-truth patients under the stated auxiliary-data model.",
        )
    else:
        attempt.row.update(
            status="inconclusive",
            reason="No unique correct links in this synthetic population; this does not prove anonymity.",
        )


def audit_collector_outage(attempt):
    """Stop the real collector during a known workload and reconcile the audit table."""
    compose = attempt.access.compose

    def stored(query_ids):
        output = compose(
            "run", "--rm", "--no-deps", "audit-writer", "--summary", *query_ids
        )
        return json.loads(next(line for line in output.splitlines() if line.startswith("[")))

    # Establish a working-path positive control before deliberately interrupting intake.
    attempt.sql("SELECT 1", "researcher")
    baseline_id = attempt.access.queries[-1]["query_id"]
    assert baseline_id, "Trino did not return a baseline query ID"
    deadline = time.monotonic() + 90
    baseline_rows = []
    baseline = {}
    while time.monotonic() < deadline:
        compose("run", "--rm", "--no-deps", "audit-writer", "--once")
        baseline_rows = stored([baseline_id])
        baseline = reconcile_query_events([baseline_id], baseline_rows)
        if baseline["coverage_percent"] == 100.0:
            break
        time.sleep(3)
    attempt.expect(
        baseline.get("coverage_percent") == 100.0,
        expected="Both QueryCreated and QueryCompleted are stored for the baseline query",
        observed=baseline,
        leak=False,
    )

    # Stop only the collector: Trino and the audit writer remain available, so
    # queries continue while event delivery is interrupted.
    compose("stop", "audit-collector")
    outage_started = datetime.now(UTC).isoformat()
    outage_ids = []
    for statement in ("SELECT 23", "SELECT 24", "SELECT 25"):
        attempt.sql(statement, "researcher")
        query_id = attempt.access.queries[-1]["query_id"]
        assert query_id, "Trino did not return a query ID during the collector outage"
        outage_ids.append(query_id)
    compose("up", "-d", "--wait", "audit-collector")
    outage_ended = datetime.now(UTC).isoformat()

    expected_ids = [baseline_id, *outage_ids]
    deadline = time.monotonic() + 180
    observed_rows = []
    reconciliation = {}
    while time.monotonic() < deadline:
        compose("run", "--rm", "--no-deps", "audit-writer", "--once")
        observed_rows = stored(expected_ids)
        reconciliation = reconcile_query_events(expected_ids, observed_rows)
        if (
            reconciliation["coverage_percent"] == 100.0
            and reconciliation["duplicate_event_count"] == 0
        ):
            break
        time.sleep(5)

    evidence = {
        "case": "audit_collector_outage",
        "measured_at_utc": datetime.now(UTC).isoformat(),
        "outage_started_utc": outage_started,
        "outage_ended_utc": outage_ended,
        "issued_query_ids": expected_ids,
        "observed_events": observed_rows,
        **reconciliation,
        "outage_behavior": {
            "acknowledged_before_stop": (
                "Baseline events were present before the collector was stopped."
            ),
            "during_stop": (
                "The collector could not acknowledge deliveries; query execution "
                "continued and the known query IDs were retained for reconciliation."
            ),
            "after_restart": (
                "The same bind-mounted spool was reused. Only deliveries retried "
                "successfully by Trino can be recovered; missing pairs remain visible."
            ),
        },
    }
    evidence_path = attempt.report.directory / "audit_collector_outage.json"
    evidence_path.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n")
    evidence_path.chmod(0o600)
    attempt.observe(**evidence)

    if reconciliation["missing_event_count"]:
        attempt.row.update(
            status="leaked",
            reason=(
                "The outage produced a measured audit-trail gap: "
                f"{reconciliation['missing_event_count']} lifecycle event(s) missing "
                f"across {len(reconciliation['missing_query_ids'])} query ID(s). "
                "See private audit_collector_outage.json evidence."
            ),
        )
    else:
        attempt.row.update(
            status="blocked",
            reason=(
                "All known queries have both lifecycle events after collector restart; "
                "the audit writer stored no duplicate query/event pairs."
            ),
        )


HANDLERS = {
    function.__name__: function
    for function in (
        anonymous_object_read,
        catalog_direct_access,
        doctor_other_patient,
        identity_impersonation,
        admin_operations,
        bronze_query,
        old_snapshot_query,
        join_or_view_bypass,
        metadata_enumeration,
        aggregate_inference,
        quasi_identifier_linkage,
        audit_collector_outage,
    )
}


CASES = [row["case"] for row in load_cases()]
if set(CASES) != set(HANDLERS):
    raise ValueError("Every cases.csv case must have exactly one bypass handler")


@pytest.mark.parametrize("case", CASES)
def test_bypass_case(bypass, case):
    with bypass(case) as attempt:
        HANDLERS[case](attempt)
