"""Check matrix translation, first-match decisions and immutable run evidence."""

import csv
import json
import re
import shutil
from pathlib import Path

import pytest
import yaml

from scripts import policy

ROOT = Path(__file__).resolve().parents[2]
TABLES = yaml.safe_load((ROOT / "policies/schema-contract.yaml").read_text())["tables"]
with (ROOT / "policies/access-matrix.csv").open() as handle:
    ROWS = list(csv.DictReader(handle))


def first_rule(rules, **attributes):
    return next(
        (
            rule
            for rule in rules
            if all(re.fullmatch(rule.get(k, ".*"), v) for k, v in attributes.items())
        ),
        None,
    )


@pytest.mark.parametrize(
    "row", ROWS, ids=lambda r: "/".join(r[k] for k in ("group", "table", "column"))
)
def test_every_matrix_decision_matches_the_first_effective_rule(row):
    rules = policy.compile_policy()
    catalog, schema, table = TABLES[row["table"]]["resource"].split(".")
    for user in policy.MEMBERS[row["group"]]:
        rule = first_rule(
            rules["tables"],
            user=user,
            group=row["group"],
            catalog=catalog,
            schema=schema,
            table=table,
        )
        assert rule["privileges"] == (["SELECT"] if row["operations"] == "SELECT" else [])
        if row["operations"] == "NONE":
            continue
        constraint = next((c for c in rule.get("columns", []) if c["name"] == row["column"]), {})
        assert constraint.get("allow", True) == (row["visibility"] != "hidden")
        assert constraint.get("mask", "") == row["mask_sql"]
        assert ("filter" in rule) == (row["row_scope"] == "ACTIVE_CARE_TEAM")
        if "filter" in rule:
            assert f"ct.doctor_user = '{user}'" in rule["filter"]
            assert "current_user" not in rule["filter"]
            assert rule["filter_environment"] == {"user": policy.READER}


def test_no_omitted_default_sections_and_no_wildcard_select_or_system_grant():
    rules = policy.compile_policy()
    for key in (
        "schemas",
        "impersonation",
        "authorization",
        "system_information",
        "system_session_properties",
        "catalog_session_properties",
        "functions",
        "procedures",
    ):
        assert rules[key] == []
    for rule in rules["tables"][:-1]:
        assert rule["privileges"] == ["SELECT"]
        assert all(key in rule for key in ("user", "catalog", "schema", "table"))
    for user, group in (("admin", "admin"), ("stranger", "doctor"), ("doctor_alice", "admin")):
        assert (
            first_rule(rules["catalogs"], user=user, group=group, catalog="system")["allow"]
            == "none"
        )
    assert first_rule(
        rules["tables"],
        user=policy.READER,
        group="",
        catalog="hospital",
        schema="silver",
        table="care_team",
    )["privileges"] == ["SELECT"]
    reader = first_rule(
        rules["tables"],
        user=policy.READER,
        group="",
        catalog="hospital",
        schema="silver",
        table="patients",
    )
    assert reader["privileges"] == ["SELECT"]
    assert {c["name"] for c in reader["columns"]} == (
        set(TABLES["patients"]["columns"]) - {"patient_token"}
    ) | set(policy.METADATA_COLUMNS)
    assert all(policy.READER not in r["user"] for r in rules["queries"])


@pytest.fixture
def policy_root(tmp_path):
    for name in (*policy.INPUTS, "policies/rules.json", "policies/version.json"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, path)
    return tmp_path


def test_committed_rules_and_metadata_are_current_and_evidence_is_preserved(tmp_path):
    manifest = policy.check()
    directory = tmp_path / "run"
    assert policy.record_experiment(directory) == manifest
    assert policy.record_experiment(directory) == manifest
    assert json.loads((directory / "policy-manifest.json").read_text()) == manifest
    assert policy.digest((directory / "rules.json").read_bytes()) == manifest["policy_digest"]
    assert (directory / "rules.json").stat().st_mode & 0o777 == 0o600
    (directory / "rules.json").write_text("changed")
    with pytest.raises(ValueError, match="differs"):
        policy.record_experiment(directory)


@pytest.mark.parametrize(
    "change", ["overlap", "unknown", "missing", "duplicate", "schema", "rules", "config"]
)
def test_unreviewed_contract_and_runtime_drift_fail(policy_root, change):
    groups = policy_root / policy.INPUTS[2]
    matrix = policy_root / policy.INPUTS[0]
    if change == "overlap":
        groups.write_text(groups.read_text().replace("admin:admin", "admin:admin,doctor_alice"))
    elif change == "unknown":
        groups.write_text(groups.read_text() + "unknown:someone\n")
    elif change in {"missing", "duplicate"}:
        lines = matrix.read_text().splitlines()
        matrix.write_text(
            "\n".join(lines[:-1] if change == "missing" else [*lines, lines[-1]]) + "\n"
        )
    elif change == "schema":
        path = policy_root / policy.INPUTS[1]
        path.write_text(
            path.read_text().replace(
                "      zip: VARCHAR", "      zip: VARCHAR\n      secret: VARCHAR"
            )
        )
    else:
        path = policy_root / ("policies/rules.json" if change == "rules" else policy.INPUTS[-1])
        path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError):
        policy.check(policy_root)


def test_filter_reader_has_no_interactive_password_identity():
    # Secret generation retains exactly the five reviewed human login names.
    from scripts.init_secrets import USERS

    assert policy.READER not in USERS
