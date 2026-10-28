#!/usr/bin/env bash
# Load one already-generated sample through the private trusted ETL path.
set -euo pipefail
# Do not let inherited shell tracing disclose Compose environment substitutions.
set +x
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(dirname -- "$script_dir")"
source_dir="${INGEST_SOURCE_DIR:-$repo_dir/data/raw/synthea/1000-seed42}"
export INGEST_SOURCE_DIR="$source_dir"
cd "$repo_dir"
exec docker compose --env-file "${LAKEHOUSE_ENV_FILE:-data/secrets/runtime.env}" \
  run --build --rm ingest -m ingest.pipeline "$@"
