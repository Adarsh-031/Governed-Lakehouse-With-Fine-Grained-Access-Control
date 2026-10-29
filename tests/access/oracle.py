"""Test-side contract semantics; deliberately independent of scripts.policy/SQL masks."""

import csv
import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
USERS = {
    "doctor_alice": "doctor",
    "doctor_bob": "doctor",
    "researcher": "researcher",
    "billing": "billing",
    "admin": "admin",
}
TABLES = yaml.safe_load((ROOT / "policies/schema-contract.yaml").read_text())["tables"]
with (ROOT / "policies/access-matrix.csv").open(newline="") as handle:
    MATRIX = list(csv.DictReader(handle))
CASES = [(user, row) for user, group in USERS.items() for row in MATRIX if row["group"] == group]


def decisions(user, table):
    return [row for row in MATRIX if row["group"] == USERS[user] and row["table"] == table]


def canonical(value, sql_type):
    if value is None:
        return None
    if sql_type == "DATE":
        return date.fromisoformat(str(value)).isoformat()
    if sql_type == "TIMESTAMP(6)":
        return datetime.fromisoformat(str(value)).isoformat(sep=" ", timespec="microseconds")
    if sql_type.startswith("DECIMAL"):
        return str(Decimal(str(value)).quantize(Decimal("0.01")))
    if sql_type.startswith("ARRAY"):
        return tuple(value)
    return value


def masked_value(table, column, value):
    """Implement the written contract in Python; never execute matrix mask_sql."""
    if table == "patients" and column == "zip":
        if value is None:
            return None
        valid = len(value) == 5 or (
            len(value) == 10 and value[5] == "-" and all(c in "0123456789" for c in value[6:])
        )
        return value[:3] + "**" if valid and all(c in "0123456789" for c in value[:5]) else None
    if table == "encounters" and column in {"start_time", "stop_time"}:
        if value is None:
            return None
        return datetime.fromisoformat(str(value)).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
    raise AssertionError(f"No independent mask expectation for {table}.{column}")


class Oracle:
    def __init__(self, directory):
        self.seed = json.loads((directory / "care-team-oracle.json").read_text())
        self.boundary = json.loads((directory / "policy-boundary-oracle.json").read_text())
        self.data = json.loads((directory / "access-data-oracle.json").read_text())
        today = date.fromisoformat(self.boundary["utc_date"])
        assert date.fromisoformat(self.seed["valid_from"]) <= today
        assert self.seed["valid_to"] is None or today < date.fromisoformat(self.seed["valid_to"])
        self.doctors = {
            user: set(self.seed["expected_patient_tokens"][user])
            | set(self.boundary["expected_patient_tokens"][user])
            for user, group in USERS.items()
            if group == "doctor"
        }

    def tokens(self, user, table):
        # Some patients may have no encounters. Intersect saved memberships with
        # pre-write table inputs, never with live care_team or a privileged query.
        return self.doctors[user] & {row["patient_token"] for row in self.data[table]}

    def rows(self, user, table, columns):
        contract = {row["column"]: row for row in decisions(user, table)}
        assert all(contract[column]["operations"] == "SELECT" for column in columns)
        assert all(contract[column]["visibility"] != "hidden" for column in columns)
        source = self.data[table]
        if USERS[user] == "doctor":
            source = [row for row in source if row["patient_token"] in self.doctors[user]]
        result = []
        for row in source:
            values = []
            for column in columns:
                value = row.get(column)
                if contract[column]["visibility"] == "masked":
                    value = masked_value(table, column, value)
                values.append(canonical(value, TABLES[table]["columns"][column]))
            result.append(tuple(values))
        return result


def assert_rows(actual, expected, table, columns):
    observed = Counter(
        tuple(
            canonical(value, TABLES[table]["columns"][column])
            for column, value in zip(columns, row, strict=True)
        )
        for row in actual
    )
    wanted = Counter(expected)
    # Counts avoid printing memberships or cell contents into CI logs.
    extra, missing = sum((observed - wanted).values()), sum((wanted - observed).values())
    assert (extra, missing) == (0, 0), f"{table}: {extra} unexpected rows; {missing} missing rows"
