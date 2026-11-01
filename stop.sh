#!/usr/bin/env bash
# Stop all services. Data, tables and secrets are kept; ./start.sh brings everything back.
set -euo pipefail
set +x
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
export TRINO_UID="$(id -u)" TRINO_GID="$(id -g)" AUDIT_UID="$(id -u)" AUDIT_GID="$(id -g)"
docker compose --env-file data/secrets/runtime.env down
echo "Stopped. Data and secrets are kept."
