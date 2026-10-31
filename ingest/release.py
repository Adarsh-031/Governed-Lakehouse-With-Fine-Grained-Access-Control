"""Aggregate-only research releases with small-cell suppression (issue #46).

Researchers no longer read row-level demographics or encounter reason codes from
silver. Instead the trusted ETL publishes three fixed aggregate tables in
``hospital.gold``. Every published cell counts distinct patients, and any cell with
fewer than ``MIN_CELL`` patients is dropped before it is written. The tables carry
no patient token or record-level key, so a researcher cannot add a predicate that
excludes one person (the exact-count differencing attack measured in #23/#24).

Run after every silver write: ``python -m ingest.release``.
"""

import json
import re
import sys
import warnings

import pyarrow as pa

MIN_CELL = 11  # Common health-statistics small-cell threshold (counts 1-10 suppressed).
NAMESPACE = "gold"
ZIP = re.compile(r"^[0-9]{5}(-[0-9]{4})?$")
SCHEMAS = {
    "researcher_demographics": pa.schema(
        [
            ("age_band", pa.large_string()),
            ("sex", pa.large_string()),
            ("race", pa.large_string()),
            ("patient_count", pa.int64()),
        ]
    ),
    "researcher_geography": pa.schema(
        [("zip_prefix", pa.large_string()), ("patient_count", pa.int64())]
    ),
    "researcher_conditions": pa.schema(
        [("reason_code", pa.large_string()), ("patient_count", pa.int64())]
    ),
}


def zip_prefix(value):
    """Same generalization the researcher ZIP mask used: 3 digits + ** or NULL."""
    if value is None or not ZIP.fullmatch(value):
        return None
    return value[:3] + "**"


def _cells(pairs, columns, min_cell):
    """Count distinct patients per key; drop cells below the threshold."""
    patients = {}
    for token, key in pairs:
        patients.setdefault(key, set()).add(token)
    rows = [
        {**dict(zip(columns, key, strict=True)), "patient_count": len(tokens)}
        for key, tokens in patients.items()
        if len(tokens) >= min_cell
    ]
    # Deterministic order; None sorts first.
    return sorted(rows, key=lambda r: tuple((r[c] is not None, r[c] or "") for c in columns))


def build(patients, encounters, min_cell=MIN_CELL):
    """Pure transform from silver rows (lists of dicts) to suppressed gold tables."""
    if min_cell < 2:
        raise ValueError("min_cell must be at least 2")
    demographics = _cells(
        ((p["patient_token"], (p["age_band"], p["sex"], p["race"])) for p in patients),
        ("age_band", "sex", "race"),
        min_cell,
    )
    geography = _cells(
        ((p["patient_token"], (zip_prefix(p["zip"]),)) for p in patients),
        ("zip_prefix",),
        min_cell,
    )
    # Encounters without a reason code are not a condition; leave them out.
    conditions = _cells(
        ((e["patient_token"], (e["reason_code"],)) for e in encounters if e["reason_code"]),
        ("reason_code",),
        min_cell,
    )
    tables = {
        "researcher_demographics": demographics,
        "researcher_geography": geography,
        "researcher_conditions": conditions,
    }
    return {name: pa.Table.from_pylist(rows, schema=SCHEMAS[name]) for name, rows in tables.items()}


def publish(catalog, min_cell=MIN_CELL):
    """Read current silver, rebuild every release, and replace each gold table."""
    patients = (
        catalog.load_table(("silver", "patients"))
        .scan(selected_fields=("patient_token", "age_band", "sex", "race", "zip"))
        .to_arrow()
        .to_pylist()
    )
    encounters = (
        catalog.load_table(("silver", "encounters"))
        .scan(selected_fields=("patient_token", "reason_code"))
        .to_arrow()
        .to_pylist()
    )
    tables = build(patients, encounters, min_cell)
    catalog.create_namespace_if_not_exists(NAMESPACE)
    report = {}
    for name, table in tables.items():
        identifier = (NAMESPACE, name)
        if catalog.table_exists(identifier):
            target = catalog.load_table(identifier)
            if not target.schema().as_arrow().equals(table.schema, check_metadata=False):
                raise ValueError(f"{name}: incompatible existing schema")
        else:
            target = catalog.create_table(
                identifier,
                table.schema,
                properties={"format-version": "2", "write.format.default": "parquet"},
            )
        target.overwrite(table, snapshot_properties={"release.min-cell": str(min_cell)})
        readback = catalog.load_table(identifier).scan().to_arrow()
        if readback.num_rows != table.num_rows:
            raise ValueError(f"{name}: readback mismatch")
        report[name] = {"published_cells": table.num_rows}
    return {"min_cell": min_cell, "releases": report}


def main():
    from ingest.pipeline import etl_catalog

    warnings.filterwarnings("ignore")
    try:
        print(json.dumps(publish(etl_catalog()), sort_keys=True))
    except Exception as error:  # noqa: BLE001 - never expose SDK/credential text
        message = str(error) if isinstance(error, ValueError) else type(error).__name__
        sys.exit(f"Release failed: {message}")


if __name__ == "__main__":
    main()
