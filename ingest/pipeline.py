"""Validate the pinned Synthea export and load the approved silver inventory."""

import argparse
import hashlib
import json
import os
import random
import re
import tempfile
import warnings
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path

import pandas as pd
import pyarrow as pa
from pyiceberg.catalog import load_catalog

from ingest.release import publish
from ingest.tokenization import patient_token
from scripts.source_data import csv_inventory

SOURCE_COLUMNS = {
    "patients": "Id BIRTHDATE GENDER RACE ETHNICITY ZIP FIRST MIDDLE LAST",
    "encounters": "Id PATIENT START STOP ENCOUNTERCLASS CODE DESCRIPTION REASONCODE REASONDESCRIPTION",
    "claims": "Id PATIENTID APPOINTMENTID PRIMARYPATIENTINSURANCEID SECONDARYPATIENTINSURANCEID",
    "claims_transactions": (
        "ID CLAIMID CHARGEID PATIENTID APPOINTMENTID TYPE AMOUNT METHOD PROCEDURECODE NOTES PAYMENTS"
    ),
    "payers": "Id NAME",
}
SCHEMAS = {
    "patients": pa.schema(
        [
            pa.field("patient_token", pa.large_string(), nullable=False),
            ("birthdate", pa.date32()),
            ("age_band", pa.large_string()),
            ("sex", pa.large_string()),
            ("race", pa.large_string()),
            ("ethnicity", pa.large_string()),
            ("zip", pa.large_string()),
        ]
    ),
    "encounters": pa.schema(
        [
            pa.field("encounter_id", pa.large_string(), nullable=False),
            pa.field("patient_token", pa.large_string(), nullable=False),
            ("start_time", pa.timestamp("us")),
            ("stop_time", pa.timestamp("us")),
            ("encounter_class", pa.large_string()),
            ("code", pa.large_string()),
            ("description", pa.large_string()),
            ("reason_code", pa.large_string()),
            ("reason_description", pa.large_string()),
        ]
    ),
    "claims": pa.schema(
        [
            pa.field("claim_id", pa.large_string(), nullable=False),
            pa.field("patient_token", pa.large_string(), nullable=False),
            pa.field("encounter_id", pa.large_string(), nullable=False),
            ("payer", pa.large_string()),
            ("procedure_code", pa.large_string()),
            ("procedure_description", pa.large_string()),
            ("total_cost", pa.decimal128(18, 2)),
            ("payer_coverage", pa.decimal128(18, 2)),
        ]
    ),
    "care_team": pa.schema(
        [
            pa.field("doctor_user", pa.large_string(), nullable=False),
            pa.field("patient_token", pa.large_string(), nullable=False),
            pa.field("valid_from", pa.date32(), nullable=False),
            ("valid_to", pa.date32()),
        ]
    ),
    "identity": pa.schema(
        [
            pa.field("patient_token", pa.large_string(), nullable=False),
            ("name", pa.large_string()),
        ]
    ),
}
DOCTORS = ("doctor_alice", "doctor_bob")


class ValidationError(ValueError):
    """Safe messages contain field/rule names only, never source values or secrets."""


def require(condition, rule):
    if not condition:
        raise ValidationError(rule)


def key_from_environment():
    text = os.environ.get("HMAC_KEY_HEX", "")
    version = os.environ.get("HMAC_KEY_VERSION", "")
    require(bool(re.fullmatch(r"[0-9a-fA-F]{64}", text)), "HMAC_KEY_HEX must encode 32 bytes")
    require(bool(re.fullmatch(r"v[1-9][0-9]*", version)), "Invalid HMAC_KEY_VERSION")
    return bytes.fromhex(text), version


def unique_required(frame, column, table):
    require(
        frame[column].notna().all() and frame[column].str.strip().ne("").all(),
        f"{table}.{column}: required key",
    )
    require(not frame[column].duplicated().any(), f"{table}.{column}: duplicate key")


def dates(values, field):
    def parse(value):
        if pd.isna(value):
            return None
        try:
            require(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)), f"{field}: invalid date")
            return date.fromisoformat(value)
        except ValueError:
            raise ValidationError(f"{field}: invalid date") from None

    return values.map(parse)


def age_band(birthdate, reference):
    if pd.isna(birthdate) or birthdate > reference:
        return None
    age = (
        reference.year
        - birthdate.year
        - ((reference.month, reference.day) < (birthdate.month, birthdate.day))
    )
    return "90+" if age >= 90 else f"{age // 10 * 10:02d}-{age // 10 * 10 + 9:02d}"


def timestamps(values, field):
    # Require an explicit offset; reject sub-microsecond truncation and naive local times.
    require(
        values.dropna()
        .str.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})")
        .all(),
        f"{field}: expected timezone-qualified microsecond timestamp",
    )
    try:
        return pd.to_datetime(values, format="ISO8601", utc=True).dt.tz_localize(None)
    except (ValueError, OverflowError):
        raise ValidationError(f"{field}: invalid timestamp") from None


def money(values, field):
    require(values.notna().all(), f"{field}: required amount")
    require(values.str.fullmatch(r"\d{1,16}(?:\.\d{1,2})?").all(), f"{field}: invalid cents")
    return values.map(Decimal)


def reference_check(frame, column, parent, field):
    require(frame[column].notna().all() and frame[column].isin(parent).all(), field)


@dataclass
class Prepared:
    tables: dict[str, pa.Table]
    report: dict
    oracle: dict


def assignments(tokens, seed, valid_from, valid_to):
    """Seeded synthetic membership, independent of CSV order or provider identities."""
    require(type(seed) is int and 0 <= seed < 2**32, "care_team: invalid seed")
    require(type(valid_from) is date, "care_team: required valid_from UTC date")
    require(
        valid_to is None or (type(valid_to) is date and valid_to > valid_from),
        "care_team: empty/reversed validity interval",
    )
    ids = sorted(tokens.index)
    random.Random(seed).shuffle(ids)
    members = {doctor: [] for doctor in DOCTORS}
    for index, source_id in enumerate(ids):
        # Every fifth patient is shared; the rest alternate between the two doctors.
        doctors = DOCTORS if index % 5 == 0 else (DOCTORS[index % 2],)
        for doctor in doctors:
            members[doctor].append(tokens[source_id])
    members = {doctor: sorted(values) for doctor, values in members.items()}
    rows = [
        {
            "doctor_user": doctor,
            "patient_token": token,
            "valid_from": valid_from,
            "valid_to": valid_to,
        }
        for doctor, values in members.items()
        for token in values
    ]
    oracle = {
        "format_version": 1,
        "assignment_rule": "sorted-source-ids/python-random-shuffle-v1/every-fifth-shared",
        "seed": seed,
        "valid_from": valid_from.isoformat(),
        "valid_to": valid_to.isoformat() if valid_to else None,
        "validity": "UTC dates; valid_from <= query_date < valid_to; NULL end is ongoing",
        "expected_patient_tokens": members,
    }
    return pd.DataFrame(rows), oracle


def oracle_bytes(oracle):
    return (json.dumps(oracle, sort_keys=True, indent=2) + "\n").encode()


def save_oracle(path, oracle):
    """Publish the source-derived oracle privately; never replace conflicting evidence."""
    content = oracle_bytes(oracle)
    require(not path.is_symlink(), "care_team oracle: symlink forbidden")
    if path.exists():
        require(path.read_bytes() == content, "care_team oracle differs; migration required")
        path.chmod(0o600)
        return
    # Atomic no-clobber publication; a killed writer cannot publish a partial JSON file.
    with tempfile.NamedTemporaryFile(dir=path.parent) as staged:
        staged.write(content)
        staged.flush()
        os.fsync(staged.fileno())
        os.link(staged.name, path)


def transform(frames, key, version, reference, *, seed=42, valid_from=None, valid_to=None):
    """All relationships and types are validated before any catalog operation."""
    require(
        len(key) == 32 and bool(re.fullmatch(r"v[1-9][0-9]*", version)), "Invalid HMAC settings"
    )
    p, e, c, tx, payers = (frames[name].copy() for name in SOURCE_COLUMNS)
    for name, frame in zip(SOURCE_COLUMNS, (p, e, c, tx, payers), strict=True):
        for column in SOURCE_COLUMNS[name].split():
            require(column in frame, f"{name}: missing required column")
        unique_required(frame, "ID" if name == "claims_transactions" else "Id", name)
    require(len(p) > 0 and len(e) > 0 and len(c) > 0, "Source clinical tables must be nonempty")
    tokens = (
        p.set_index("Id")
        .index.to_series()
        .map(lambda value: patient_token("synthea:" + value, key, version))
    )
    births = dates(p.BIRTHDATE, "patients.BIRTHDATE")
    patients = pd.DataFrame(
        {
            "patient_token": p.Id.map(tokens),
            "birthdate": births,
            "age_band": births.map(lambda value: age_band(value, reference)),
            "sex": p.GENDER,
            "race": p.RACE,
            "ethnicity": p.ETHNICITY,
            "zip": p.ZIP.where(p.ZIP.str.fullmatch(r"[0-9]{5}(?:-[0-9]{4})?", na=False)),
        }
    )
    reference_check(e, "PATIENT", tokens.index, "encounters: orphan patient")
    starts, stops = timestamps(e.START, "encounters.START"), timestamps(e.STOP, "encounters.STOP")
    require(
        (starts.isna() | stops.isna() | (stops >= starts)).all(), "encounters: reversed interval"
    )
    encounters = pd.DataFrame(
        {
            "encounter_id": e.Id,
            "patient_token": e.PATIENT.map(tokens),
            "start_time": starts,
            "stop_time": stops,
            "encounter_class": e.ENCOUNTERCLASS,
            "code": e.CODE,
            "description": e.DESCRIPTION,
            "reason_code": e.REASONCODE,
            "reason_description": e.REASONDESCRIPTION,
        }
    )
    reference_check(c, "PATIENTID", tokens.index, "claims: orphan patient")
    reference_check(c, "APPOINTMENTID", e.Id, "claims: orphan encounter")
    encounter_patients = e.set_index("Id").PATIENT
    require(
        c.APPOINTMENTID.map(encounter_patients).eq(c.PATIENTID).all(),
        "claims: encounter patient mismatch",
    )
    payer_names = payers.set_index("Id").NAME
    for column in ("PRIMARYPATIENTINSURANCEID", "SECONDARYPATIENTINSURANCEID"):
        require(c[column].dropna().isin(payer_names.index).all(), f"claims.{column}: orphan payer")
    reference_check(tx, "CLAIMID", c.Id, "transactions: orphan claim")
    claim_headers = c.set_index("Id")
    for column in ("PATIENTID", "APPOINTMENTID"):
        require(
            tx[column].notna().all() and tx.CLAIMID.map(claim_headers[column]).eq(tx[column]).all(),
            f"transactions: {column} mismatch",
        )
    require(
        tx.TYPE.notna().all()
        and tx.TYPE.isin({"CHARGE", "PAYMENT", "ADJUSTMENT", "TRANSFERIN", "TRANSFEROUT"}).all(),
        "transactions: unknown type",
    )
    # The pinned exporter emits an increasing CHARGEID for every transaction;
    # a CHARGE starts a line, followed by its payments/adjustments/transfers.
    require(
        tx.CHARGEID.notna().all() and tx.CHARGEID.str.fullmatch(r"[0-9]{1,18}").all(),
        "transactions: invalid sequence",
    )
    tx["sequence"] = tx.CHARGEID.astype("int64")
    require(not tx.sequence.duplicated().any(), "transactions: duplicate sequence")
    tx = tx.sort_values("sequence")
    charge = tx.TYPE.eq("CHARGE")
    tx["line"] = tx.ID.where(charge).groupby(tx.CLAIMID).ffill()
    require(tx.line.notna().all(), "transactions: payment before charge")
    lines = tx[charge].copy()
    require(set(lines.CLAIMID) == set(c.Id), "claims: missing charge lines")
    line_values = lines.set_index("ID")
    for column in ("PROCEDURECODE", "NOTES"):
        require(
            tx[column].fillna("").eq(tx.line.map(line_values[column]).fillna("")).all(),
            f"transactions: inconsistent line {column}",
        )
    payments = tx[tx.TYPE.eq("PAYMENT")].copy()
    require(
        payments.METHOD.isin({"ECHECK", "CASH", "CHECK", "CC", "COPAY"}).all(),
        "transactions: unknown payment method",
    )
    payments["amount"] = money(payments.PAYMENTS, "transactions.PAYMENTS")
    covered = payments[payments.METHOD.eq("ECHECK")].groupby("line").amount.sum()
    claims = pd.DataFrame(
        {
            "claim_id": lines.ID,  # Opaque source charge UUID, not the header UUID.
            "patient_token": lines.PATIENTID.map(tokens),
            "encounter_id": lines.APPOINTMENTID,
            "payer": lines.CLAIMID.map(claim_headers.PRIMARYPATIENTINSURANCEID).map(payer_names),
            "procedure_code": lines.PROCEDURECODE,
            "procedure_description": lines.NOTES,
            "total_cost": money(lines.AMOUNT, "transactions.AMOUNT"),
            "payer_coverage": lines.ID.map(covered).fillna(Decimal("0.00")),
        }
    )
    care_team, oracle = assignments(tokens, seed, valid_from or reference, valid_to)
    # Only approved name components enter the separately routed identity Arrow table.
    names = p[["FIRST", "MIDDLE", "LAST"]].apply(
        lambda row: (
            " ".join(str(value).strip() for value in row.dropna() if str(value).strip()) or None
        ),
        axis=1,
    )
    identity = pd.DataFrame({"patient_token": p.Id.map(tokens), "name": names})
    frames_out = {
        "patients": patients,
        "encounters": encounters,
        "claims": claims,
        "care_team": care_team,
        "identity": identity,
    }
    tables = {}
    for name, frame in frames_out.items():
        try:
            tables[name] = pa.Table.from_pandas(
                frame, schema=SCHEMAS[name], preserve_index=False
            ).replace_schema_metadata(None)
        except (pa.ArrowException, ValueError):
            raise ValidationError(f"{name}: values exceed approved Arrow types") from None
    report = {
        "source": "synthea",
        "source_version": "3.4.0",
        "age_reference_date": reference.isoformat(),
        "token_key_version": version,
        "patient_id_domain": "synthea:",
        "source_rows": {name: len(frame) for name, frame in frames.items()},
        "rows": {name: len(table) for name, table in tables.items()},
        "claims_grain": "one CHARGE transaction; claim_id = transaction ID",
        "claims_headers_with_charges": int(lines.CLAIMID.nunique()),
        "orphan_patient_tokens": 0,
        "orphan_encounter_ids": 0,
        "care_team": {
            "seed": seed,
            "valid_from": oracle["valid_from"],
            "valid_to": oracle["valid_to"],
            "doctor_patient_counts": {
                doctor: len(values) for doctor, values in oracle["expected_patient_tokens"].items()
            },
            "overlap_patients": len(
                set(oracle["expected_patient_tokens"][DOCTORS[0]])
                & set(oracle["expected_patient_tokens"][DOCTORS[1]])
            ),
        },
    }
    oracle.update({"token_key_version": version, "patient_id_domain": "synthea:"})
    return Prepared(tables, report, oracle)


def prepare(source, key, version, **assignment_options):
    manifest = json.loads((source / "manifest.json").read_text())
    parameters = manifest["parameters"]
    require(
        parameters.get("source") == "synthea" and parameters.get("version") == "3.4.0",
        "Expected pinned Synthea 3.4.0 manifest",
    )
    require(csv_inventory(source) == manifest["tables"], "Source inventory/checksum mismatch")
    frames = {}
    for name, columns in SOURCE_COLUMNS.items():
        # Preserve codes, ZIPs and literal 'NA'; SSNs/contact fields are never read.
        frame = pd.read_csv(
            source / f"{name}.csv", dtype="string", keep_default_na=False, usecols=columns.split()
        )
        frames[name] = frame.apply(lambda series: series.str.strip()).replace("", pd.NA)
    require(len(frames["patients"]) == parameters["population"], "Patient population mismatch")
    reference = pd.to_datetime(parameters["reference_date"], format="%Y%m%d").date()
    prepared = transform(frames, key, version, reference, **assignment_options)
    prepared.report["source_sha256"] = {
        name: item["sha256"] for name, item in manifest["tables"].items()
    }
    prepared.oracle["source_sha256"] = prepared.report["source_sha256"]
    prepared.report["care_team_oracle_sha256"] = hashlib.sha256(
        oracle_bytes(prepared.oracle)
    ).hexdigest()
    return prepared


def etl_catalog(warehouse="hospital"):
    require(warehouse in {"hospital", "identity"}, "Unexpected ETL warehouse")
    return load_catalog(
        warehouse,
        **{
            "type": "rest",
            "uri": "http://catalog:8181/catalog",
            "warehouse": warehouse,
            "credential": f"etl:{os.environ['ETL_OIDC_CLIENT_SECRET']}",
            "oauth2-server-uri": "http://keycloak:8080/realms/lakehouse/protocol/openid-connect/token",
            "scope": "openid",
            "header.X-Iceberg-Access-Delegation": "",
            "s3.endpoint": "http://storage:9000",
            "s3.region": "us-east-1",
            "s3.access-key-id": os.environ["ETL_STORAGE_ACCESS_KEY"],
            "s3.secret-access-key": os.environ["ETL_STORAGE_SECRET_KEY"],
            "s3.force-virtual-addressing": "false",
            "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
        },
    )


def ordered(table):
    return table.sort_by(
        [(name, "ascending") for name in table.column_names]
    ).replace_schema_metadata(None)


def load(prepared, catalog, identity_catalog):
    """Single trusted writer. Preflight all tables; resume identical partial runs."""
    existing = {}
    targets = {
        name: (identity_catalog, ("identity", "patient_identity"), "identity")
        if name == "identity"
        else (catalog, ("silver", name), "silver")
        for name in prepared.tables
    }
    for name, expected in prepared.tables.items():
        client, identifier, prefix = targets[name]
        if not client.table_exists(identifier):
            continue
        table = client.load_table(identifier)
        require(
            table.schema().as_arrow().equals(expected.schema, check_metadata=False),
            f"{name}: incompatible existing schema",
        )
        require(
            table.metadata.format_version == 2 and table.spec().is_unpartitioned(),
            f"{name}: expected unpartitioned Iceberg v2",
        )
        require(
            table.location().startswith(f"s3://warehouse/{prefix}/"),
            f"{name}: unexpected storage location",
        )
        actual = table.scan().to_arrow()
        require(
            len(actual) == 0 or ordered(actual).equals(ordered(expected)),
            f"{name}: existing data differs; migration required",
        )
        existing[name] = table, len(actual)
    catalog.create_namespace_if_not_exists("silver")
    identity_catalog.create_namespace_if_not_exists("identity")
    actions = {}
    for name, expected in prepared.tables.items():
        client, identifier, _ = targets[name]
        table, count = existing.get(name, (None, 0))
        if table is None:
            table = client.create_table(
                identifier,
                expected.schema,
                properties={"format-version": "2", "write.format.default": "parquet"},
            )
        if count == 0:
            table.append(
                expected,
                snapshot_properties={
                    "ingest.source": "synthea-3.4.0",
                    "ingest.age-reference-date": prepared.report["age_reference_date"],
                    "ingest.token-key-version": prepared.report["token_key_version"],
                },
            )
            actions[name] = "loaded"
        else:
            actions[name] = "verified existing"
        readback = client.load_table(identifier).scan().to_arrow()
        require(ordered(readback).equals(ordered(expected)), f"{name}: readback mismatch")
    return {**prepared.report, "actions": actions, "readback_verified": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/source"))
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--assignment-seed", type=int, default=42)
    parser.add_argument("--valid-from", type=date.fromisoformat)
    parser.add_argument("--valid-to", type=date.fromisoformat)
    parser.add_argument("--oracle", type=Path, default=Path("/evidence/care-team-oracle.json"))
    args = parser.parse_args()
    try:
        # SDK warnings can contain configuration; stdout is only the safe report.
        warnings.filterwarnings("ignore")
        key, version = key_from_environment()
        prepared = prepare(
            args.source,
            key,
            version,
            seed=args.assignment_seed,
            valid_from=args.valid_from,
            valid_to=args.valid_to,
        )
        if args.validate_only:
            report = prepared.report
        else:
            # Freeze expected sets before catalog reads/writes, never from the live care_team.
            save_oracle(args.oracle, prepared.oracle)
            clinical = etl_catalog()
            report = load(prepared, clinical, etl_catalog("identity"))
            # Rebuild the aggregate-only research releases from the loaded silver (#46).
            report["research_releases"] = publish(clinical)
        print(json.dumps(report, sort_keys=True))
    except Exception as error:  # noqa: BLE001 - never expose SDK/source/credential text
        message = str(error) if isinstance(error, ValidationError) else type(error).__name__
        parser.exit(1, f"Ingestion failed: {message}\n")


if __name__ == "__main__":
    main()
