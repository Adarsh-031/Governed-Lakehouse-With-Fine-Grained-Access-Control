"""Compile/check the reviewed MVP contract and freeze policy evidence for a run."""

import argparse
import csv
import hashlib
import json
import re
from collections import Counter
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
VERSION = "clinical-v1"
MEMBERS = {
    "doctor": ["doctor_alice", "doctor_bob"],
    "researcher": ["researcher"],
    "billing": ["billing"],
    "admin": ["admin"],
}
READER = "policy_care_team_reader"
METADATA_COLUMNS = ("$path", "$partition", "$file_modified_time")
INPUTS = (
    "policies/access-matrix.csv",
    "policies/schema-contract.yaml",
    "docker/trino/auth/groups.txt",
    "docker/trino/access-control.properties",
    "docker/trino/read-only.properties",
    "docker/trino/config.properties",
)


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=True) + "\n").encode()


def digest(content):
    return hashlib.sha256(content).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def compile_policy(root=ROOT):
    groups = {}
    for line in (root / INPUTS[2]).read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            group, users = line.split(":", 1)
            require(group not in groups, "Duplicate group")
            groups[group] = users.split(",")
    require(groups == MEMBERS, "Unreviewed group membership (including overlapping roles)")
    tables = yaml.safe_load((root / INPUTS[1]).read_text())["tables"]
    with (root / INPUTS[0]).open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    expected = {
        (group, table, column)
        for group in MEMBERS
        for table, spec in tables.items()
        for column in spec["columns"]
    }
    counts = Counter((r["group"], r["table"], r["column"]) for r in rows)
    require(set(counts) == expected and set(counts.values()) == {1}, "Incomplete/duplicate matrix")
    policy = {
        "catalogs": [
            {"user": READER, "catalog": "hospital", "allow": "read-only"},
            *[
                {
                    "user": "|".join(users),
                    "group": group,
                    "catalog": "hospital|audit" if group == "admin" else "hospital",
                    "allow": "read-only",
                }
                for group, users in MEMBERS.items()
            ],
            {"catalog": ".*", "allow": "none"},  # Override implicit system access.
        ],
        "schemas": [],  # No schema ownership; SELECT uses table permissions.
        "tables": [
            {
                "user": READER,
                "catalog": "hospital",
                "schema": "silver",
                "table": "care_team",
                "privileges": ["SELECT"],
                "columns": [{"name": name, "allow": False} for name in METADATA_COLUMNS],
            }
        ],
        "impersonation": [],
        "authorization": [],
        "system_information": [],
        "system_session_properties": [],
        "catalog_session_properties": [],
        "functions": [],  # Trino always permits system.builtin; no connector functions.
        "procedures": [],
        "queries": [
            {"user": "|".join(users), "group": group, "allow": ["execute"]}
            for group, users in MEMBERS.items()
        ],
    }
    # Trino's analyzer checks correlated outer token references under the filter
    # identity too. Grant only those token columns, without recursive filters.
    for name in ("patients", "encounters"):
        policy["tables"].append(
            {
                "user": READER,
                "catalog": "hospital",
                "schema": "silver",
                "table": name,
                "privileges": ["SELECT"],
                "columns": [
                    {"name": column, "allow": False}
                    for column in (*tables[name]["columns"], *METADATA_COLUMNS)
                    if column != "patient_token"
                ],
            }
        )
    for group, users in MEMBERS.items():
        for table, spec in tables.items():
            decisions = [r for r in rows if r["group"] == group and r["table"] == table]
            operations = {r["operations"] for r in decisions}
            scopes = {r["row_scope"] for r in decisions}
            require(len(operations) == len(scopes) == 1, "Inconsistent table scope/operations")
            operation, scope = next(iter(operations)), next(iter(scopes))
            require(operation in {"SELECT", "NONE"}, "Unknown operation")
            require(
                scope
                == (
                    "NONE"
                    if operation == "NONE"
                    else ("ACTIVE_CARE_TEAM" if group == "doctor" else "ALL_MVP")
                ),
                "Unreviewed row scope",
            )
            columns = [{"name": name, "allow": False} for name in METADATA_COLUMNS]
            for row in decisions:
                visibility = row["visibility"]
                require(visibility in {"visible", "hidden", "masked"}, "Unknown visibility")
                require(bool(row["mask_sql"]) == (visibility == "masked"), "Invalid mask")
                require(operation != "NONE" or visibility == "hidden", "Invalid denied table")
                if visibility == "hidden":
                    columns.append({"name": row["column"], "allow": False})
                elif visibility == "masked":
                    columns.append({"name": row["column"], "mask": row["mask_sql"]})
            if operation == "NONE":
                continue
            catalog, schema, name = spec["resource"].split(".")
            require(
                all(re.fullmatch(r"[a-z_]+", s) for s in (catalog, schema, name)),
                "Unsafe resource name",
            )
            for user in users:
                rule = {
                    "user": user,
                    "group": group,
                    "catalog": catalog,
                    "schema": schema,
                    "table": name,
                    "privileges": ["SELECT"],
                }
                if columns:
                    rule["columns"] = columns
                if scope == "ACTIVE_CARE_TEAM":
                    require(table in {"patients", "encounters"}, "Unreviewed doctor table")
                    # current_user inside this environment is READER, not the doctor.
                    # An exact user+group rule binds this trusted literal to the caller.
                    rule["filter"] = (
                        "EXISTS (SELECT 1 FROM hospital.silver.care_team AS ct "
                        f"WHERE ct.patient_token = {name}.patient_token "
                        f"AND ct.doctor_user = '{user}' "
                        "AND ct.valid_from <= CAST(current_timestamp AT TIME ZONE 'UTC' AS DATE) "
                        "AND (ct.valid_to IS NULL OR "
                        "CAST(current_timestamp AT TIME ZONE 'UTC' AS DATE) < ct.valid_to))"
                    )
                    rule["filter_environment"] = {"user": READER}
                policy["tables"].append(rule)
    policy["tables"].append({"privileges": []})
    return policy


def metadata(content, root=ROOT):
    return {
        "policy_version": VERSION,
        "trino_version": "476",
        "policy_digest": digest(content),
        "input_sha256": {path: digest((root / path).read_bytes()) for path in INPUTS},
    }


def check(root=ROOT):
    content = (root / "policies/rules.json").read_bytes()
    require(content == encode(compile_policy(root)), "rules.json drift; review and run --write")
    manifest = metadata(content, root)
    require(
        (root / "policies/version.json").read_bytes() == encode(manifest),
        "Policy metadata drift; review and run --write",
    )
    return manifest


def record_experiment(directory, root=ROOT):
    manifest = check(root)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    files = {
        "policy-manifest.json": encode(manifest),
        "rules.json": (root / "policies/rules.json").read_bytes(),
    }
    for path in INPUTS:
        files[path.replace("/", "__")] = (root / path).read_bytes()
    # A reused run directory must never silently acquire a different policy.
    for name, content in files.items():
        path = directory / name
        if path.exists():
            require(
                path.read_bytes() == content, "Experiment policy differs; use a new run directory"
            )
    for name, content in files.items():
        path = directory / name
        try:
            with path.open("xb") as handle:
                path.chmod(0o600)
                handle.write(content)
        except FileExistsError:
            require(path.read_bytes() == content, "Concurrent experiment policy change")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="Regenerate reviewed rules and digest")
    parser.add_argument(
        "--experiment-dir", type=Path, help="Freeze policy/configuration with a run"
    )
    args = parser.parse_args()
    try:
        if args.write:
            content = encode(compile_policy())
            (ROOT / "policies/rules.json").write_bytes(content)
            (ROOT / "policies/version.json").write_bytes(encode(metadata(content)))
        manifest = record_experiment(args.experiment_dir) if args.experiment_dir else check()
        print(json.dumps(manifest, sort_keys=True))
    except (ValueError, OSError) as error:
        parser.exit(1, f"Policy check failed: {error}\n")


if __name__ == "__main__":
    main()
