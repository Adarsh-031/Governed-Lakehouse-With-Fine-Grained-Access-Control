"""Catalog and object-level denials against the populated restricted identity table."""

import os
from urllib.parse import urlparse

from check import catalog, require
from common import access_token, request, run
from pyarrow import fs


def main():
    table = catalog("etl", warehouse="identity").load_table(("identity", "patient_identity"))
    require(table.scan().to_arrow().num_rows > 0, "Identity table must be populated")
    endpoint = "http://catalog:8181/catalog/v1/config?warehouse=identity"
    for service in ("trino", "audit-writer"):
        request("GET", endpoint, token=access_token(service), expected=(403, 404))
    request("GET", endpoint, expected=(401,))
    request("GET", endpoint, token="invalid", expected=(401,))
    reader = fs.S3FileSystem(
        access_key=os.environ["TRINO_STORAGE_ACCESS_KEY"],
        secret_key=os.environ["TRINO_STORAGE_SECRET_KEY"],
        endpoint_override="storage:9000",
        scheme="http",
        region="us-east-1",
    )
    data_file = next(iter(table.scan().plan_files())).file.file_path
    for location in (table.metadata_location, data_file):
        parsed = urlparse(location)
        try:
            with reader.open_input_file(parsed.netloc + parsed.path):
                pass
        except OSError:
            continue
        raise RuntimeError("Trino S3 reader accessed restricted identity")
    print("PASS: populated identity catalog denied to Trino/audit/anonymous; Trino S3 reads denied")


if __name__ == "__main__":
    run(main)
