"""Trusted ETL-only boundary fixtures; never grant SQL users provisioning rights."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow as pa

from ingest.pipeline import SCHEMAS, etl_catalog, key_from_environment, prepare


def token(name):
    return "v1:" + hashlib.sha256(f"issue18:{name}".encode()).hexdigest()


def main():
    today = datetime.now(UTC).date()
    yesterday, tomorrow = today - timedelta(days=1), today + timedelta(days=1)
    names = [
        "alice",
        "bob",
        "shared",
        "future",
        "expired",
        "invalid",
        "empty",
        "missing",
        "duplicate",
    ]
    # Literal expectations are saved before consulting/writing authorization data.
    oracle = {
        "utc_date": today.isoformat(),
        "expected_patient_tokens": {
            "doctor_alice": sorted(token(n) for n in ("alice", "shared", "duplicate")),
            "doctor_bob": sorted(token(n) for n in ("bob", "shared")),
        },
        "tokens": {name: token(name) for name in names},
    }
    path = Path("/evidence/policy-boundary-oracle.json")
    path.write_text(json.dumps(oracle))
    path.chmod(0o600)
    zip_values = ["02139", "02139-1234", None, "ABC12", "123456", "", "1234", "99999", "00000"]
    patients, encounters = [], []
    for index, name in enumerate(names):
        patients.append(
            {
                "patient_token": token(name),
                "birthdate": today,
                "age_band": "00-09",
                "sex": "F",
                "race": None,
                "ethnicity": None,
                "zip": zip_values[index],
            }
        )
        encounters.append(
            {
                "encounter_id": f"issue18-{name}",
                "patient_token": token(name),
                "start_time": None
                if name == "missing"
                else datetime(2026, 10, 28, 12, 34, 56, 123456),
                "stop_time": None if index % 2 == 0 else datetime(2026, 11, 2, 1, 2, 3, 456789),
                "encounter_class": "synthetic",
                "code": "00123",
                "description": "restricted fixture",
                "reason_code": "00456",
                "reason_description": "restricted fixture",
            }
        )
    assignments = []
    for doctor, name, start, end in [
        ("doctor_alice", "alice", today, tomorrow),
        ("doctor_bob", "bob", today, None),
        ("doctor_alice", "shared", yesterday, None),
        ("doctor_bob", "shared", yesterday, None),
        ("doctor_alice", "future", tomorrow, None),
        ("doctor_bob", "future", tomorrow, None),
        ("doctor_alice", "expired", yesterday, today),
        ("doctor_bob", "expired", yesterday, today),
        ("doctor_alice", "invalid", tomorrow, yesterday),
        ("doctor_bob", "invalid", tomorrow, yesterday),
        ("doctor_alice", "empty", today, today),
        ("doctor_alice", "duplicate", yesterday, None),
        ("doctor_alice", "duplicate", yesterday, None),
    ]:
        assignments.append(
            {
                "doctor_user": doctor,
                "patient_token": token(name),
                "valid_from": start,
                "valid_to": end,
            }
        )
    claims = [
        {
            "claim_id": f"issue18-{name}",
            "patient_token": token(name),
            "encounter_id": f"issue18-{name}",
            "payer": "fixture payer",
            "procedure_code": "00789",
            "procedure_description": "restricted fixture",
            "total_cost": None,
            "payer_coverage": None,
        }
        for name in names
    ]
    fixture_rows = {
        "patients": patients,
        "encounters": encounters,
        "claims": claims,
        "care_team": assignments,
    }
    # Capture unfiltered input values before catalog access. Access expectations
    # apply separate Python semantics, never the deployed SQL masks or live grants.
    key, version = key_from_environment()
    prepared = prepare(Path("/source"), key, version)
    data_oracle = {
        name: [*prepared.tables[name].to_pylist(), *rows] for name, rows in fixture_rows.items()
    }
    data_oracle["audit"] = [
        {"query_id": "synthetic-query", "query_text_restricted": "restricted text"}
    ]
    data_path = Path("/evidence/access-data-oracle.json")
    data_path.write_text(json.dumps(data_oracle, default=str))
    data_path.chmod(0o600)
    clinical = etl_catalog()
    for name, rows in fixture_rows.items():
        clinical.load_table(("silver", name)).append(
            pa.Table.from_pylist(rows, schema=SCHEMAS[name])
        )
    # Isolated audit SQL fixture uses the existing clinical warehouse via a test-only
    # catalog alias. It is not an audit read grant or a production audit pipeline.
    clinical.create_namespace("audit")
    schema = pa.schema(
        [
            ("query_id", pa.large_string()),
            ("event_type", pa.large_string()),
            ("authenticated_user", pa.large_string()),
            ("groups", pa.list_(pa.large_string())),
            ("query_text_restricted", pa.large_string()),
            ("tables", pa.list_(pa.large_string())),
            ("columns", pa.list_(pa.large_string())),
            ("output_rows_if_available", pa.int64()),
            ("status", pa.large_string()),
            ("error_code", pa.large_string()),
            ("event_timestamp", pa.timestamp("us")),
            ("received_timestamp", pa.timestamp("us")),
            ("policy_version", pa.large_string()),
            ("policy_digest", pa.large_string()),
        ]
    )
    audit = clinical.create_table(("audit", "query_events"), schema)
    audit.append(
        pa.Table.from_pylist(
            [{"query_id": "synthetic-query", "query_text_restricted": "restricted text"}],
            schema=schema,
        )
    )
    clinical.create_namespace("identity")
    identity = clinical.create_table(("identity", "patient_identity"), SCHEMAS["identity"])
    identity.append(
        pa.Table.from_pylist(
            [{"patient_token": token("alice"), "name": "forbidden fixture"}],
            schema=SCHEMAS["identity"],
        )
    )
    # A real, existing bronze resource ensures SQL negatives cannot pass merely
    # because no bronze table was registered. Production service grants stay as-is.
    clinical.create_namespace("bronze")
    bronze = clinical.create_table(("bronze", "access_probe"), pa.schema([("value", pa.string())]))
    bronze.append(pa.table({"value": ["restricted synthetic bronze sentinel"]}))
    oracle["snapshots"] = {
        name: clinical.load_table(("silver", name)).current_snapshot().snapshot_id
        for name in ("patients", "encounters")
    }
    path.write_text(json.dumps(oracle))
    print("PASS: independent policy fixtures and expected sets saved")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:  # noqa: BLE001 - sanitize SDK/identity output
        raise SystemExit(f"Policy fixture failed: {type(error).__name__}") from None
