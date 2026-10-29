"""Run inside catalog-check: active backend probes without exposing credentials."""

import json
import sys
import xml.etree.ElementTree as ET

import httpx
from check import catalog
from common import CATALOG, WAREHOUSE, access_token


def anonymous_object_read():
    table = catalog("etl").load_table(("silver", "patients"))
    location = table.metadata_location
    # Credentialed read proves this exact object exists and the backend works.
    with table.io.new_input(location).open() as stream:
        size = len(stream.read())
    assert size > 0
    object_path = location.removeprefix("s3://")
    requests = []
    with httpx.Client(trust_env=False, timeout=30) as client:
        for path in (object_path, "warehouse?list-type=2&prefix=silver/"):
            response = client.get(f"http://storage:9000/{path}")
            code = None
            if response.status_code != 200:
                code = ET.fromstring(response.content).findtext("Code")
            requests.append(
                {"request": f"GET /{path}", "http_status": response.status_code, "error": code}
            )
    return {"identity": "anonymous", "positive_control_bytes": size, "requests": requests}


def catalog_direct_access():
    # ETL and Trino can read the real clinical table; ordinary SQL identities
    # have no OIDC credentials. Probe the backend anyway, bypassing networking.
    reader = catalog("trino")
    table = reader.load_table(("silver", "patients"))
    assert table.current_snapshot() is not None
    requests = []
    table_path = f"/catalog/v1/{reader.properties['prefix']}/namespaces/silver/tables/patients"
    with httpx.Client(trust_env=False, timeout=30) as client:
        control = client.get(
            f"{CATALOG}{table_path}",
            headers={"Authorization": f"Bearer {access_token('trino')}"},
        )
        assert control.status_code == 200
        for identity, token in (
            ("anonymous", None),
            ("invalid_bearer", "invalid"),
            ("audit-writer", access_token("audit-writer")),
        ):
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            for path in (
                f"/catalog/v1/config?warehouse={WAREHOUSE}",
                table_path,
            ):
                response = client.get(f"{CATALOG}{path}", headers=headers)
                requests.append(
                    {
                        "identity": identity,
                        "request": f"GET {path}",
                        "http_status": response.status_code,
                    }
                )
    return {
        "positive_control": {
            "identity": "trino",
            "request": f"GET {table_path}",
            "http_status": control.status_code,
        },
        "requests": requests,
    }


if __name__ == "__main__":
    try:
        function = {
            "anonymous_object_read": anonymous_object_read,
            "catalog_direct_access": catalog_direct_access,
        }[sys.argv[1]]
        print(json.dumps(function(), sort_keys=True))
    except Exception as error:  # noqa: BLE001 - SDK exceptions may contain credentials
        raise SystemExit(f"Backend probe failed: {type(error).__name__}") from None
