"""Persist incomplete attempts honestly, including failures and residual risks."""

import csv
import json
import os
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

ROOT = Path(__file__).resolve().parents[2]
STATUSES = {"not_run", "blocked", "leaked", "inconclusive", "not_applicable"}


def load_cases(path=ROOT / "tests/bypass/cases.csv"):
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(line for line in handle if line.strip()))
    names = [row["case"] for row in rows]
    if not names or not all(names) or len(names) != len(set(names)):
        raise ValueError("Bypass cases must have unique, nonempty names")
    if any(row["status"] not in STATUSES for row in rows):
        raise ValueError("Unknown bypass status")
    return rows


@contextmanager
def private_output(path, *, newline=None):
    # Replace complete files atomically; interruption leaves the previous report.
    with NamedTemporaryFile("w", dir=path.parent, newline=newline, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink()
            raise
    temporary.replace(path)


def linkage_metrics(observed, auxiliary):
    """Match auxiliary QIs to released tokens; ground truth only scores matches.

    Inputs are (token, age_band, sex, masked_zip). Incomplete keys are excluded,
    not treated as evidence of identification. Denominator is the full population.
    """
    released = {}
    for token, *values in observed:
        key = tuple(values)
        if all(value is not None for value in key):
            released.setdefault(key, set()).add(token)
    correct = attempted = complete = 0
    for token, *values in auxiliary:
        key = tuple(values)
        if any(value is None for value in key):
            continue
        complete += 1
        candidates = released.get(key, set())
        if len(candidates) == 1:
            attempted += 1
            correct += token in candidates
    population = len(auxiliary)
    return {
        "population": population,
        "complete_auxiliary_rows": complete,
        "unique_matches": attempted,
        "correct_links": correct,
        "linkage_rate": correct / population if population else None,
        "match_precision": correct / attempted if attempted else None,
    }


class Report:
    def __init__(self, directory, cases=None):
        self.directory = Path(directory)
        # Refuse to overwrite another experiment, including an incomplete run.
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.cases = {
            row["case"]: {
                **row,
                "status": "not_run",
                "reason": "Case has not executed in this run.",
                "attempts": [],
                "follow_up": row.get("follow_up") or None,
            }
            for row in (load_cases() if cases is None else cases)
        }
        self.context = {}
        self.save()

    def save(self):
        payload = {
            "context": self.context,
            "summary": dict(Counter(row["status"] for row in self.cases.values())),
            "cases": list(self.cases.values()),
        }
        path = self.directory / "bypass-results.json"
        with private_output(path) as f:
            json.dump(payload, f, indent=2, default=str)
            f.write("\n")
        path = self.directory / "cases.csv"
        with private_output(path, newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["case", "expected", "evidence", "status", "reason", "follow_up"]
            )
            writer.writeheader()
            writer.writerows(
                {key: row[key] for key in writer.fieldnames} for row in self.cases.values()
            )


class Attempt:
    def __init__(self, report, name, access):
        self.report, self.access = report, access
        self.row = report.cases[name]

    def __enter__(self):
        self.row.update(status="inconclusive", reason="Attempt started but did not finish.")
        self.report.save()
        return self

    def __exit__(self, error_type, error, traceback):
        if error_type is None and self.row["reason"] == "Attempt started but did not finish.":
            self.row.update(status="blocked", reason="All attempted authorization boundaries held.")
        elif error_type is not None and self.row["status"] != "leaked":
            self.row.update(status="inconclusive", reason=f"Attempt failed: {error_type.__name__}.")
        self.report.save()

    def observe(self, **evidence):
        self.row["attempts"].append(evidence)
        self.report.save()

    def expect(self, condition, *, expected, observed, leak=True):
        self.observe(expected=expected, observed=observed)
        if not condition:
            self.row.update(
                status="leaked" if leak else "inconclusive",
                reason="Observed evidence differs from the expected boundary.",
            )
            self.report.save()
            raise AssertionError(self.row["reason"])

    def sql(self, statement, user="admin", *, denied=False, **kwargs):
        start = len(self.access.queries)
        try:
            rows = self.access.execute(statement, user, denied=denied, **kwargs)
        except AssertionError:
            if denied and self.access.queries[start:] and self.access.queries[-1]["error"] is None:
                self.row.update(status="leaked", reason="Forbidden SQL operation succeeded.")
            raise
        else:
            self.observe(
                rows=rows, identity=user, expected="PERMISSION_DENIED" if denied else "success"
            )
            return rows
        finally:
            for query in self.access.queries[start:]:
                self.observe(**query)
