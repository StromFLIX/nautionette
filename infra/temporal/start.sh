#!/bin/sh
# Compose startup fence: Coolify can skip hooks when old containers are absent.
set -eu
if [ -n "${TEMPORAL_MAINTENANCE_APP:-}" ]; then
    case "$TEMPORAL_MAINTENANCE_APP" in *[!a-zA-Z0-9_-]*) exit 1 ;; esac
    : "${TEMPORAL_DEPLOY_COMMIT:?Missing deployment revision}"
    attempts=0
    while :; do
        candidate= phase=
        file="/etc/temporal/maintenance/$TEMPORAL_MAINTENANCE_APP"
        if [ -f "$file" ]; then
            { read -r candidate; read -r phase; } < "$file"
        fi
        if [ "$candidate" = "$TEMPORAL_DEPLOY_COMMIT" ]; then
            case "$phase" in current|prepared|completed) break ;; esac
        fi
        attempts=$((attempts + 1))
        if [ "$attempts" -ge 600 ]; then
            echo 'Temporal maintenance fence is not ready; refusing server startup.' >&2
            exit 1
        fi
        sleep 2
    done
fi
exec "$@"
