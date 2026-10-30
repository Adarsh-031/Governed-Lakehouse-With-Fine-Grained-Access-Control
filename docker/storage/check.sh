#!/bin/sh
# Exercise actual S3/IAM behavior using synthetic objects, then remove only our fixtures.
set -eu
. /bootstrap/common.sh
probe=".storage-check-${MC_CONFIG_DIR##*/}"
payload="$MC_CONFIG_DIR/payload"
printf 'synthetic storage permission fixture\n' >"$payload"

cleanup() {
    for prefix in bronze silver gold identity audit unassigned; do
        mc rm --force "admin/warehouse/$prefix/$probe/object.txt" >/dev/null 2>&1 || :
        mc rm --force "admin/warehouse/$prefix/$probe/write.txt" >/dev/null 2>&1 || :
    done
    rm -rf "$MC_CONFIG_DIR"
}
trap cleanup EXIT

denied() {
    if mc --json "$@" >"$MC_CONFIG_DIR/denied" 2>&1; then
        echo "FAIL: a forbidden $1 operation succeeded." >&2
        exit 1
    fi
    # A DNS error, absent object or invalid key is not evidence of authorization.
    result=$(cat "$MC_CONFIG_DIR/denied")
    case "$result" in
        *AccessDenied*|*'Access Denied'*|*'Insufficient permissions to access this path'*) ;;
        *) echo "FAIL: expected AccessDenied for $1; received another error." >&2; exit 1 ;;
    esac
}

quiet mc alias set etl http://storage:9000 "$ETL_STORAGE_ACCESS_KEY" "$ETL_STORAGE_SECRET_KEY"
quiet mc alias set trino http://storage:9000 "$TRINO_STORAGE_ACCESS_KEY" "$TRINO_STORAGE_SECRET_KEY"
quiet mc alias set audit http://storage:9000 "$AUDIT_WRITER_STORAGE_ACCESS_KEY" "$AUDIT_WRITER_STORAGE_SECRET_KEY"

for prefix in bronze silver gold identity audit unassigned; do
    quiet mc cp "$payload" "admin/warehouse/$prefix/$probe/object.txt"
done

# Anonymous listing AND GET of an object known to exist must both be forbidden.
for path in 'warehouse?list-type=2' "warehouse/silver/$probe/object.txt"; do
    code=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' "http://storage:9000/$path")
    [ "$code" = 403 ] || { echo "FAIL: anonymous request returned HTTP $code." >&2; exit 1; }
done

for prefix in bronze silver gold identity; do
    quiet mc ls "etl/warehouse/$prefix/$probe/"
    quiet mc cat "etl/warehouse/$prefix/$probe/object.txt"
    quiet mc cp "$payload" "etl/warehouse/$prefix/$probe/write.txt"
    quiet mc rm "etl/warehouse/$prefix/$probe/write.txt"
done
# Trino reads audit/ for the admin-only audit catalog; writes stay denied.
for prefix in silver gold audit; do
    quiet mc ls "trino/warehouse/$prefix/$probe/"
    quiet mc cat "trino/warehouse/$prefix/$probe/object.txt"
    denied cp "$payload" "trino/warehouse/$prefix/$probe/object.txt"
    denied rm "trino/warehouse/$prefix/$probe/object.txt"
done
quiet mc ls "audit/warehouse/audit/$probe/"
quiet mc cat "audit/warehouse/audit/$probe/object.txt"
quiet mc cp "$payload" "audit/warehouse/audit/$probe/write.txt"
denied rm "audit/warehouse/audit/$probe/object.txt"

check_blocked_prefix() {
    role=$1
    prefix=$2
    denied ls "$role/warehouse/$prefix/$probe/"
    denied cat "$role/warehouse/$prefix/$probe/object.txt"
    denied cp "$payload" "$role/warehouse/$prefix/$probe/object.txt"
    denied rm "$role/warehouse/$prefix/$probe/object.txt"
}
for prefix in audit unassigned; do check_blocked_prefix etl "$prefix"; done
for prefix in bronze identity unassigned; do check_blocked_prefix trino "$prefix"; done
for prefix in bronze silver gold identity unassigned; do check_blocked_prefix audit "$prefix"; done

for role in etl trino audit; do
    denied ls "$role/warehouse/"
    denied anonymous set download "$role/warehouse"
    denied mb "$role/storage-check-forbidden-bucket"
done
# The pinned mc admin info command can exit zero after a denied request. Check
# the signed HTTP response itself rather than treating that exit code as proof.
for role in etl trino audit; do
    case "$role" in
        etl) key=$ETL_STORAGE_ACCESS_KEY; secret=$ETL_STORAGE_SECRET_KEY ;;
        trino) key=$TRINO_STORAGE_ACCESS_KEY; secret=$TRINO_STORAGE_SECRET_KEY ;;
        audit) key=$AUDIT_WRITER_STORAGE_ACCESS_KEY; secret=$AUDIT_WRITER_STORAGE_SECRET_KEY ;;
    esac
    code=$(curl --silent --show-error --output /dev/null --write-out '%{http_code}' \
        --aws-sigv4 'aws:amz:us-east-1:s3' --user "$key:$secret" \
        http://storage:9000/minio/admin/v3/info)
    [ "$code" = 403 ] || { echo "FAIL: service admin request returned HTTP $code." >&2; exit 1; }
done
echo "PASS: anonymous access denied; ETL, Trino and audit permissions verified."
