#!/bin/sh
set -eu
. /bootstrap/common.sh

# Sharing a key would merge roles (or attach a service policy to the root user).
for key in "$ETL_STORAGE_ACCESS_KEY" "$TRINO_STORAGE_ACCESS_KEY" "$AUDIT_WRITER_STORAGE_ACCESS_KEY"; do
    [ -n "$key" ] && [ "$key" != "$STORAGE_ROOT_USER" ] || exit 1
done
[ "$ETL_STORAGE_ACCESS_KEY" != "$TRINO_STORAGE_ACCESS_KEY" ] || exit 1
[ "$ETL_STORAGE_ACCESS_KEY" != "$AUDIT_WRITER_STORAGE_ACCESS_KEY" ] || exit 1
[ "$TRINO_STORAGE_ACCESS_KEY" != "$AUDIT_WRITER_STORAGE_ACCESS_KEY" ] || exit 1

quiet mc mb --ignore-existing admin/warehouse
# Revoke any previously configured anonymous bucket policy on every run.
quiet mc anonymous set none admin/warehouse

provision() {
    role=$1
    access_key=$2
    secret_key=$3
    quiet mc admin policy create admin "lakehouse-$role" "/bootstrap/policies/$role.json"
    quiet mc admin user add admin "$access_key" "$secret_key"
    # Refuse additive grants introduced outside this bootstrap. A successful rerun
    # must not silently leave a service with a broad policy or inherited group.
    quiet mc admin user info admin "$access_key"
    policy_seen=false
    groups_seen=false
    while IFS= read -r line; do
        case "$line" in
            'PolicyName: ')
                policy_seen=true ;;
            "PolicyName: lakehouse-$role")
                policy_seen=true ;;
            'PolicyName:'*)
                echo "Unexpected service policy binding; remove unmanaged grants before retrying." >&2
                exit 1 ;;
            'MemberOf: []')
                groups_seen=true ;;
            'MemberOf:'*)
                echo "Unexpected service group membership; remove unmanaged grants before retrying." >&2
                exit 1 ;;
        esac
    done <"$MC_CONFIG_DIR/result"
    [ "$policy_seen" = true ] && [ "$groups_seen" = true ] || exit 1
    quiet mc admin policy attach admin "lakehouse-$role" --user "$access_key"
}

provision etl "$ETL_STORAGE_ACCESS_KEY" "$ETL_STORAGE_SECRET_KEY"
provision trino "$TRINO_STORAGE_ACCESS_KEY" "$TRINO_STORAGE_SECRET_KEY"
provision audit-writer "$AUDIT_WRITER_STORAGE_ACCESS_KEY" "$AUDIT_WRITER_STORAGE_SECRET_KEY"
echo "Private warehouse bucket and service policies initialized."
