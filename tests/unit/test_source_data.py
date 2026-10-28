"""Exercise source integrity, CSV accounting and publication without network or Java."""

import argparse
import csv
import gzip
import json
import subprocess
from pathlib import Path

import pytest

from scripts import source_data as data


def write_csv(path, header, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(records)


def test_csv_counts_records_instead_of_physical_lines(tmp_path):
    write_csv(tmp_path / "patients.csv", ["id", "note"], [["1", "first\nsecond"], ["2", ""]])
    table = data.csv_inventory(tmp_path)["patients.csv"]
    assert table["rows"] == 2
    assert table["columns"] == ["id", "note"]
    assert table["sha256"] == data.sha256(tmp_path / "patients.csv")


@pytest.mark.parametrize("content", ["", "id,id\n1,2\n", "id,\n1,2\n", "id\n1,2\n"])
def test_malformed_csv_is_rejected(tmp_path, content):
    (tmp_path / "bad.csv").write_text(content)
    with pytest.raises(ValueError):
        data.csv_inventory(tmp_path)


def test_download_and_cached_artifact_are_both_verified(tmp_path):
    source = tmp_path / "official"
    source.write_bytes(b"release")
    target = tmp_path / "cache/artifact"
    data.download(source.as_uri(), target, data.sha256(source))
    assert target.read_bytes() == b"release"
    target.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="Checksum mismatch"):
        data.download(source.as_uri(), target, data.sha256(source))


def test_corrupt_download_does_not_publish_or_leave_partial_files(tmp_path):
    source = tmp_path / "official"
    source.write_bytes(b"corrupt")
    target = tmp_path / "cache/artifact"
    with pytest.raises(ValueError, match="Checksum mismatch"):
        data.download(source.as_uri(), target, "0" * 64)
    assert not target.exists()
    assert list(target.parent.iterdir()) == []


def sample(tmp_path, name, records):
    directory = tmp_path / name
    write_csv(directory / "patients.csv", ["id"], [[r] for r in records])
    return directory


def test_repeat_retains_original_sample_and_rejects_changed_counts(tmp_path, capsys):
    output = tmp_path / "published"
    data.publish(sample(tmp_path, "first", [1, 2]), output, {"parameters": {"seed": 42}})
    original = (output / "manifest.json").read_bytes()
    # Byte identity is not required by #3; a second run must have matching row counts.
    data.publish(sample(tmp_path, "repeat", [3, 4]), output, {"parameters": {"seed": 42}})
    assert "Repeat verified" in capsys.readouterr().out
    assert (output / "manifest.json").read_bytes() == original
    assert (output / "patients.csv").read_text().splitlines() == ["id", "1", "2"]
    with pytest.raises(ValueError, match="different row counts"):
        data.publish(sample(tmp_path, "changed", [1]), output, {"parameters": {"seed": 42}})
    assert (output / "manifest.json").read_bytes() == original


def test_existing_dataset_parameters_and_integrity_are_checked(tmp_path):
    output = tmp_path / "published"
    data.publish(sample(tmp_path, "first", [1]), output, {"parameters": {"seed": 42}})
    with pytest.raises(ValueError, match="Different parameters"):
        data.publish(sample(tmp_path, "other", [1]), output, {"parameters": {"seed": 43}})
    (output / "patients.csv").write_text("id\nmodified\n")
    with pytest.raises(ValueError, match="changed since publication"):
        data.publish(sample(tmp_path, "repeat", [1]), output, {"parameters": {"seed": 42}})


@pytest.fixture
def demo_source(tmp_path, monkeypatch):
    """A local, complete 100-subject miniature of the retained upstream tables."""
    source = tmp_path / "upstream"
    subjects = [[str(i)] for i in range(100)]
    write_csv(source / "demo_subject_id.csv", ["subject_id"], subjects)
    write_csv(source / "hosp/patients.csv", ["subject_id"], subjects)
    for name in (
        "hosp/admissions.csv",
        "hosp/diagnoses_icd.csv",
        "hosp/procedures_icd.csv",
        "icu/icustays.csv",
    ):
        write_csv(source / name, ["subject_id", "hadm_id"], [["0", "10"]])
    (source / "LICENSE.txt").write_text("Fixture license")
    (source / "README.txt").write_text("Fixture README")
    checksums = {}
    for name in data.MIMIC_FILES:
        path = source / name
        if name.endswith(".gz"):
            path.write_bytes(gzip.compress(path.with_suffix("").read_bytes()))
        checksums[name] = data.sha256(path)
    monkeypatch.setattr(data, "MIMIC_URL", source.as_uri() + "/")
    monkeypatch.setattr(data, "MIMIC_FILES", checksums)
    return source


def test_demo_acquisition_decompresses_verifies_and_preserves_license(tmp_path, demo_source):
    output = tmp_path / "published"
    data.download_mimic_demo(argparse.Namespace(output=output))
    assert not list(output.rglob("*.gz"))
    assert (output / "LICENSE.txt").read_text() == "Fixture license"
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["data_kind"] == "deidentified_clinical"
    assert manifest["tables"]["hosp/patients.csv"]["rows"] == 100
    assert manifest["parameters"]["source"] == "mimic-iv-demo"
    data.download_mimic_demo(argparse.Namespace(output=output))


def test_failed_demo_checksum_leaves_no_published_dataset(tmp_path, demo_source):
    (demo_source / "hosp/patients.csv.gz").write_bytes(b"corruption")
    output = tmp_path / "published"
    with pytest.raises(ValueError, match="Checksum mismatch"):
        data.download_mimic_demo(argparse.Namespace(output=output))
    assert not output.exists()
    assert not list(tmp_path.glob(".mimic-demo-*"))


def test_demo_rejects_mismatched_admission_patient_pair(tmp_path, demo_source):
    write_csv(demo_source / "icu/icustays.csv", ["subject_id", "hadm_id"], [["1", "10"]])
    with pytest.raises(ValueError, match="Invalid demo patient/admission reference"):
        data.validate_demo(demo_source)


@pytest.mark.parametrize("patients", [1000, 10000, 100000])
def test_cli_accepts_requested_scales(monkeypatch, patients):
    calls = []
    monkeypatch.setattr(data, "generate_synthea", calls.append)
    monkeypatch.setattr("sys.argv", ["source_data.py", "synthea", "--patients", str(patients)])
    data.main()
    assert calls[0].patients == patients
    assert calls[0].seed == 42


@pytest.mark.parametrize("value", ["0", "-1", "2147483648"])
def test_invalid_population_is_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError):
        data.positive_population(value)


@pytest.mark.parametrize("value", ["20250229", "20251301", "2025011", "yesterday"])
def test_invalid_reference_date_is_rejected(value):
    with pytest.raises(argparse.ArgumentTypeError):
        data.reference_date(value)


def test_generation_checks_exact_population_before_publication(tmp_path, monkeypatch):
    jar = tmp_path / "synthea.jar"
    jar.write_bytes(b"fixture")
    monkeypatch.setattr(data, "SYNTHEA_SHA256", data.sha256(jar))
    monkeypatch.setattr(data, "check_java", lambda java: "fixture Java 17")

    def fake_java(command, **kwargs):
        run = Path(kwargs["cwd"])
        for name in data.SYNTHEA_FILES:
            write_csv(run / "export/csv" / name, ["Id"], [["only-one-patient"]])

    monkeypatch.setattr(data.subprocess, "run", fake_java)
    output = tmp_path / "published"
    args = argparse.Namespace(
        java="java",
        jar=jar,
        output=output,
        patients=1000,
        seed=42,
        reference_date="20250101",
        heap="2g",
    )
    with pytest.raises(ValueError, match="requested patient count"):
        data.generate_synthea(args)
    assert not output.exists()
    assert not list(tmp_path.glob(".synthea-*"))


def test_java_8_fails_with_actionable_message(monkeypatch):
    monkeypatch.setattr(
        data.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a, 0, stdout="", stderr='openjdk version "1.8.0_504"'
        ),
    )
    with pytest.raises(ValueError, match="Java 17.*--java"):
        data.check_java("java")
