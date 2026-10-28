"""Load real CSVs through ETL, read them back, and verify an identical rerun."""

import csv
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_INGEST_INTEGRATION") != "1",
    reason="Set RUN_INGEST_INTEGRATION=1 for the Docker ingestion acceptance test.",
)


def test_ingestion_roundtrip_rerun_and_key_drift(compose):
    source = Path(os.environ.get("INGEST_TEST_SOURCE", ROOT / "tests/fixtures/synthea")).resolve()
    assert (source / "manifest.json").is_file(), "Generate the Synthea sample first."
    compose.override_file.write_text(
        compose.override_file.read_text()
        + "  ingest:\n"
        + f'    user: "{os.getuid()}:{os.getgid()}"\n'
        + "    volumes:\n"
        + f"      - {source}:/source:ro\n"
    )
    compose("build", "ingest")
    # Same service/dependency path as scripts/ingest.sh; fresh project/credentials.
    output = compose("run", "--rm", "ingest")
    report = json.loads(next(line for line in output.splitlines() if line.startswith("{")))
    with (source / "claims_transactions.csv").open() as handle:
        charge_count = sum(row["TYPE"] == "CHARGE" for row in csv.DictReader(handle))
    assert report["rows"]["claims"] == charge_count
    assert report["readback_verified"]
    assert report["rows"]["patients"] == report["source_rows"]["patients"]
    assert report["rows"]["encounters"] == report["source_rows"]["encounters"]
    assert report["claims_headers_with_charges"] == report["source_rows"]["claims"]
    assert report["orphan_patient_tokens"] == report["orphan_encounter_ids"] == 0
    assert set(report["actions"].values()) == {"loaded"}
    rerun = compose("run", "--rm", "--no-deps", "ingest")
    repeated = json.loads(next(line for line in rerun.splitlines() if line.startswith("{")))
    assert repeated["rows"] == report["rows"]
    assert set(repeated["actions"].values()) == {"verified existing"}
    # A changed HMAC version cannot append a second token domain to an existing cohort.
    failed = compose(
        "run", "--rm", "--no-deps", "-e", "HMAC_KEY_VERSION=v2", "ingest", expect_success=False
    )
    assert "existing data differs; migration required" in failed
    assert compose.values["HMAC_KEY_HEX"] not in output + rerun + failed
