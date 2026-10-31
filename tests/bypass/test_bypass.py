"""Every inventory case produces evidence, including measured residual risks."""

import json
import os
import ssl
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from audit.reliability import reconcile_query_events
from tests.access.oracle import USERS, assert_rows, decisions, masked_value
from tests.access.test_access import forbidden_operations
from tests.integration.test_trino import query

from .privacy import k_anonymity_metrics, rare_reason_code_profiles
from .report import linkage_metrics, load_cases


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


def _privacy_source_population(attempt):
    """Return the Synthea source population, excluding the nine boundary fixtures."""
    oracle = attempt.access.oracle
    boundary_tokens = set(oracle.boundary["tokens"].values())
    source_patients = [
        row for row in oracle.data["patients"]
        if row.get("patient_token") not in boundary_tokens
    ]
    source_encounters = [
        row for row in oracle.data["encounters"]
        if row.get("patient_token") not in boundary_tokens
    ]
    if not source_patients:
        raise AssertionError("No source patients are available for privacy measurement")

    source_dir = Path(
        os.environ.get(
            "ACCESS_TEST_SOURCE",
            Path(__file__).resolve().parents[1] / "fixtures" / "synthea",
        )
    ).resolve()
    manifest = json.loads((source_dir / "manifest.json").read_text(encoding="utf-8"))
    requested_population = manifest.get("parameters", {}).get("population")
    if requested_population is not None and requested_population != len(source_patients):
        raise AssertionError("Synthea manifest population does not match the source oracle")
    return source_patients, source_encounters, boundary_tokens, manifest


def _privacy_evidence(attempt, case_name, metrics):
    """Write aggregate-only evidence for scale CI; patient-level data stays private."""
    path = attempt.report.directory / "privacy-risk-summary.json"
    payload = json.loads(path.read_text()) if path.exists() else {
        "scope": (
            "Synthea source patients only; adversarial policy-boundary fixture "
            "patients excluded. Synthetic measurements are not real-world rates."
        ),
        "cases": {},
    }
    payload["source"] = metrics["source"]
    payload["cases"][case_name] = {
        key: value for key, value in metrics.items() if key != "source"
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    path.chmod(0o600)


def aggregate_inference(attempt):
    source_patients, source_encounters, boundary_tokens, manifest = (
        _privacy_source_population(attempt)
    )
    population = len(source_patients)
    excluded_sql = (
        "TRUE" if not boundary_tokens else
        "patient_token NOT IN ("
        + ", ".join(f"'{token}'" for token in sorted(boundary_tokens))
        + ")"
    )

    source_records = [
        {
            "patient_token": row["patient_token"],
            "age_band": row.get("age_band"),
            "sex": row.get("sex"),
            "zip": masked_value("patients", "zip", row.get("zip")),
        }
        for row in source_patients
    ]
    profiles, rare_summary = rare_reason_code_profiles(
        source_patients, source_encounters, rare_threshold=5
    )
    rare_by_token = {
        row["patient_token"]: row["rare_reason_codes"] for row in profiles
    }
    for row in source_records:
        row["rare_reason_codes"] = rare_by_token[row["patient_token"]]

    # Independent source oracle: exact aggregates over the source patients only.
    expected_groups = Counter(
        (row["age_band"], row["sex"], row["zip"]) for row in source_records
    )
    sql_groups = attempt.sql(
        "SELECT age_band, sex, zip, count(*) "
        f"FROM hospital.silver.patients WHERE {excluded_sql} "
        "GROUP BY 1, 2, 3",
        "researcher",
    )
    observed_groups = {tuple(row[:3]): row[3] for row in sql_groups}
    attempt.expect(
        observed_groups == expected_groups,
        expected="Source-only demographic aggregates match independent masked ground truth",
        observed={"source_patients": population, "aggregate_groups": len(observed_groups)},
    )

    # A separate query validates that the rare-code prevalence calculation uses
    # the researcher-visible reason_code field, counting distinct patients.
    code_patients = {}
    for row in source_encounters:
        code = row.get("reason_code")
        if code is not None and str(code).strip():
            code_patients.setdefault(str(code).strip(), set()).add(row["patient_token"])
    expected_prevalence = sorted(
        (code, len(tokens)) for code, tokens in code_patients.items()
    )
    observed_prevalence = attempt.sql(
        "SELECT reason_code, count(DISTINCT patient_token) "
        f"FROM hospital.silver.encounters WHERE {excluded_sql} "
        "AND reason_code IS NOT NULL "
        "GROUP BY reason_code ORDER BY reason_code",
        "researcher",
    )
    attempt.expect(
        sorted((str(row[0]), row[1]) for row in observed_prevalence) == expected_prevalence,
        expected="Distinct-patient reason-code prevalence matches source oracle",
        observed={"distinct_reason_codes": len(observed_prevalence)},
    )

    qi_sets = {
        "age_band_sex": ("age_band", "sex"),
        "age_band_sex_masked_zip": ("age_band", "sex", "zip"),
        "age_band_sex_masked_zip_rare_reason_codes": (
            "age_band", "sex", "zip", "rare_reason_codes"
        ),
    }
    k_metrics = {}
    for name, columns in qi_sets.items():
        all_records = k_anonymity_metrics(source_records, columns, threshold=5)
        complete_records = [
            row for row in source_records
            if all(row.get(column) is not None for column in columns)
        ]
        k_metrics[name] = {
            "all_records": all_records,
            "complete_case_only": k_anonymity_metrics(
                complete_records, columns, threshold=5
            ),
        }

    selected_token = sorted(row["patient_token"] for row in source_patients)[0]
    total = attempt.sql(
        "SELECT count(*) FROM hospital.silver.patients "
        f"WHERE {excluded_sql}",
        "researcher",
    )[0][0]
    excluded = attempt.sql(
        "SELECT count(*) FROM hospital.silver.patients "
        f"WHERE {excluded_sql} AND patient_token <> '{selected_token}'",
        "researcher",
    )[0][0]
    attempt.expect(
        total == population and total - excluded == 1,
        expected="Exact-count differencing identifies a single selected source record",
        observed={"source_population": total, "count_after_exclusion": excluded},
    )

    groups_below_five = sum(
        1 for count in expected_groups.values() if count < 5
    )
    records_in_small_groups = sum(
        count for count in expected_groups.values() if count < 5
    )
    summary = {
        "source": {
            "source": manifest.get("parameters", {}).get("source", "unknown"),
            "source_version": manifest.get("parameters", {}).get("version"),
            "population_requested": manifest.get("parameters", {}).get("population"),
            "population_measured": population,
            "seed": manifest.get("parameters", {}).get("seed"),
            "reference_date": manifest.get("parameters", {}).get("reference_date"),
            "boundary_fixture_patients_excluded": len(boundary_tokens),
        },
        "quasi_identifier_metrics": k_metrics,
        "rare_reason_code_summary": rare_summary,
        "aggregate_inference": {
            "source_population": population,
            "demographic_aggregate_groups": len(expected_groups),
            "groups_with_count_below_5": groups_below_five,
            "records_in_groups_below_5": records_in_small_groups,
            "records_in_groups_below_5_percent": round(
                100.0 * records_in_small_groups / population, 2
            ),
            "exact_count_differencing": {
                "source_total": total,
                "count_after_excluding_one_selected_record": excluded,
                "disclosed_count_difference": total - excluded,
                "single_record_presence_disclosed": total - excluded == 1,
            },
        },
        "thresholds": {
            "k_anonymity_threshold": 5,
            "rare_reason_code_distinct_patient_threshold": 5,
            "small_aggregate_cell_threshold": 5,
        },
        "interpretation": (
            "Synthetic source-only measurements. k-anonymity and small-cell "
            "counts describe the released quasi-identifiers; they are not a "
            "formal privacy guarantee. reason_code is only a condition-like "
            "encounter attribute available in this schema."
        ),
    }
    attempt.observe(**summary)
    _privacy_evidence(attempt, "aggregate_inference", summary)


def quasi_identifier_linkage(attempt):
    source_patients, source_encounters, boundary_tokens, manifest = (
        _privacy_source_population(attempt)
    )
    population = len(source_patients)
    rows = attempt.sql(
        "SELECT patient_token, age_band, sex, zip "
        "FROM hospital.silver.patients",
        "researcher",
    )
    source_rows = [row for row in rows if row[0] not in boundary_tokens]
    actual = {row[0]: tuple(row[1:]) for row in source_rows}
    expected = {
        row["patient_token"]: (
            row.get("age_band"),
            row.get("sex"),
            masked_value("patients", "zip", row.get("zip")),
        )
        for row in source_patients
    }
    attempt.expect(
        actual == expected,
        expected="Researcher-visible demographic QIs match the source-only masked oracle",
        observed={"source_patients": population, "source_rows_returned": len(actual)},
    )

    source_records = [
        {
            "patient_token": row["patient_token"],
            "age_band": row.get("age_band"),
            "sex": row.get("sex"),
            "zip": masked_value("patients", "zip", row.get("zip")),
        }
        for row in source_patients
    ]
    profiles, rare_summary = rare_reason_code_profiles(
        source_patients, source_encounters, rare_threshold=5
    )
    rare_by_token = {
        row["patient_token"]: row["rare_reason_codes"] for row in profiles
    }
    for row in source_records:
        row["rare_reason_codes"] = rare_by_token[row["patient_token"]]

    demographic_linkage = linkage_metrics(
        [
            (row["patient_token"], row["age_band"], row["sex"], row["zip"])
            for row in source_records
        ],
        [
            (row["patient_token"], row["age_band"], row["sex"], row["zip"])
            for row in source_records
        ],
    )
    condition_profile_linkage = linkage_metrics(
        [
            (
                row["patient_token"],
                row["age_band"],
                row["sex"],
                row["zip"],
                row["rare_reason_codes"],
            )
            for row in source_records
        ],
        [
            (
                row["patient_token"],
                row["age_band"],
                row["sex"],
                row["zip"],
                row["rare_reason_codes"],
            )
            for row in source_records
        ],
    )
    summary = {
        "source": {
            "source": manifest.get("parameters", {}).get("source", "unknown"),
            "source_version": manifest.get("parameters", {}).get("version"),
            "population_requested": manifest.get("parameters", {}).get("population"),
            "population_measured": population,
            "seed": manifest.get("parameters", {}).get("seed"),
            "reference_date": manifest.get("parameters", {}).get("reference_date"),
            "boundary_fixture_patients_excluded": len(boundary_tokens),
        },
        "quasi_identifiers": ["age_band", "sex", "masked_zip_prefix"],
        "attacker_model": (
            "Attacker has exact auxiliary quasi-identifiers for the same synthetic "
            "cohort; HMAC token is used only to score correctness, never as a feature."
        ),
        "demographic_linkage": demographic_linkage,
        "demographic_plus_rare_reason_codes_linkage": condition_profile_linkage,
        "rare_reason_code_summary": rare_summary,
        "interpretation": (
            "Synthetic source-only linkage simulation. A successful match is "
            "a measured linkage under the stated auxiliary-data model, not a "
            "real-world identification probability or anonymity assessment."
        ),
    }
    attempt.observe(**summary)
    _privacy_evidence(attempt, "quasi_identifier_linkage", summary)
    if demographic_linkage["correct_links"] or condition_profile_linkage["correct_links"]:
        attempt.row.update(
            status="leaked",
            reason=(
                "Measured residual linkage risk: one or more source-only "
                "quasi-identifier profiles uniquely matched synthetic ground truth."
            ),
        )
    else:
        attempt.row.update(
            status="inconclusive",
            reason=(
                "No correct unique links were measured in this synthetic population; "
                "this does not prove anonymity."
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
