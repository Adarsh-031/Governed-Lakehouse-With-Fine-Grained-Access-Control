#!/usr/bin/env bash
# Resolve the project from this script, even when invoked from another directory.
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec uv run --locked --group dev --project "$script_dir/.." \
  python "$script_dir/init_secrets.py" "$@"
