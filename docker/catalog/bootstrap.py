"""Rerunnable authenticated server, warehouse, and service-grant bootstrap."""

import os

from common import PROJECT, WAREHOUSE, access_token, management, run

PROFILE = {
    "type": "s3",
    "bucket": "warehouse",
    "key-prefix": "silver",
    # Lakekeeper serializes an endpoint's empty path as '/'. Compare canonical form.
    "endpoint": "http://storage:9000/",
    "region": "us-east-1",
    "path-style-access": True,
    "flavor": "s3-compat",
    # Clients keep their own scoped S3 credentials; do not delegate the ETL key.
    "sts-enabled": False,
    "remote-signing-enabled": False,
}


def main():
    admin = access_token("catalog-bootstrap")
    info = management("GET", "info", admin)
    if not info["bootstrapped"]:
        management("POST", "bootstrap", admin, json={"accept-terms-of-use": True}, expected=(204,))
    for warehouse_name, prefix, trino_relations in (
        (WAREHOUSE, "silver", {"describe", "select"}),
        ("identity", "identity", set()),
    ):
        provision_warehouse(admin, warehouse_name, prefix, trino_relations)
    # The audit table is written only by the audit-writer service, directly through
    # PyIceberg (never Trino, so writes cannot generate recursive query events).
    # Lakekeeper writes table metadata with the audit-writer storage key, which can
    # add objects under audit/ but cannot delete them (append-only storage policy).
    provision_warehouse(
        admin,
        AUDIT_WAREHOUSE,
        "audit",
        set(),
        grants=AUDIT_GRANTS,
        storage_key="AUDIT_WRITER",
    )
    print("PASS: authenticated bootstrap; hospital at silver/, restricted identity at identity/")
    print("PASS: identity ETL only; clinical Trino read; credential vending disabled")
    print("PASS: audit warehouse at audit/; audit-writer writes; Trino read for admin only")


AUDIT_WAREHOUSE = "audit"
AUDIT_GRANTS = {
    "etl": set(),
    # Read-only Trino catalog; rules.json lets only the admin group select from it.
    "trino": {"describe", "select"},
    "audit-writer": {"describe", "select", "create", "modify"},
}


def provision_warehouse(
    admin, warehouse_name, prefix, trino_relations, *, grants=None, storage_key="ETL"
):
    if grants is None:
        grants = {
            "etl": {"describe", "select", "create", "modify"},
            "trino": trino_relations,
            "audit-writer": set(),
        }
    profile = {**PROFILE, "key-prefix": prefix}
    warehouses = management("GET", "warehouse", admin)["warehouses"]
    warehouse = next((w for w in warehouses if w["name"] == warehouse_name), None)
    if warehouse is None:
        warehouse = management(
            "POST",
            "warehouse",
            admin,
            expected=(201,),
            json={
                "warehouse-name": warehouse_name,
                "project-id": PROJECT,
                "storage-profile": profile,
                "storage-credential": {
                    "type": "s3",
                    "credential-type": "access-key",
                    "access-key-id": os.environ[f"{storage_key}_STORAGE_ACCESS_KEY"],
                    "secret-access-key": os.environ[f"{storage_key}_STORAGE_SECRET_KEY"],
                },
            },
        )
    else:
        actual = warehouse["storage-profile"]
        if any(actual.get(key) != value for key, value in profile.items()):
            raise RuntimeError("Existing warehouse has a different storage profile")
    warehouse_id = warehouse["warehouse-id"]
    path = f"permissions/warehouse/{warehouse_id}/assignments"
    assignments = management("GET", path, admin)["assignments"]
    for service, relations in grants.items():
        # Self-provisioning gets the verified OIDC subject from Lakekeeper itself.
        user = management(
            "POST",
            "user",
            access_token(service),
            json={"update-if-exists": True},
            expected=(200, 201),
        )["id"]
        current = [assignment for assignment in assignments if assignment.get("user") == user]
        desired = [{"type": relation, "user": user} for relation in sorted(relations)]
        writes = [a for a in desired if a not in current]
        deletes = [a for a in current if a not in desired]
        if writes or deletes:
            management(
                "POST", path, admin, json={"writes": writes, "deletes": deletes}, expected=(204,)
            )


if __name__ == "__main__":
    run(main)
