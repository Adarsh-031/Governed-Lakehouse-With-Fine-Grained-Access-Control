"""Acquire pinned bronze CSVs using only Python's standard library (Python 3.11+)."""

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
CONFIG = Path(__file__).with_name("synthea.properties")
SYNTHEA_VERSION = "3.4.0"
SYNTHEA_URL = (
    "https://github.com/synthetichealth/synthea/releases/download/"
    f"v{SYNTHEA_VERSION}/synthea-with-dependencies.jar"
)
SYNTHEA_SHA256 = "38678aab1e667d26671163824aad60ee5e30fa366f3d59d5ea5765ebb5702432"
SYNTHEA_FILES = (
    "patients.csv",
    "encounters.csv",
    "claims.csv",
    "claims_transactions.csv",
    "providers.csv",
    "organizations.csv",
    "payers.csv",
)
MIMIC_URL = "https://physionet.org/files/mimic-iv-demo/2.2/"
# Published by PhysioNet in the versioned demo's SHA256SUMS.txt.
MIMIC_FILES = {
    "LICENSE.txt": "bcdfcd4f31c790e30fb645dc36414907172405897adff329ad379eed85d12017",
    "README.txt": "4eaae533164245e59e5eb70b52695c061900cdc067cebe508105706b4d10e393",
    "demo_subject_id.csv": "6e9ba177eefc70617ccc1782b9af716b23fbcf36c4c5143e275149d78ee83624",
    "hosp/patients.csv.gz": "59dcc6cd8450ddf03ad32cc7d76e41caa4c9633541715987f590c74835cf6fae",
    "hosp/admissions.csv.gz": "910b9f160ffdf1e08ea673585393f347c773ccc87d66875c627584a903ae8493",
    "hosp/diagnoses_icd.csv.gz": "958565670c0b3903c0c12825366dc2bae8561d2d55f16feac0c949dd630ed3e4",
    "hosp/procedures_icd.csv.gz": "68f21dcba9ae0c4b7faa3ef38aa950a8ebeb6d167bc74416314dccdcf28674e0",
    "icu/icustays.csv.gz": "e05e81aa52a3022e522b6832a898101a69b84e64c17bb344d819e458d5bc21b3",
}


def sha256(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def download(url: str, path: Path, expected: str) -> None:
    """Never execute/use an unverified download or a mismatching cached artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if sha256(path) != expected:
            raise ValueError(f"Checksum mismatch: {path}")
        return
    with tempfile.TemporaryDirectory(dir=path.parent, prefix=".download-") as temp:
        partial = Path(temp) / "artifact"
        with urlopen(url, timeout=120) as response, partial.open("wb") as handle:
            shutil.copyfileobj(response, handle)
        if sha256(partial) != expected:
            raise ValueError(f"Checksum mismatch downloading {url}")
        partial.replace(path)


def csv_inventory(directory: Path) -> dict:
    """Count CSV records, including quoted newlines, without loading tables in RAM."""
    tables = {}
    for path in sorted(directory.rglob("*.csv")):
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle, strict=True)
            header = next(reader, [])
            if not header or len(header) != len(set(header)) or any(not h for h in header):
                raise ValueError(f"Missing or duplicate CSV header: {path}")
            count = 0
            for row in reader:
                if len(row) != len(header):
                    raise ValueError(f"Invalid CSV record width: {path}, record {count + 1}")
                count += 1
        tables[path.relative_to(directory).as_posix()] = {
            "rows": count,
            "columns": header,
            "sha256": sha256(path),
        }
    return tables


def publish(staged: Path, output: Path, manifest: dict) -> None:
    """Publish complete data once; reruns verify counts and retain the original sample."""
    manifest["tables"] = csv_inventory(staged)
    if output.exists():
        previous = json.loads((output / "manifest.json").read_text())
        if previous["parameters"] != manifest["parameters"]:
            raise ValueError(f"Different parameters already exist at {output}; use --output")
        if csv_inventory(output) != previous["tables"]:
            raise ValueError(f"Existing dataset changed since publication: {output}")
        counts = lambda m: {name: item["rows"] for name, item in m["tables"].items()}
        if counts(previous) != counts(manifest):
            raise ValueError(f"Repeated run produced different row counts: {output}")
        print(f"Repeat verified: all CSV row counts match; retained {output}")
    else:
        (staged / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        staged.rename(output)
        print(f"Published {output}")
    for name, item in manifest["tables"].items():
        print(f"  {name}: {item['rows']} rows")


def check_java(java: str) -> str:
    result = subprocess.run([java, "-version"], check=True, capture_output=True, text=True)
    version = result.stderr + result.stdout
    match = re.search(r'version "(\d+)(?:\.(\d+))?', version)
    major = int(match[1]) if match else 0
    if major == 1:
        major = int(match[2])
    if major < 17:
        raise ValueError("Java 17+ required; select a runtime with --java /path/to/bin/java")
    return version.strip()


def generate_synthea(args: argparse.Namespace) -> None:
    java_version = check_java(args.java)
    jar = args.jar.resolve()
    download(SYNTHEA_URL, jar, SYNTHEA_SHA256)
    output = args.output or ROOT / "data/raw/synthea" / f"{args.patients}-seed{args.seed}"
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    parameters = {
        "source": "synthea",
        "version": SYNTHEA_VERSION,
        "jar_sha256": SYNTHEA_SHA256,
        "config_sha256": sha256(CONFIG),
        "population": args.patients,
        "seed": args.seed,
        "clinician_seed": args.seed,
        "reference_date": args.reference_date,
        "end_date": args.reference_date,
        "state": "Massachusetts",
        "overflow": False,
        "timezone": "UTC",
    }
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".synthea-") as temp:
        run = Path(temp)
        command = [
            args.java,
            f"-Xmx{args.heap}",
            "-Duser.timezone=UTC",
            "-Duser.language=en",
            "-Duser.country=US",
            "-jar",
            str(jar),
            "-c",
            str(CONFIG),
            "-s",
            str(args.seed),
            "-cs",
            str(args.seed),
            "-p",
            str(args.patients),
            "-r",
            args.reference_date,
            "-e",
            args.reference_date,
            "-o",
            "false",
            f"--exporter.baseDirectory={run / 'export'}",
            "Massachusetts",
        ]
        # An isolated working directory prevents local synthea.properties overrides.
        subprocess.run(command, check=True, cwd=run)
        staged = run / "dataset"
        staged.mkdir()
        for name in SYNTHEA_FILES:
            shutil.move(run / "export/csv" / name, staged / name)
        inventory = csv_inventory(staged)
        if inventory["patients.csv"]["rows"] != args.patients:
            raise ValueError("Synthea did not export the requested patient count")
        publish(
            staged,
            output,
            {
                "parameters": parameters,
                "source_url": SYNTHEA_URL,
                "license": "Apache-2.0",
                "data_kind": "synthetic",
                "java_version": java_version,
                "token_key_version": None,
            },
        )


def validate_demo(directory: Path) -> None:
    """Verify the fixed demo cohort and patient/admission references in retained tables."""

    def rows(name):
        with (directory / name).open(newline="", encoding="utf-8") as handle:
            yield from csv.DictReader(handle)

    subjects = {r["subject_id"] for r in rows("demo_subject_id.csv")}
    patients = [r["subject_id"] for r in rows("hosp/patients.csv")]
    if len(subjects) != 100 or len(patients) != 100 or set(patients) != subjects:
        raise ValueError("MIMIC demo must contain exactly the published 100-patient cohort")
    admissions = {}
    for row in rows("hosp/admissions.csv"):
        if row["subject_id"] not in subjects or row["hadm_id"] in admissions:
            raise ValueError("Invalid demo admission key or patient reference")
        admissions[row["hadm_id"]] = row["subject_id"]
    for name in ("hosp/diagnoses_icd.csv", "hosp/procedures_icd.csv", "icu/icustays.csv"):
        for row in rows(name):
            if (
                row["subject_id"] not in subjects
                or admissions.get(row["hadm_id"]) != row["subject_id"]
            ):
                raise ValueError(f"Invalid demo patient/admission reference in {name}")


def download_mimic_demo(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".mimic-demo-") as temp:
        staged = Path(temp) / "dataset"
        staged.mkdir()
        for name, checksum in MIMIC_FILES.items():
            path = staged / name
            download(MIMIC_URL + name, path, checksum)
            if name.endswith(".gz"):
                with gzip.open(path, "rb") as src, path.with_suffix("").open("wb") as dst:
                    shutil.copyfileobj(src, dst)
                path.unlink()
        validate_demo(staged)
        publish(
            staged,
            output,
            {
                "parameters": {"source": "mimic-iv-demo", "version": "2.2", "files": MIMIC_FILES},
                "source_url": "https://physionet.org/content/mimic-iv-demo/2.2/",
                "license": "ODbL-1.0",
                "data_kind": "deidentified_clinical",
                "token_key_version": None,
            },
        )


def positive_population(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 2_147_483_647:
        raise argparse.ArgumentTypeError("patients must be in 1..2147483647")
    return number


def seed(value: str) -> int:
    number = int(value)
    if not -(2**63) <= number < 2**63:
        raise argparse.ArgumentTypeError("seed must fit a signed 64-bit integer")
    return number


def reference_date(value: str) -> str:
    try:
        if len(value) != 8 or not value.isascii() or not value.isdigit():
            raise ValueError
        datetime.strptime(value, "%Y%m%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("reference date must be a valid YYYYMMDD") from exc
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="source", required=True)
    synthea = subparsers.add_parser("synthea", help="Generate the pinned Synthea CSV sample")
    synthea.add_argument("--patients", type=positive_population, default=1000)
    synthea.add_argument("--seed", type=seed, default=42)
    synthea.add_argument("--reference-date", type=reference_date, default="20250101")
    synthea.add_argument("--output", type=Path)
    synthea.add_argument("--jar", type=Path, default=ROOT / "data/tools/synthea-3.4.0.jar")
    synthea.add_argument("--java", default=os.environ.get("JAVA", "java"))
    synthea.add_argument("--heap", default="2g", help="Java maximum heap, e.g. 2g or 4096m")
    synthea.set_defaults(func=generate_synthea)
    mimic = subparsers.add_parser("mimic-demo", help="Download only the open MIMIC-IV 2.2 demo")
    mimic.add_argument("--output", type=Path, default=ROOT / "data/raw/mimic-iv-demo/2.2")
    mimic.set_defaults(func=download_mimic_demo)
    args = parser.parse_args()
    try:
        args.func(args)
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"error: {exc}\n")


if __name__ == "__main__":
    main()
