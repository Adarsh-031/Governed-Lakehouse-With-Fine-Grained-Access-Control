"""Pure helpers for the policy-overhead benchmark (#25): workloads, baselines, statistics.

The enforced query is the role's own SQL from sql/queries/, run as that role so Trino
injects the rules.json row filter and column masks. The baseline is the same SQL run
as admin (no filters or masks on silver) with each table reference replaced by a
subquery that applies the role's filter and masks by hand. Both must return the same
rows before any timing is reported.
"""

import json
import math
import re
import statistics
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ROLE_USERS = {"doctor": "doctor_alice", "researcher": "researcher", "billing": "billing"}
BASELINE_USER = "admin"
TABLE_REFERENCE = re.compile(r"hospital\.silver\.(\w+)(\s+AS\s+(\w+))?", re.IGNORECASE)
TRAILING_LIMIT = re.compile(r"\s+LIMIT\s+\d+\s*$", re.IGNORECASE)


def parse_queries(path: Path) -> list[dict]:
    """Split a sql/queries file into {id, sql, columns} using its comment headers."""
    queries = []
    # Statements end with ";" at end of line; comments may contain ";" mid-line.
    for chunk in re.split(r";[ \t]*$", path.read_text(), flags=re.MULTILINE):
        query_id = re.search(r"^--\s*([A-Z]\d+)\.", chunk, re.MULTILINE)
        columns = re.search(r"^--\s*Expected columns:\s*(.+?)\.?\s*$", chunk, re.MULTILINE)
        sql = "\n".join(
            line
            for line in chunk.splitlines()
            if line.strip() and not line.lstrip().startswith("--")
        ).strip()
        if not sql:
            continue
        if not (query_id and columns):
            raise ValueError(f"{path.name}: query without ID or expected columns")
        queries.append(
            {
                "id": query_id.group(1),
                "sql": sql,
                "columns": [c.strip() for c in columns.group(1).split(",")],
            }
        )
    return queries


def load_policy(root: Path = ROOT):
    rules = json.loads((root / "policies/rules.json").read_text())["tables"]
    groups = {}
    for line in (root / "docker/trino/auth/groups.txt").read_text().splitlines():
        if ":" in line:
            group, members = line.split(":", 1)
            for member in filter(None, members.split(",")):
                groups.setdefault(member.strip(), []).append(group.strip())
    contract = yaml.safe_load((root / "policies/schema-contract.yaml").read_text())["tables"]
    columns = {name: list(table["columns"]) for name, table in contract.items()}
    return rules, groups, columns


def table_rule(rules, groups, user, table, catalog="hospital", schema="silver"):
    """First matching rule, as Trino's file-based access control evaluates them."""

    def matches(pattern, value):
        return pattern is None or re.fullmatch(pattern, value) is not None

    for rule in rules:
        if (
            matches(rule.get("user"), user)
            and (
                rule.get("group") is None
                or any(matches(rule["group"], g) for g in groups.get(user, []))
            )
            and matches(rule.get("catalog"), catalog)
            and matches(rule.get("schema"), schema)
            and matches(rule.get("table"), table)
        ):
            return rule
    return {"privileges": []}


def baseline_sql(sql, user, rules, groups, table_columns):
    """Rewrite SQL so admin computes the same rows the policy gives `user`."""

    def replace(match):
        table, alias = match.group(1), match.group(3) or match.group(1)
        rule = table_rule(rules, groups, user, table)
        if "SELECT" not in rule.get("privileges", []):
            raise ValueError(f"{user} has no SELECT on {table}")
        policy = {c["name"]: c for c in rule.get("columns", [])}
        projection = []
        for column in table_columns[table]:
            entry = policy.get(column, {})
            if entry.get("allow") is False:
                continue
            projection.append(f"{entry['mask']} AS {column}" if entry.get("mask") else column)
        where = f" WHERE {rule['filter']}" if rule.get("filter") else ""
        inner = f"SELECT {', '.join(projection)} FROM hospital.silver.{table} AS {table}{where}"
        return f"({inner}) AS {alias}"

    return TABLE_REFERENCE.sub(replace, sql)


def verification_sql(sql, columns):
    """Order-insensitive fingerprint of the full result (trailing LIMIT removed)."""
    body = TRAILING_LIMIT.sub("", sql)
    return (
        f"SELECT count(*) AS result_rows, checksum(ROW({', '.join(columns)})) AS fingerprint "
        f"FROM ({body}) AS q"
    )


def workloads(root: Path = ROOT):
    rules, groups, table_columns = load_policy(root)
    items = []
    for role, user in ROLE_USERS.items():
        for query in parse_queries(root / f"sql/queries/{role}.sql"):
            items.append(
                {
                    "role": role,
                    "user": user,
                    "query_id": query["id"],
                    "columns": query["columns"],
                    "enforced": query["sql"],
                    "baseline": baseline_sql(query["sql"], user, rules, groups, table_columns),
                }
            )
    return items


def percentile(values, fraction):
    """Nearest-rank percentile; with 10 samples p95 is the slowest run."""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(fraction * len(ordered)) - 1)]


def summarize(samples):
    """samples: dicts with query_id, role, variant, phase, elapsed_ms, cpu_ms, peak_memory_bytes."""
    groups = {}
    for sample in samples:
        key = (sample["role"], sample["query_id"], sample["phase"], sample["variant"])
        groups.setdefault(key, []).append(sample)
    rows = []
    for (role, query_id, phase, variant), items in sorted(groups.items()):
        elapsed = [s["elapsed_ms"] for s in items]
        rows.append(
            {
                "role": role,
                "query_id": query_id,
                "phase": phase,
                "variant": variant,
                "runs": len(items),
                "median_ms": statistics.median(elapsed),
                "p95_ms": percentile(elapsed, 0.95),
                "median_cpu_ms": statistics.median(s["cpu_ms"] for s in items),
                "median_peak_memory_bytes": statistics.median(
                    s["peak_memory_bytes"] for s in items
                ),
                "result_rows": items[0]["result_rows"],
            }
        )
    baseline = {
        (r["role"], r["query_id"], r["phase"]): r["median_ms"]
        for r in rows
        if r["variant"] == "baseline"
    }
    for row in rows:
        base = baseline.get((row["role"], row["query_id"], row["phase"]))
        row["overhead_pct_vs_baseline"] = (
            round(100 * (row["median_ms"] - base) / base, 1)
            if row["variant"] == "enforced" and base
            else ""
        )
    return rows
