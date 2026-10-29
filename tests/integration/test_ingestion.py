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


@pytest.mark.parametrize("valid_to", [None, "2025-01-11"], ids=["ongoing", "finite"])
def test_ingestion_roundtrip_rerun_and_key_drift(compose, tmp_path, valid_to):
    source = Path(os.environ.get("INGEST_TEST_SOURCE", ROOT / "tests/fixtures/synthea")).resolve()
    assert (source / "manifest.json").is_file(), "Generate the Synthea sample first."
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    compose.override_file.write_text(
        compose.override_file.read_text()
        + "  ingest:\n"
        + f'    user: "{os.getuid()}:{os.getgid()}"\n'
        + "    volumes:\n"
        + f"      - {source}:/source:ro\n"
        + f"      - {evidence}:/evidence:rw\n"
        + f"      - {ROOT / 'tests/integration/ingestion_check.py'}:/app/ingestion_check.py:ro\n"
        + "  catalog-check:\n    volumes:\n"
        + f"      - {ROOT / 'tests/integration/identity_check.py'}:/app/identity_check.py:ro\n"
    )
    compose("build", "ingest")
    # Same service/dependency path as scripts/ingest.sh; fresh project/credentials.
    pipeline_args = ("-m", "ingest.pipeline", *(("--valid-to", valid_to) if valid_to else ()))
    output = compose("run", "--rm", "ingest", *pipeline_args)
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
    assert report["rows"]["identity"] == report["rows"]["patients"]
    assert report["care_team"]["overlap_patients"] > 0
    assert report["care_team"]["valid_to"] == valid_to
    assert set(report["care_team"]["doctor_patient_counts"]) == {"doctor_alice", "doctor_bob"}
    if report["rows"]["patients"] == 1000:
        assert set(report["care_team"]["doctor_patient_counts"].values()) == {600}
        assert report["care_team"]["overlap_patients"] == 200
        assert report["rows"]["care_team"] == 1200
    oracle_path = evidence / "care-team-oracle.json"
    oracle = oracle_path.read_bytes()
    assert oracle_path.stat().st_mode & 0o777 == 0o600

    def check():
        output = compose("run", "--rm", "--no-deps", "ingest", "/app/ingestion_check.py")
        return json.loads(next(line for line in output.splitlines() if line.startswith("{")))

    before = check()
    assert before["saved_sets_verified"]
    assert before["oracle_sha256"] == report["care_team_oracle_sha256"]
    # Bootstrap is rerunnable after identity registration and real data writes.
    compose("run", "--rm", "--no-deps", "catalog-init")
    assert "PASS:" in compose("run", "--rm", "--no-deps", "catalog-check", "/app/identity_check.py")
    rerun = compose("run", "--rm", "--no-deps", "ingest", *pipeline_args)
    repeated = json.loads(next(line for line in rerun.splitlines() if line.startswith("{")))
    assert repeated["rows"] == report["rows"]
    assert set(repeated["actions"].values()) == {"verified existing"}
    assert check() == before
    assert oracle_path.read_bytes() == oracle
    # A changed HMAC version cannot append a second token domain to an existing cohort.
    failed = compose(
        "run",
        "--rm",
        "--no-deps",
        "-e",
        "HMAC_KEY_VERSION=v2",
        "ingest",
        *pipeline_args,
        expect_success=False,
    )
    assert "care_team oracle differs; migration required" in failed
    changed_seed = compose(
        "run",
        "--rm",
        "--no-deps",
        "ingest",
        *pipeline_args,
        "--assignment-seed",
        "43",
        expect_success=False,
    )
    assert "care_team oracle differs; migration required" in changed_seed
    assert check() == before
    assert oracle_path.read_bytes() == oracle
    assert compose.values["HMAC_KEY_HEX"] not in output + rerun + failed
