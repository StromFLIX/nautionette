#!/bin/sh
# Maintenance-only schema management. Never run implicitly on server startup.
set -eu
: "${POSTGRES_SEEDS:?Set POSTGRES_SEEDS}"
: "${POSTGRES_USER:?Set POSTGRES_USER}"
: "${POSTGRES_PWD:?Set POSTGRES_PWD}"
export PGHOST="$POSTGRES_SEEDS" PGPORT="${DB_PORT:-5432}"
export PGUSER="$POSTGRES_USER" PGPASSWORD="$POSTGRES_PWD" SQL_PASSWORD="$POSTGRES_PWD"
export PGCONNECT_TIMEOUT=5
core=${DBNAME:-temporal}
visibility=${VISIBILITY_DBNAME:-temporal_visibility}
case "${TEMPORAL_TOOLS_VERSION:?}" in
    1.29.7) previous=1.29.0; core_before=1.18; vis_before=1.9; core_after=1.18; vis_after=1.9 ;;
    1.30.9) previous=1.29.7; core_before=1.18; vis_before=1.9; core_after=1.18; vis_after=1.13 ;;
    1.31.5) previous=1.30.9; core_before=1.18; vis_before=1.13; core_after=1.19; vis_after=1.14 ;;
    1.32.2) previous=1.31.5; core_before=1.19; vis_before=1.14; core_after=1.19; vis_after=1.14 ;;
    *) echo 'Unsupported tools release; update and rehearse the migration plan first.' >&2; exit 1 ;;
esac
sql() {
    temporal-sql-tool --plugin postgres12 --ep "$PGHOST" -p "$PGPORT" \
        -u "$PGUSER" --db "$1" "$2" ${3:+"$3"} ${4:+"$4"}
}
version() {
    psql -X -A -t -v ON_ERROR_STOP=1 --dbname "$1" -c 'SELECT curr_version FROM schema_version'
}
database_exists() {
    printf "SELECT datname FROM pg_database WHERE datname = :'db';\n" | \
        psql -X -A -t -v ON_ERROR_STOP=1 --dbname postgres -v db="$1"
}
check_versions() {
    actual_core=$(version "$core")
    actual_vis=$(version "$visibility")
    if [ "$actual_core" != "$1" ] || [ "$actual_vis" != "$2" ]; then
        echo "Schema mismatch: core=$actual_core visibility=$actual_vis; expected $1/$2. Stop and investigate; do not skip a server hop." >&2
        exit 1
    fi
}
case "${1:-}" in
    status)
        printf 'core=%s visibility=%s tools=%s\n' "$(version "$core")" "$(version "$visibility")" "$TEMPORAL_TOOLS_VERSION"
        ;;
    initialize-empty)
        # Explicit fresh-install operation only. Refuse either existing schema,
        # including a half-initialized database; never mistake an outage for empty.
        for db in "$core" "$visibility"; do
            exists=$(database_exists "$db")
            if [ -n "$exists" ]; then
                tables=$(psql -X -A -t -v ON_ERROR_STOP=1 --dbname "$db" \
                    -c "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
                [ "$tables" = 0 ] || { echo "Refusing nonempty database $db" >&2; exit 1; }
            fi
        done
        for db in "$core" "$visibility"; do
            exists=$(database_exists "$db")
            [ -n "$exists" ] || sql "$db" create-database
            sql "$db" setup-schema -v 0.0
        done
        sql "$core" update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned
        sql "$visibility" update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned
        check_versions "$core_after" "$vis_after"
        ;;
    "upgrade-from-$previous")
        # Require the exact reviewed predecessor. A partial failure fails closed:
        # inspect schema_update_history and recover deliberately, not blind retry.
        check_versions "$core_before" "$vis_before"
        sql "$core" update-schema -d /etc/temporal/schema/postgresql/v12/temporal/versioned
        sql "$visibility" update-schema -d /etc/temporal/schema/postgresql/v12/visibility/versioned
        check_versions "$core_after" "$vis_after"
        ;;
    *) echo "Usage: status | initialize-empty | upgrade-from-$previous" >&2; exit 2 ;;
esac
