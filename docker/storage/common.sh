#!/bin/sh
# Sourced by the short-lived bootstrap/check containers. Never enable shell tracing.
set -eu
umask 077
export MC_CONFIG_DIR
MC_CONFIG_DIR=$(mktemp -d /tmp/lakehouse-mc.XXXXXX)
trap 'rm -rf "$MC_CONFIG_DIR"' EXIT

# Suppress client output, which can include credential-bearing command arguments.
quiet() {
    if ! "$@" >"$MC_CONFIG_DIR/result" 2>&1; then
        echo "Storage command failed; client output suppressed to protect credentials." >&2
        exit 1
    fi
}

quiet mc alias set admin http://storage:9000 "$STORAGE_ROOT_USER" "$STORAGE_ROOT_PASSWORD"
