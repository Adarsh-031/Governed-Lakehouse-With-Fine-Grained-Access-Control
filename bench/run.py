"""Policy-overhead benchmark runner (#25), following bench/plan.yaml.

For each patient scale whose Synthea sample exists, this starts a fresh isolated
Compose project (throw-away credentials, like the integration tests), ingests the
sample through the trusted ETL path, verifies that every enforced query returns the
same rows as its hand-written admin baseline, then times cold and warm runs.

    uv run --locked --group dev python -m bench.run --scales 1000 10000

Results go to results/raw/bench-<UTC timestamp>/. Scales without a generated
sample are recorded as not run, never estimated.
"""

import argparse
import csv
import json
import os
import platform
import ssl
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

from bench.workload import BASELINE_USER, ROOT, summarize, verification_sql, workloads

PLAN = yaml.safe_load((ROOT / "bench/plan.yaml").read_text())
SAMPLE_FIELDS = (
    "scale",
    "role",
    "query_id",
    "variant",
    "phase",
    "rep",
    "user",
    "wall_ms",
    "elapsed_ms",
    "cpu_ms",
    "peak_memory_bytes",
    "processed_rows",
    "processed_bytes",
    "result_rows",
)


class QueryError(RuntimeError):
    pass


class Trino:
    """Minimal CA-verified HTTPS client; passwords never reach output."""

    def __init__(self, compose):
        self.compose = compose
        self.connect()

    def connect(self):
        address = self.compose("port", "trino", "8443").strip().splitlines()[-1]
        _, port = address.rsplit(":", 1)
        context = ssl.create_default_context(cafile=str(self.compose.ca))
        self.client = httpx.Client(
            base_url=f"https://localhost:{port}", verify=context, trust_env=False, timeout=60
        )

    def restart(self):
        self.client.close()
        self.compose("restart", "trino")
        self.compose("up", "-d", "--wait", "trino")
        self.connect()
        self.query(BASELINE_USER, "SELECT 1", retries=30)

    def version(self):
        response = self.client.get("/v1/info")
        return response.json().get("nodeVersion", {}).get("version")

    def query(self, user, sql, retries=0):
        for attempt in range(retries + 1):
            try:
                return self._query(user, sql)
            except QueryError as error:
                # Trino reports no worker until the node registers after a restart.
                if attempt == retries or "No nodes available" not in str(error):
                    raise
                time.sleep(2)

    def _query(self, user, sql):
        auth = httpx.BasicAuth(user, self.compose.values[f"TRINO_PASSWORD_{user.upper()}"])
        headers = {"X-Trino-User": user}
        started = time.perf_counter()
        response = self.client.post("/v1/statement", content=sql, auth=auth, headers=headers)
        if response.status_code != 200:
            raise QueryError(f"HTTP {response.status_code}")
        result, rows = response.json(), []
        while True:
            rows.extend(result.get("data", []))
            if "nextUri" not in result:
                break
            result = self.client.get(result["nextUri"], auth=auth, headers=headers).json()
        wall_ms = (time.perf_counter() - started) * 1000
        if "error" in result:
            error = result["error"]
            raise QueryError(f"{error.get('errorName')}: {error.get('message', '')[:300]}")
        stats = result.get("stats", {})
        return rows, {
            "wall_ms": round(wall_ms, 2),
            "elapsed_ms": stats.get("elapsedTimeMillis"),
            "cpu_ms": stats.get("cpuTimeMillis"),
            "peak_memory_bytes": stats.get("peakMemoryBytes"),
            "processed_rows": stats.get("processedRows"),
            "processed_bytes": stats.get("processedBytes"),
            "result_rows": len(rows),
        }


def json_line(output, opener="{"):
    return json.loads(next(line for line in output.splitlines() if line.startswith(opener)))


def write_csv(path, rows, fields=None):
    rows = list(rows)
    fields = fields or (list(rows[0]) if rows else [])
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def hardware():
    info = {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "python": platform.python_version(),
    }
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                info["cpu_model"] = line.split(":", 1)[1].strip()
                break
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemTotal"):
                info["memory_total"] = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    docker = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    info["docker_server"] = docker.stdout.strip() or None
    return info


def run_scale(scale, source, run_dir, reps, isolated_compose):
    """Benchmark one scale in a fresh project; returns the scale's environment record."""
    items = workloads()
    with tempfile.TemporaryDirectory(prefix=f"bench-{scale}-") as tmp:
        tmp = Path(tmp)
        evidence = tmp / "evidence"
        evidence.mkdir(mode=0o700)
        with isolated_compose(tmp) as compose:
            compose.override_file.write_text(
                compose.override_file.read_text()
                + "  ingest:\n"
                + f'    user: "{os.getuid()}:{os.getgid()}"\n'
                + "    volumes:\n"
                + f"      - {source}:/source:ro\n"
                + f"      - {evidence}:/evidence:rw\n"
                + f"      - {ROOT / 'bench/storage_stats.py'}:/app/storage_stats.py:ro\n"
            )
            compose("build", "ingest")
            started = time.perf_counter()
            report = json_line(compose("run", "--rm", "ingest", "-m", "ingest.pipeline"))
            ingest_seconds = round(time.perf_counter() - started, 1)
            if not report.get("readback_verified"):
                raise RuntimeError("ETL readback was not verified")
            storage = json_line(
                compose("run", "--rm", "--no-deps", "ingest", "/app/storage_stats.py")
            )
            write_csv(
                run_dir / f"storage-{scale}.csv",
                ({"scale": scale, "table": name, **values} for name, values in storage.items()),
            )
            compose("up", "-d", "--wait", "trino")
            trino = Trino(compose)
            trino.query(BASELINE_USER, "SELECT 1", retries=30)

            # 1. Equivalence: enforced output must match the policy-equivalent baseline.
            verification = []
            for item in items:
                enforced, _ = trino.query(
                    item["user"], verification_sql(item["enforced"], item["columns"])
                )
                baseline, _ = trino.query(
                    BASELINE_USER, verification_sql(item["baseline"], item["columns"])
                )
                match = enforced == baseline
                verification.append(
                    {
                        "scale": scale,
                        "role": item["role"],
                        "query_id": item["query_id"],
                        "enforced_rows": enforced[0][0],
                        "baseline_rows": baseline[0][0],
                        "fingerprints_match": match,
                        # Rows the enforced query returned beyond the policy-equivalent result.
                        "unauthorized_rows": 0
                        if match
                        else max(0, enforced[0][0] - baseline[0][0]),
                    }
                )
            write_csv(run_dir / f"verification-{scale}.csv", verification)
            verified = [
                item
                for item, check in zip(items, verification, strict=True)
                if check["fingerprints_match"]
            ]

            samples = []

            def measure(item, variant, phase, rep):
                user = item["user"] if variant == "enforced" else BASELINE_USER
                _, stats = trino.query(user, item[variant])
                samples.append(
                    {
                        "scale": scale,
                        "role": item["role"],
                        "query_id": item["query_id"],
                        "variant": variant,
                        "phase": phase,
                        "rep": rep,
                        "user": user,
                        **stats,
                    }
                )

            # 2. Cold: first execution of each query after a Trino restart, per variant.
            for variant in ("baseline", "enforced"):
                trino.restart()
                for item in verified:
                    measure(item, variant, "cold", 0)
            # 3. Warm: repetitions, alternating which variant runs first to cancel drift.
            for rep in range(1, reps + 1):
                order = ("baseline", "enforced") if rep % 2 else ("enforced", "baseline")
                for item in verified:
                    for variant in order:
                        measure(item, variant, "warm", rep)
            write_csv(run_dir / f"raw-{scale}.csv", samples, SAMPLE_FIELDS)
            write_csv(
                run_dir / f"summary-{scale}.csv",
                ({"scale": scale, **row} for row in summarize(samples)),
            )
            images = compose("images", "--format", "json")
            trino_version = trino.version()
            trino.client.close()
    manifest = json.loads((source / "manifest.json").read_text())
    return {
        "scale": scale,
        "source_parameters": manifest.get("parameters"),
        "source_tables": {
            name: {"rows": t.get("rows"), "sha256": t.get("sha256")}
            for name, t in manifest.get("tables", {}).items()
        },
        "ingest_seconds": ingest_seconds,
        "ingest_rows": report.get("rows"),
        "storage": storage,
        "trino_version": trino_version,
        "images": json_line(images, "[") if "[" in images else images,
        "queries_verified": len(verified),
        "queries_total": len(items),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scales", type=int, nargs="+", default=PLAN["patient_counts"])
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--reps", type=int, default=PLAN["repetitions"])
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data/raw/synthea")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    from tests.conftest import isolated_compose

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = (args.output or ROOT / f"results/raw/bench-{stamp}").resolve()
    run_dir.mkdir(parents=True, exist_ok=False)
    # Freeze the exact policy beside the results (bench/plan.yaml policy_evidence).
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/policy.py"),
            "--experiment-dir",
            str(run_dir / "policy"),
        ],
        check=True,
        cwd=ROOT,
    )
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=ROOT, check=False
    ).stdout.strip()
    environment = {
        "started_utc": stamp,
        "git_commit": commit or None,
        "hardware": hardware(),
        "repetitions": args.reps,
        "seed": args.seed,
        "baseline": "admin running the role SQL with the role's rules.json filter and masks "
        "inlined as subqueries; same Trino, same isolated benchmark project",
        "cold_definition": "first execution of each query after restarting the Trino "
        "container; Silo/OS page cache is not dropped and no Trino file cache is configured",
        "scales": {},
    }
    failed = False
    for scale in args.scales:
        source = args.data_dir / f"{scale}-seed{args.seed}"
        if not (source / "manifest.json").is_file():
            environment["scales"][str(scale)] = {
                "status": "not_run",
                "reason": f"no generated sample at {source.relative_to(ROOT) if source.is_relative_to(ROOT) else source}",
            }
            continue
        try:
            record = run_scale(scale, source.resolve(), run_dir, args.reps, isolated_compose)
            record["status"] = (
                "run"
                if record["queries_verified"] == record["queries_total"]
                else "run_with_mismatch"
            )
            failed |= record["status"] != "run"
        except BaseException as error:  # pytest.fail() from the compose helper is a BaseException
            if isinstance(error, KeyboardInterrupt):
                raise
            failed = True
            record = {"status": "failed", "reason": f"{type(error).__name__}: {str(error)[:500]}"}
        environment["scales"][str(scale)] = record
        (run_dir / "environment.json").write_text(json.dumps(environment, indent=2, default=str))
    (run_dir / "environment.json").write_text(json.dumps(environment, indent=2, default=str))
    print(json.dumps({s: r["status"] for s, r in environment["scales"].items()}))
    print(f"results: {run_dir.relative_to(ROOT) if run_dir.is_relative_to(ROOT) else run_dir}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
