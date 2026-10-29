"""Independent care-team expectations, identity routing, and private oracle evidence."""

import json
from datetime import timedelta
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from ingest import pipeline as etl
from ingest.tokenization import patient_token

from .test_ingestion import KEY, REFERENCE, fake_catalog


def prepared(frames, **options):
    return etl.transform(frames, KEY, "v1", REFERENCE, **options)


def test_seeded_rule_has_frozen_expected_sets_overlap_and_exclusions():
    # Literal expected source-ID sets protect the generator against accidental rule drift.
    tokens = pd.Series({f"p{i}": patient_token(f"synthea:p{i}", KEY) for i in range(10)})
    table, oracle = etl.assignments(tokens, 42, REFERENCE, None)
    expected_ids = {
        "doctor_alice": {"p0", "p2", "p5", "p6", "p7", "p9"},
        "doctor_bob": {"p1", "p3", "p4", "p6", "p7", "p8"},
    }
    assert oracle["expected_patient_tokens"] == {
        doctor: sorted(tokens[list(ids)]) for doctor, ids in expected_ids.items()
    }
    alice, bob = (set(values) for values in oracle["expected_patient_tokens"].values())
    assert len(alice & bob) == 2 and alice - bob and bob - alice
    assert alice | bob == set(tokens)
    assert not table.duplicated(["doctor_user", "patient_token", "valid_from"]).any()
    assert table.valid_from.eq(REFERENCE).all() and table.valid_to.isna().all()
    # Reordering source CSVs cannot change either the saved oracle or assignment rows.
    reordered, repeated = etl.assignments(
        tokens.sample(frac=1, random_state=1), 42, REFERENCE, None
    )
    pd.testing.assert_frame_equal(reordered, table)
    assert repeated == oracle
    assert etl.assignments(tokens, 43, REFERENCE, None)[1] != oracle


@pytest.mark.parametrize("seed", [-1, 2**32, True, "42"])
def test_invalid_seed_rejected(seed):
    with pytest.raises(etl.ValidationError, match="invalid seed"):
        etl.assignments(pd.Series({"p1": "token"}), seed, REFERENCE, None)


@pytest.mark.parametrize(
    "start,end", [(None, None), (REFERENCE, REFERENCE), (REFERENCE, REFERENCE - timedelta(days=1))]
)
def test_missing_start_and_empty_or_reversed_intervals_rejected(start, end):
    with pytest.raises(etl.ValidationError):
        etl.assignments(pd.Series({"p1": "token"}), 42, start, end)


def test_identity_only_projects_name_components_and_shared_tokens(frames):
    frames["patients"]["SSN"] = "do-not-project-ssn"
    frames["patients"]["ADDRESS"] = "do-not-project-address"
    frames["patients"].loc[0, ["FIRST", "MIDDLE", "LAST"]] = [" Test ", " Middle ", "Patient"]
    data = prepared(frames)
    rows = data.tables["identity"].to_pylist()
    assert rows == [
        {"patient_token": patient_token("synthea:p1", KEY), "name": "Test Middle Patient"},
        {"patient_token": patient_token("synthea:p2", KEY), "name": None},
    ]
    for name, table in data.tables.items():
        if name != "identity":
            assert "Test Middle Patient" not in repr(table.to_pylist())
            assert "name" not in table.column_names
        assert "do-not-project" not in repr(table.to_pylist())
        assert set(table["patient_token"].to_pylist()) <= set(
            data.tables["patients"]["patient_token"].to_pylist()
        )
    assert "Test Middle Patient" not in json.dumps(data.report) + json.dumps(data.oracle)


def test_saved_oracle_is_private_stable_and_rejects_drift(frames, tmp_path):
    path = tmp_path / "oracle.json"
    oracle = prepared(frames).oracle
    etl.save_oracle(path, oracle)
    assert json.loads(path.read_text()) == oracle
    assert path.stat().st_mode & 0o777 == 0o600
    before = path.stat().st_mtime_ns
    etl.save_oracle(path, oracle)
    assert path.stat().st_mtime_ns == before
    with pytest.raises(etl.ValidationError, match="migration required"):
        etl.save_oracle(path, prepared(frames, seed=43).oracle)
    assert json.loads(path.read_text()) == oracle
    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(etl.ValidationError, match="symlink forbidden"):
        etl.save_oracle(link, oracle)


def test_load_preflights_identity_before_any_clinical_mutation(frames):
    data = prepared(frames)
    clinical, tables = fake_catalog(data)
    identity, identity_tables = fake_catalog(data, prefix="identity")
    etl.load(data, clinical, identity)
    assert "identity" not in tables and "patient_identity" in identity_tables
    identity.create_table.assert_called_once()
    assert identity.create_table.call_args.args[0] == ("identity", "patient_identity")
    # An interrupted run left clinical missing/empty; identity drift must still stop writes.
    del tables["care_team"]
    tables["patients"].data = data.tables["patients"].slice(0, 0)
    identity_tables["patient_identity"].data = pa.Table.from_pylist(
        [{"patient_token": "v1:wrong", "name": "Changed"}], schema=etl.SCHEMAS["identity"]
    )
    with pytest.raises(etl.ValidationError, match="identity: existing data differs"):
        etl.load(data, clinical, identity)
    assert "care_team" not in tables
    assert tables["patients"].append.call_count == 1


def test_upgrade_issue16_partial_resume_and_order_independent_rerun(frames):
    data = prepared(frames)
    clinical, tables = fake_catalog(data)
    identity, identity_tables = fake_catalog(data, prefix="identity")
    etl.load(data, clinical, identity)
    del tables["care_team"]
    del identity_tables["patient_identity"]
    report = etl.load(data, clinical, identity)
    assert report["actions"]["care_team"] == report["actions"]["identity"] == "loaded"
    assert report["actions"]["patients"] == "verified existing"
    tables["care_team"].data = tables["care_team"].data.take(
        pa.array(list(reversed(range(len(tables["care_team"].data)))))
    )
    assert set(etl.load(data, clinical, identity)["actions"].values()) == {"verified existing"}
    with pytest.raises(etl.ValidationError, match="existing data differs"):
        etl.load(prepared(frames, valid_from=REFERENCE + timedelta(days=1)), clinical, identity)


@pytest.mark.parametrize("target", ["care_team", "identity"])
def test_wrong_storage_prefix_rejected(frames, target):
    data = prepared(frames)
    clinical, tables = fake_catalog(data)
    identity, identity_tables = fake_catalog(data, prefix="identity")
    etl.load(data, clinical, identity)
    table = tables["care_team"] if target == "care_team" else identity_tables["patient_identity"]
    table.location.return_value = "s3://warehouse/bronze/wrong"
    with pytest.raises(etl.ValidationError, match="unexpected storage location"):
        etl.load(data, clinical, identity)


def test_validate_only_does_not_save_oracle_or_connect(monkeypatch, capsys):
    monkeypatch.setenv("HMAC_KEY_HEX", KEY.hex())
    monkeypatch.setenv("HMAC_KEY_VERSION", "v1")
    monkeypatch.setattr(
        "sys.argv", ["ingest", "--source", "tests/fixtures/synthea", "--validate-only"]
    )
    monkeypatch.setattr(etl, "save_oracle", lambda *args: pytest.fail("unexpected evidence write"))
    monkeypatch.setattr(etl, "etl_catalog", lambda *args: pytest.fail("unexpected connection"))
    etl.main()
    report = json.loads(capsys.readouterr().out)
    assert report["rows"]["identity"] == 2 and report["rows"]["care_team"] == 3
    assert len(report["care_team_oracle_sha256"]) == 64


def test_finite_interval_oracle_records_half_open_dates(frames):
    end = REFERENCE + timedelta(days=10)
    data = prepared(frames, valid_to=end)
    assert data.oracle["valid_from"] == REFERENCE.isoformat()
    assert data.oracle["valid_to"] == end.isoformat()
    assert all(row["valid_to"] == end for row in data.tables["care_team"].to_pylist())


def test_compose_evidence_is_only_writable_mount_and_has_no_new_privileged_secrets():
    import yaml

    config = yaml.safe_load(Path("compose.yaml").read_text())["services"]
    assert config["ingest"]["volumes"][1].endswith(":/evidence:rw")
    assert config["ingest"]["read_only"]
    assert "CATALOG_BOOTSTRAP_OIDC_CLIENT_SECRET" not in config["ingest"]["environment"]
    assert not any("identity.properties" in mount for mount in config["trino"]["volumes"])
