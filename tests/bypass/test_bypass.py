"""Every inventory case produces evidence, including measured residual risks."""

import json
import ssl
import time
from datetime import UTC, datetime

import httpx
import pytest

from audit.reliability import reconcile_query_events
from tests.access.oracle import USERS, assert_rows, decisions, masked_value
from tests.access.test_access import forbidden_operations
from tests.integration.test_trino import query

from .privacy import (
    aggregate_inference_metrics as privacy_aggregate_metrics,
)
from .privacy import (
    equivalence_class_metrics,
    quasi_identifier_linkage_metrics,
    with_rare_condition_signatures,
)
from .report import load_cases, private_output


def save_privacy_summary(attempt, filename, payload):
    """Write only aggregate disclosure metrics for safe CI artifact publication."""
    path = attempt.report.directory / filename
    with private_output(path) as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)
        handle.write("\n")
    path.chmod(0o600)


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
        for user in ("admin", "doctor_alice", "doctor_bob", "researcher"):
            # #46: researcher has no row-level ZIP; check the token projection instead.
            if table == "encounters":
                columns = ["patient_token", "start_time"]
            elif user == "researcher":
                columns = ["patient_token"]
            else:
                columns = ["patient_token", "zip"]
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


RELEASES = {
    "researcher_demographics": ["age_band", "sex", "race", "patient_count"],
    "researcher_geography": ["zip_prefix", "patient_count"],
    "researcher_conditions": ["reason_code", "patient_count"],
}
MIN_CELL = 11
FORMER_QIS = ["age_band", "sex", "zip"]
RARE_CONDITION = {
    "field": "encounters.reason_code",
    "unit": "distinct patients",
    "threshold_percent": 5,
    "threshold_count": "max(1, ceil(population * 0.05))",
}


def former_projection(oracle):
    """The pre-#46 researcher view, rebuilt from the independent oracle (no live query)."""
    return [
        {
            "patient_token": row["patient_token"],
            "age_band": row["age_band"],
            "sex": row["sex"],
            "zip": masked_value("patients", "zip", row["zip"]),
        }
        for row in oracle.data["patients"]
    ]


def read_releases(attempt):
    """Read every #46 release as researcher; exact oracle match and >= 11 per cell."""
    summary = {}
    for table, columns in RELEASES.items():
        rows = attempt.sql(f"SELECT {', '.join(columns)} FROM hospital.gold.{table}", "researcher")
        compare(
            attempt, rows, attempt.access.oracle.rows("researcher", table, columns), table, columns
        )
        counts = [row[-1] for row in rows]
        attempt.expect(
            all(count >= MIN_CELL for count in counts),
            expected=f"Every published cell has at least {MIN_CELL} patients",
            observed={"table": table, "cells": len(rows), "min_count": min(counts, default=None)},
        )
        shown = [
            row[0] for row in attempt.sql(f"SHOW COLUMNS FROM hospital.gold.{table}", "researcher")
        ]
        attempt.expect(
            "patient_token" not in shown,
            expected="Release has no record-level key to exclude one patient",
            observed={"table": table, "columns": shown},
        )
        summary[table] = {
            "published_cells": len(rows),
            "min_patient_count": min(counts, default=None),
            "patients_in_published_cells": sum(counts),
        }
    return summary


def aggregate_inference(attempt):
    oracle = attempt.access.oracle
    population = len(oracle.data["patients"])
    # Before #46: what the former row-level researcher projection exposed.
    former = former_projection(oracle)
    before = {
        "k_anonymity": equivalence_class_metrics(former, FORMER_QIS),
        "k_anonymity_with_rare_condition_signature": equivalence_class_metrics(
            with_rare_condition_signatures(former, oracle.data["encounters"], threshold_percent=5),
            [*FORMER_QIS, "rare_condition_signature"],
        ),
        "aggregate_inference": privacy_aggregate_metrics(former, quasi_identifiers=FORMER_QIS),
    }
    # After #46: the same attack queries are denied for the researcher.
    target = oracle.boundary["tokens"]["alice"]
    for statement in (
        "SELECT patient_token, age_band, sex, zip FROM hospital.silver.patients",
        "SELECT age_band, sex, zip, count(*) FROM hospital.silver.patients GROUP BY 1, 2, 3",
        f"SELECT count(*) FROM hospital.silver.patients WHERE age_band = '00-09' AND patient_token <> '{target}'",
        "SELECT patient_token, reason_code FROM hospital.silver.encounters",
        "SELECT reason_code, count(DISTINCT patient_token) FROM hospital.silver.encounters GROUP BY 1",
    ):
        attempt.sql(statement, "researcher", denied=True)
    releases = read_releases(attempt)
    after = {
        "row_level_quasi_identifiers_visible": [],
        "releases": releases,
        "min_published_cell": min(
            (r["min_patient_count"] for r in releases.values() if r["min_patient_count"]),
            default=None,
        ),
        "exact_count_differencing": (
            "not possible: releases have no patient key or record-level predicate, and "
            "any cell that would fall below the threshold is absent rather than counted"
        ),
    }
    attempt.observe(population=population, before_mitigation=before, after_mitigation=after)
    save_privacy_summary(
        attempt,
        "issue-24-aggregate-inference.json",
        {
            "issue": [24, 46],
            "case": "aggregate_inference",
            "population": population,
            "min_cell": MIN_CELL,
            "quasi_identifiers_before": FORMER_QIS,
            "rare_condition_definition": RARE_CONDITION,
            "before_mitigation": before,
            "after_mitigation": after,
        },
    )
    attempt.row.update(
        status="blocked",
        reason=(
            "Mitigated by #46: row-level quasi-identifiers and reason codes are denied to "
            f"research; aggregate releases publish only cells with >= {MIN_CELL} patients "
            "and carry no patient key. Before/after metrics are in the private report."
        ),
    )


def quasi_identifier_linkage(attempt):
    oracle = attempt.access.oracle
    # Attacker's auxiliary data: exact age band, sex, ZIP prefix and rare conditions.
    auxiliary = former_projection(oracle)
    auxiliary_extended = with_rare_condition_signatures(
        auxiliary, oracle.data["encounters"], threshold_percent=5
    )
    extended_qis = [*FORMER_QIS, "rare_condition_signature"]
    # Before #46 the researcher saw exactly these values row by row.
    before = {
        "baseline_linkage": quasi_identifier_linkage_metrics(
            auxiliary, auxiliary, quasi_identifiers=FORMER_QIS
        ),
        "linkage_with_rare_condition_signature": quasi_identifier_linkage_metrics(
            auxiliary_extended, auxiliary_extended, quasi_identifiers=extended_qis
        ),
    }
    # After #46: no researcher-visible row carries any linkage key.
    visible = {
        table: [
            row[0]
            for row in attempt.sql(f"SHOW COLUMNS FROM hospital.silver.{table}", "researcher")
        ]
        for table in ("patients", "encounters")
    }
    exposed = (set(FORMER_QIS) | {"race", "ethnicity", "birthdate"}) & set(visible["patients"])
    exposed |= {"reason_code", "reason_description"} & set(visible["encounters"])
    attempt.expect(
        not exposed,
        expected="No row-level quasi-identifier or reason code visible to research",
        observed=visible,
    )
    attempt.sql(
        "SELECT patient_token, age_band, sex, zip FROM hospital.silver.patients",
        "researcher",
        denied=True,
    )
    attempt.sql(
        "SELECT patient_token, reason_code FROM hospital.silver.encounters",
        "researcher",
        denied=True,
    )
    tokens = [
        row[0]
        for row in attempt.sql("SELECT patient_token FROM hospital.silver.patients", "researcher")
    ]
    released_now = [{"patient_token": token, **dict.fromkeys(FORMER_QIS)} for token in tokens]
    released_now_extended = [{**row, "rare_condition_signature": None} for row in released_now]
    releases = read_releases(attempt)
    min_cell = min(
        (r["min_patient_count"] for r in releases.values() if r["min_patient_count"]),
        default=None,
    )
    after = {
        "baseline_linkage": quasi_identifier_linkage_metrics(
            released_now, auxiliary, quasi_identifiers=FORMER_QIS
        ),
        "linkage_with_rare_condition_signature": quasi_identifier_linkage_metrics(
            released_now_extended, auxiliary_extended, quasi_identifiers=extended_qis
        ),
        "release_best_case_match_precision_percent": (100.0 / min_cell if min_cell else None),
    }
    attempt.observe(before_mitigation=before, after_mitigation=after, visible_columns=visible)
    save_privacy_summary(
        attempt,
        "issue-24-quasi-identifier-linkage.json",
        {
            "issue": [24, 46],
            "case": "quasi_identifier_linkage",
            "population": len(auxiliary),
            "min_cell": MIN_CELL,
            "quasi_identifiers": extended_qis,
            "rare_condition_definition": RARE_CONDITION,
            "before_mitigation": before,
            "after_mitigation": after,
            "attacker_model": (
                "Exact auxiliary age band, sex, ZIP prefix and uncommon reason-code "
                "signatures; synthetic population only."
            ),
        },
    )
    linked = (
        after["baseline_linkage"]["correct_links"]
        + after["linkage_with_rare_condition_signature"]["correct_links"]
    )
    attempt.expect(
        linked == 0,
        expected="No unique correct links after mitigation",
        observed={"correct_links": linked},
    )
    attempt.row.update(
        status="blocked",
        reason=(
            "Mitigated by #46: research rows carry only the pseudonymous token, so the "
            "auxiliary quasi-identifiers match nothing; aggregate cells hold at least "
            f"{MIN_CELL} patients. Synthetic population only; not an anonymity proof."
        ),
    )


def audit_collector_outage(attempt):
    """Stop the real collector during a known workload and reconcile the audit table."""
    compose = attempt.access.compose

    def stored(query_ids):
        output = compose("run", "--rm", "--no-deps", "audit-writer", "--summary", *query_ids)
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
