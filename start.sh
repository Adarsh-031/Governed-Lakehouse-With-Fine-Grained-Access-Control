#!/usr/bin/env bash
# One-command local start: secrets, storage, catalog, Trino + audit, data, dashboard.
# Safe to re-run: every step keeps what already exists.
#
# Usage: ./start.sh [--patients N] [--seed S] [--no-dashboard]
set -euo pipefail
set +x  # never trace: Compose substitutes secrets into the environment

patients=1000
seed=42
dashboard=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --patients) patients="$2"; shift 2 ;;
    --seed) seed="$2"; shift 2 ;;
    --no-dashboard) dashboard=0; shift ;;
    -h|--help) sed -n '2,6p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$repo_dir"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }
fail() { printf '\033[1;31mError: %s\033[0m\n' "$*" >&2; exit 1; }

step "Checking prerequisites"
for cmd in docker uv java openssl; do
  command -v "$cmd" >/dev/null || fail "'$cmd' is not installed"
done
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
docker info >/dev/null 2>&1 || fail "Docker is not running (try: sudo service docker start)"

step "Installing Python dependencies"
uv sync --locked --group dev

step "Secrets, users and TLS (kept if they already exist)"
./scripts/init_secrets.sh
export TRINO_UID="$(id -u)" TRINO_GID="$(id -g)" AUDIT_UID="$(id -u)" AUDIT_GID="$(id -g)"
env_file="data/secrets/runtime.env"
[[ -f "$env_file" ]] || fail "$env_file was not created"
dc() { docker compose --env-file "$env_file" "$@"; }

step "Storage"
dc up -d --wait storage
dc run --rm storage-init

step "Catalog"
dc build catalog-init
dc up -d --wait catalog
dc run --rm --no-deps catalog-init

step "Trino and the audit pipeline"
dc up -d --wait trino audit-writer

dataset="data/raw/synthea/${patients}-seed${seed}"
if [[ -f "$dataset/manifest.json" ]]; then
  step "Synthetic data already generated at $dataset"
else
  step "Generating $patients synthetic patients (seed $seed), takes a few minutes"
  ./scripts/generate_synthea.sh --patients "$patients" --seed "$seed"
fi

step "Loading silver, identity and gold tables (existing tables are verified, not duplicated)"
INGEST_SOURCE_DIR="$repo_dir/$dataset" ./scripts/ingest.sh

step "Stack is up"
dc ps --format 'table {{.Service}}\t{{.Status}}'
echo
echo "Trino:     https://localhost:8443"
echo "Passwords: $env_file (TRINO_PASSWORD_DOCTOR_ALICE, _DOCTOR_BOB, _RESEARCHER, _BILLING, _ADMIN)"
echo "Stop with: ./stop.sh"

if [[ "$dashboard" -eq 1 ]]; then
  step "Starting the dashboard at http://localhost:8501 (Ctrl+C stops only the dashboard)"
  export TRINO_HOST=localhost TRINO_PORT=8443 TRINO_CA_BUNDLE="$repo_dir/data/secrets/ca.crt"
  exec uv run --no-sync --with streamlit streamlit run dashboard/app.py
fi
