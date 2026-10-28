"""Test-only identity warehouse provisioning; mounted into isolated catalog-init."""

import os

from bootstrap import PROFILE
from common import PROJECT, access_token, management, request, run


def main():
    admin = access_token("catalog-bootstrap")
    warehouse = management(
        "POST",
        "warehouse",
        admin,
        expected=(201,),
        json={
            "warehouse-name": "identity",
            "project-id": PROJECT,
            "storage-profile": {**PROFILE, "key-prefix": "identity"},
            "storage-credential": {
                "type": "s3",
                "credential-type": "access-key",
                "access-key-id": os.environ["ETL_STORAGE_ACCESS_KEY"],
                "secret-access-key": os.environ["ETL_STORAGE_SECRET_KEY"],
            },
        },
    )
    etl = management("POST", "user", access_token("etl"), json={"update-if-exists": True})["id"]
    management(
        "POST",
        f"permissions/warehouse/{warehouse['warehouse-id']}/assignments",
        admin,
        json={
            "writes": [
                {"type": relation, "user": etl}
                for relation in ("describe", "select", "create", "modify")
            ],
            "deletes": [],
        },
        expected=(204,),
    )
    # The normal Trino service still has no identity catalog grant.
    request(
        "GET",
        "http://catalog:8181/catalog/v1/config?warehouse=identity",
        token=access_token("trino"),
        expected=(403, 404),
    )
    print("PASS: isolated identity warehouse grants ETL only; normal Trino denied")


if __name__ == "__main__":
    run(main)
