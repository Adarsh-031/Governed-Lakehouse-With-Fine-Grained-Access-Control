#!/usr/bin/env bash
# Disposable full-stack smoke test for GitHub Actions.
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "$0")/.." && pwd)"
cd "$ROOT"

SECRETS_DIR="$ROOT/data/secrets"
ENV_FILE="$SECRETS_DIR/runtime.env"
PROJECT_NAME="lakehouse-ci-${GITHUB_RUN_ID:?GitHub run ID is required}-${GITHUB_RUN_ATTEMPT:?GitHub run attempt is required}"
SECRETS_GENERATION_ATTEMPTED=0
STACK_ATTEMPTED=0

# data/secrets/README.md is tracked. Preserve it, and refuse to overwrite any
# pre-existing generated credentials from a developer or another process.
if [[ -L "$SECRETS_DIR" ]]; then
  echo "Refusing a symlinked data/secrets directory." >&2
  exit 1
fi
for name in runtime.env password.db ca.key ca.crt trino.key trino.crt trino.pem; do
  if [[ -e "$SECRETS_DIR/$name" || -L "$SECRETS_DIR/$name" ]]; then
    echo "Refusing to overwrite existing generated secret: $name" >&2
    exit 1
  fi
done

dc() {
  docker compose \
    --project-name "$PROJECT_NAME" \
    --file "$ROOT/compose.yaml" \
    --env-file "$ENV_FILE" \
    "$@"
}

cleanup() {
  local status=$?
  trap - EXIT

  if [[ "$STACK_ATTEMPTED" -eq 1 && -f "$ENV_FILE" ]]; then
    if ! dc down --volumes --remove-orphans --timeout 30; then
      echo "Compose teardown failed for project $PROJECT_NAME." >&2
      if [[ "$status" -eq 0 ]]; then
        status=1
      fi
    fi
  fi

  if [[ "$SECRETS_GENERATION_ATTEMPTED" -eq 1 ]]; then
    for name in runtime.env password.db ca.key ca.crt trino.key trino.crt trino.pem; do
      if ! rm -f -- "$SECRETS_DIR/$name"; then
        echo "Could not remove disposable secret: $name" >&2
        status=1
      fi
    done
    # The generator makes this directory private; restore the checkout's
    # ordinary directory permissions while retaining the tracked README.
    if [[ -d "$SECRETS_DIR" && ! -L "$SECRETS_DIR" ]]; then
      chmod 755 "$SECRETS_DIR" || status=1
    fi
  fi

  exit "$status"
}
trap cleanup EXIT

# Use the repository's credential generator, with a fresh CA and random
# service/user secrets. Files are private and ignored by Git.
mkdir -p "$ROOT/data"
SECRETS_GENERATION_ATTEMPTED=1
uv run --locked --no-sync python - <<'PY'
from pathlib import Path

from scripts.init_secrets import initialize

initialize(Path("data/secrets"))
PY

# Trino runs unprivileged and needs access to the private 0600 host files.
export TRINO_UID="$(id -u)"
export TRINO_GID="$(id -g)"

STACK_ATTEMPTED=1

# --build creates the pinned catalog helper image when it is not present.
# --wait does not return until dependencies, catalog bootstrap and Trino health
# are ready.
dc up --build --detach --wait --wait-timeout 900 trino

# The generated values are hex or simple fixed paths; this file is created here.
set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a

# Password goes over stdin to the in-container CLI, never in argv or logs.
query_output="$(
  printf '%s\n' "$TRINO_PASSWORD_ADMIN" |
    dc exec -T trino /bin/bash -c '
      IFS= read -r TRINO_PASSWORD
      export TRINO_PASSWORD
      exec trino \
        --server https://localhost:8443 \
        --user admin \
        --password \
        --truststore-path /etc/trino/auth/ca.crt \
        --output-format CSV \
        --execute "SELECT current_user"
    '
)"

printf '%s\n' "$query_output"
if ! grep -Fxq "admin" <<< "$query_output"; then
  echo "Smoke query did not return the authenticated admin identity." >&2
  exit 1
fi

echo "Trino HTTPS smoke query passed as admin."
