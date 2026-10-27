"""Create/read an Iceberg v2 table through REST; never replace existing data."""

import argparse
import json
import os

import httpx
import pyarrow as pa
from common import CATALOG, TOKEN_ENDPOINT, WAREHOUSE, access_token, run
from pyiceberg.catalog import load_catalog
from pyiceberg.exceptions import ForbiddenError, UnauthorizedError
from pyiceberg.schema import Schema
from pyiceberg.types import LongType, NestedField, StringType

ROWS = [{"id": 1, "value": "lakekeeper"}, {"id": 2, "value": "silo"}]
SCHEMA = Schema(
    NestedField(1, "id", LongType(), required=False),
    NestedField(2, "value", StringType(), required=False),
)


def catalog(service):
    prefix = service.upper().replace("-", "_")
    return load_catalog(
        WAREHOUSE,
        **{
            "type": "rest",
            "uri": f"{CATALOG}/catalog",
            "warehouse": WAREHOUSE,
            "credential": f"{service}:{os.environ[prefix + '_OIDC_CLIENT_SECRET']}",
            "oauth2-server-uri": TOKEN_ENDPOINT,
            "scope": "openid",
            "header.X-Iceberg-Access-Delegation": "",
            "s3.endpoint": "http://storage:9000",
            "s3.region": "us-east-1",
            "s3.access-key-id": os.environ[prefix + "_STORAGE_ACCESS_KEY"],
            "s3.secret-access-key": os.environ[prefix + "_STORAGE_SECRET_KEY"],
            "s3.force-virtual-addressing": "false",
            "py-io-impl": "pyiceberg.io.pyarrow.PyArrowFileIO",
        },
    )


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("create", "read"))
    parser.add_argument("--namespace", default="catalog_smoke")
    args = parser.parse_args()
    identifier = (args.namespace, "roundtrip")
    etl = catalog("etl")
    if args.mode == "create":
        etl.create_namespace_if_not_exists(args.namespace)
        if not etl.table_exists(identifier):
            table = etl.create_table(identifier, SCHEMA, properties={"format-version": "2"})
            table.append(
                pa.Table.from_pylist(
                    ROWS,
                    schema=pa.schema(
                        [
                            ("id", pa.int64()),
                            ("value", pa.string()),
                        ]
                    ),
                )
            )
    require((args.namespace,) in etl.list_namespaces(), "Namespace missing")
    # New clients must discover catalog state and fetch metadata/Parquet from Silo.
    writer_table = catalog("etl").load_table(identifier)
    reader = catalog("trino")
    table = reader.load_table(identifier)
    require(table.metadata.format_version == 2, "Expected Iceberg format v2")
    require(table.location().startswith("s3://warehouse/silver/"), "Table escaped silver prefix")
    require(
        table.metadata_location.startswith(table.location() + "/metadata/"), "Bad metadata path"
    )
    require(table.scan().to_arrow().sort_by("id").to_pylist() == ROWS, "Readback differs")
    require(
        writer_table.scan().to_arrow().sort_by("id").to_pylist() == ROWS, "ETL readback differs"
    )
    require(writer_table.current_snapshot() == table.current_snapshot(), "Snapshot differs")
    require(
        table.io.properties.get("s3.access-key-id") == os.environ["TRINO_STORAGE_ACCESS_KEY"],
        "Catalog replaced the reader's scoped S3 identity",
    )

    try:
        reader.create_namespace(f"{args.namespace}_forbidden")
    except (ForbiddenError, UnauthorizedError):
        pass
    else:
        raise RuntimeError("Trino was allowed to create a namespace")

    try:
        # A metadata-only commit tests catalog authorization without an S3 write.
        with table.transaction() as transaction:
            transaction.set_properties({"catalog-check-denied": "true"})
    except (ForbiddenError, UnauthorizedError):
        pass
    else:
        raise RuntimeError("Trino was allowed to commit table metadata")

    config_url = f"{CATALOG}/catalog/v1/config?warehouse={WAREHOUSE}"
    for token, statuses in (
        (None, (401, 403)),
        ("invalid", (401, 403)),
        (access_token("audit-writer"), (403, 404)),
    ):
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = httpx.get(config_url, headers=headers, timeout=30)
        require(response.status_code in statuses, "Unauthorized catalog access succeeded")
    print(
        "PASS: PyIceberg create/read and service isolation"
        if args.mode == "create"
        else "PASS: PyIceberg read after restart and service isolation"
    )
    print(
        json.dumps(
            {
                "namespace": args.namespace,
                "table": "roundtrip",
                "format-version": table.metadata.format_version,
                "rows": ROWS,
                "location": table.location(),
                "metadata-location": table.metadata_location,
                "snapshot-id": table.current_snapshot().snapshot_id,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    run(main)
