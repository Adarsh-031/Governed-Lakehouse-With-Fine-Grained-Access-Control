"""Transform edge cases and fail-closed loading without external services."""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
import pyarrow as pa
import pytest
import yaml

from ingest import pipeline as etl
from ingest.tokenization import patient_token
from scripts.source_data import csv_inventory

KEY = b"a" * 32
REFERENCE = date(2025, 1, 1)


@pytest.fixture
def frames():
    rows = {
        "patients": [
            ["p1", "1980-02-29", "F", "NA", "test", "02139"],
            ["p2", None, "M", None, None, "invalid"],
        ],
        "encounters": [
            [
                "e1",
                "p1",
                "2024-01-01T01:00:00.123456+01:00",
                None,
                "ambulatory",
                "00123",
                "visit",
                None,
                None,
            ],
            ["e2", "p2", None, None, None, None, None, None, None],
        ],
        "claims": [["c1", "p1", "e1", "payer1", "payer2"]],
        "claims_transactions": [
            ["t1", "c1", "0", "p1", "e1", "CHARGE", "100.01", None, "00456", "procedure", "0.00"],
            ["t2", "c1", "1", "p1", "e1", "PAYMENT", None, "COPAY", "00456", "procedure", "10.00"],
            ["t3", "c1", "2", "p1", "e1", "PAYMENT", None, "ECHECK", "00456", "procedure", "60.00"],
            ["t4", "c1", "3", "p1", "e1", "TRANSFEROUT", None, None, "00456", "procedure", "0.00"],
            [
                "t5",
                "c1",
                "4",
                "p1",
                "e1",
                "TRANSFERIN",
                "30.01",
                None,
                "00456",
                "procedure",
                "0.00",
            ],
            ["t6", "c1", "5", "p1", "e1", "PAYMENT", None, "ECHECK", "00456", "procedure", "20.00"],
            ["t7", "c1", "6", "p1", "e1", "PAYMENT", None, "CASH", "00456", "procedure", "10.01"],
            ["t8", "c1", "10", "p1", "e1", "CHARGE", "2.99", None, "00789", "second", "0.00"],
            ["t9", "c1", "11", "p1", "e1", "PAYMENT", None, "CC", "00789", "second", "2.99"],
        ],
        "payers": [["payer1", "Primary"], ["payer2", "Secondary"]],
    }
    return {
        name: pd.DataFrame(value, columns=etl.SOURCE_COLUMNS[name].split(), dtype="string")
        for name, value in rows.items()
    }


def prepare(frames):
    return etl.transform(frames, KEY, "v1", REFERENCE)


def test_native_mapping_preserves_types_and_reconciles_source_counts(frames):
    data = prepare(frames)
    token = patient_token("synthea:p1", KEY)
    p = data.tables["patients"].to_pylist()
    e = data.tables["encounters"].to_pylist()
    c = data.tables["claims"].to_pylist()
    assert p[0] == {
        "patient_token": token,
        "birthdate": date(1980, 2, 29),
        "age_band": "40-49",
        "sex": "F",
        "race": "NA",
        "ethnicity": "test",
        "zip": "02139",
    }
    assert p[1]["age_band"] is None and p[1]["zip"] is None
    assert e[0]["patient_token"] == c[0]["patient_token"] == token
    assert e[0]["start_time"] == datetime(2024, 1, 1, 0, 0, 0, 123456)
    assert e[0]["stop_time"] is None and e[0]["code"] == "00123"
    assert c[0] == {
        "claim_id": "t1",
        "patient_token": token,
        "encounter_id": "e1",
        "payer": "Primary",
        "procedure_code": "00456",
        "procedure_description": "procedure",
        "total_cost": Decimal("100.01"),
        "payer_coverage": Decimal("80.00"),
    }
    assert c[1]["total_cost"] == Decimal("2.99") and c[1]["payer_coverage"] == 0
    assert data.report["rows"] == {"patients": 2, "encounters": 2, "claims": 2}
    assert data.report["source_rows"]["claims"] == 1
    assert data.report["claims_headers_with_charges"] == 1
    # Row order cannot change line attribution; sequence is numeric, not lexical.
    frames["claims_transactions"] = frames["claims_transactions"].sample(frac=1, random_state=42)
    assert prepare(frames).tables["claims"].equals(data.tables["claims"])


@pytest.mark.parametrize(
    "born,ref,expected",
    [
        (None, "2025-01-01", None),
        ("2025-01-02", "2025-01-01", None),
        ("1935-01-01", "2025-01-01", "90+"),
        ("1935-01-02", "2025-01-01", "80-89"),
        ("2004-02-29", "2024-02-29", "20-29"),
        ("2004-02-29", "2025-02-28", "20-29"),
        ("2004-02-29", "2014-02-28", "00-09"),
        ("2004-02-29", "2014-03-01", "10-19"),
    ],
)
def test_age_bands_use_calendar_anniversary(born, ref, expected):
    assert (
        etl.age_band(date.fromisoformat(born) if born else None, date.fromisoformat(ref))
        == expected
    )


@pytest.mark.parametrize(
    "table,column,row,value,rule",
    [
        ("patients", "Id", 0, None, "required key"),
        ("patients", "Id", 0, "p2", "duplicate key"),
        ("patients", "BIRTHDATE", 0, "1980-02-30", "invalid date"),
        ("encounters", "PATIENT", 0, "missing", "orphan patient"),
        ("encounters", "START", 0, "2024-01-01T00:00:00", "timezone-qualified"),
        ("encounters", "START", 0, "2024-01-01T00:00:00.1234567Z", "timezone-qualified"),
        ("encounters", "START", 0, "2024-02-30T00:00:00Z", "invalid timestamp"),
        ("encounters", "STOP", 0, "2023-01-01T00:00:00Z", "reversed interval"),
        ("claims", "PATIENTID", 0, "missing", "orphan patient"),
        ("claims", "PATIENTID", 0, "p2", "encounter patient mismatch"),
        ("claims", "APPOINTMENTID", 0, "missing", "orphan encounter"),
        ("claims", "PRIMARYPATIENTINSURANCEID", 0, "missing", "orphan payer"),
        ("claims_transactions", "CLAIMID", 0, "missing", "orphan claim"),
        ("claims_transactions", "PATIENTID", 0, "p2", "PATIENTID mismatch"),
        ("claims_transactions", "APPOINTMENTID", 0, "e2", "APPOINTMENTID mismatch"),
        ("claims_transactions", "TYPE", 0, "unknown", "unknown type"),
        ("claims_transactions", "CHARGEID", 0, "1", "duplicate sequence"),
        ("claims_transactions", "CHARGEID", 0, "0.1", "invalid sequence"),
        ("claims_transactions", "CHARGEID", 0, "99", "payment before charge"),
        ("claims_transactions", "PROCEDURECODE", 1, "bad", "inconsistent line"),
        ("claims_transactions", "METHOD", 1, "unknown", "unknown payment method"),
        ("claims_transactions", "AMOUNT", 0, "1.001", "invalid cents"),
        ("claims_transactions", "AMOUNT", 0, "10000000000000000.00", "invalid cents"),
        ("claims_transactions", "AMOUNT", 0, "NaN", "invalid cents"),
        ("claims_transactions", "AMOUNT", 0, "-1.00", "invalid cents"),
        ("claims_transactions", "PAYMENTS", 1, None, "required amount"),
    ],
)
def test_invalid_source_fails_without_echoing_values(frames, table, column, row, value, rule):
    frames[table].loc[row, column] = value
    with pytest.raises(etl.ValidationError, match=rule):
        prepare(frames)


def test_missing_charge_and_uninsured_patient(frames):
    frames["claims_transactions"] = frames["claims_transactions"].iloc[0:0]
    with pytest.raises(etl.ValidationError, match="missing charge"):
        prepare(frames)
    # Headers can have no primary insurance; preserve NULL, not fabricated payer.


def test_uninsured_payer_is_null(frames):
    frames["claims"].loc[0, "PRIMARYPATIENTINSURANCEID"] = pd.NA
    assert prepare(frames).tables["claims"].to_pylist()[0]["payer"] is None


def test_schema_matches_sql_inventory():
    spec = yaml.safe_load(Path("policies/schema-contract.yaml").read_text())["tables"]
    types = {
        "VARCHAR": pa.large_string(),
        "DATE": pa.date32(),
        "TIMESTAMP(6)": pa.timestamp("us"),
        "DECIMAL(18,2)": pa.decimal128(18, 2),
    }
    for name, schema in etl.SCHEMAS.items():
        assert schema.names == list(spec[name]["columns"])
        assert [field.type for field in schema] == [
            types[t] for t in spec[name]["columns"].values()
        ]


def test_prepare_checks_manifest_and_drops_unknown_columns(frames, tmp_path):
    for name, frame in frames.items():
        frame["SECRET_EXTRA"] = "do-not-copy"
        frame.to_csv(tmp_path / f"{name}.csv", index=False)
    manifest = {
        "parameters": {
            "source": "synthea",
            "version": "3.4.0",
            "population": 2,
            "reference_date": "20250101",
        },
        "tables": csv_inventory(tmp_path),
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    data = etl.prepare(tmp_path, KEY, "v1")
    assert "do-not-copy" not in repr(data.tables)
    with (tmp_path / "patients.csv").open("a") as handle:
        handle.write("tampered\n")
    with pytest.raises(ValueError):
        etl.prepare(tmp_path, KEY, "v1")


def fake_catalog(data):
    tables = {}
    catalog = Mock()
    catalog.table_exists.side_effect = lambda identifier: identifier[1] in tables
    catalog.load_table.side_effect = lambda identifier: tables[identifier[1]]

    def create(identifier, schema, **kwargs):
        table = Mock()
        table.schema.return_value.as_arrow.return_value = schema
        table.metadata.format_version = 2
        table.spec.return_value.is_unpartitioned.return_value = True
        table.location.return_value = "s3://warehouse/silver/test"
        table.data = pa.Table.from_batches([], schema=schema)
        table.scan.return_value.to_arrow.side_effect = lambda: table.data
        table.append.side_effect = lambda arrow, **kwargs: setattr(table, "data", arrow)
        tables[identifier[1]] = table
        return table

    catalog.create_table.side_effect = create
    return catalog, tables


def test_load_rerun_partial_resume_and_changed_data_rejection(frames):
    data = prepare(frames)
    catalog, tables = fake_catalog(data)
    assert set(etl.load(data, catalog)["actions"].values()) == {"loaded"}
    assert set(etl.load(data, catalog)["actions"].values()) == {"verified existing"}
    assert all(table.append.call_count == 1 for table in tables.values())
    tables["claims"].data = data.tables["claims"].slice(0, 0)
    etl.load(data, catalog)
    assert tables["claims"].append.call_count == 2
    frames["patients"].loc[0, "GENDER"] = "changed"
    with pytest.raises(etl.ValidationError, match="existing data differs"):
        etl.load(prepare(frames), catalog)
    assert tables["patients"].append.call_count == 1


def test_all_tables_preflight_before_mutation(frames):
    data = prepare(frames)
    catalog, tables = fake_catalog(data)
    etl.load(data, catalog)
    tables["patients"].data = data.tables["patients"].slice(0, 0)
    tables["claims"].schema.return_value.as_arrow.return_value = pa.schema([])
    with pytest.raises(etl.ValidationError, match="incompatible existing schema"):
        etl.load(data, catalog)
    assert tables["patients"].append.call_count == 1


def test_key_validation_and_secret_safe_cli(frames, monkeypatch, capsys):
    monkeypatch.setenv("HMAC_KEY_HEX", KEY.hex())
    monkeypatch.setenv("HMAC_KEY_VERSION", "v1")
    assert etl.key_from_environment() == (KEY, "v1")
    monkeypatch.setattr("sys.argv", ["ingest"])
    monkeypatch.setattr(etl, "prepare", Mock(side_effect=RuntimeError(KEY.hex())))
    with pytest.raises(SystemExit):
        etl.main()
    output = capsys.readouterr()
    assert KEY.hex() not in output.err + output.out
    assert "RuntimeError" in output.err
    monkeypatch.setenv("HMAC_KEY_HEX", "invalid-secret-value")
    with pytest.raises(etl.ValidationError, match="encode 32 bytes"):
        etl.key_from_environment()


def test_compose_grants_only_etl_secrets_and_readonly_source():
    config = yaml.safe_load(Path("compose.yaml").read_text())["services"]["ingest"]
    assert set(config["environment"]) == {
        "ETL_OIDC_CLIENT_SECRET",
        "ETL_STORAGE_ACCESS_KEY",
        "ETL_STORAGE_SECRET_KEY",
        "HMAC_KEY_HEX",
        "HMAC_KEY_VERSION",
    }
    assert config["networks"] == ["backend"] and not config.get("ports")
    assert config["read_only"] and config["volumes"][0].endswith(":/source:ro")


@pytest.mark.parametrize(
    "table,column",
    [
        ("claims_transactions", "PATIENTID"),
        ("claims_transactions", "APPOINTMENTID"),
        ("claims_transactions", "TYPE"),
    ],
)
def test_null_transaction_references_and_types_fail_closed(frames, table, column):
    frames[table].loc[0, column] = pd.NA
    with pytest.raises(etl.ValidationError):
        prepare(frames)


def test_launcher_resolves_paths_and_forwards_pipeline_options(tmp_path):
    import os
    import subprocess

    docker = tmp_path / "docker"
    docker.write_text('#!/bin/sh\nprintf "%s\\n" "$PWD" "$INGEST_SOURCE_DIR" "$@"\n')
    docker.chmod(0o755)
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, "PATH": str(tmp_path) + os.pathsep + os.environ["PATH"]}
    env.pop("INGEST_SOURCE_DIR", None)
    env.pop("LAKEHOUSE_ENV_FILE", None)
    output = subprocess.check_output(
        [str(root / "scripts/ingest.sh"), "--validate-only"], env=env, cwd=tmp_path, text=True
    ).splitlines()
    assert output[:2] == [str(root), str(root / "data/raw/synthea/1000-seed42")]
    assert output[2:] == [
        "compose",
        "--env-file",
        "data/secrets/runtime.env",
        "run",
        "--build",
        "--rm",
        "ingest",
        "-m",
        "ingest.pipeline",
        "--validate-only",
    ]
