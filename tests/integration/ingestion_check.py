"""Independent saved-oracle comparison against ETL readback, never interactive grants."""

import hashlib
import json
from datetime import date, timedelta
from pathlib import Path

from ingest.pipeline import DOCTORS, etl_catalog, require


def main():
    content = Path("/evidence/care-team-oracle.json").read_bytes()
    oracle = json.loads(content)
    clinical, identity = etl_catalog(), etl_catalog("identity")
    tables = {
        name: clinical.load_table(("silver", name))
        for name in ("patients", "encounters", "claims", "care_team")
    }
    tables["identity"] = identity.load_table(("identity", "patient_identity"))
    rows = tables["care_team"].scan().to_arrow().to_pylist()
    patients = set(tables["patients"].scan().to_arrow()["patient_token"].to_pylist())
    identities = tables["identity"].scan().to_arrow()
    require(set(identities.column_names) == {"patient_token", "name"}, "Identity projection")
    require(set(identities["patient_token"].to_pylist()) == patients, "Identity patient coverage")
    require(len(identities) == len(patients), "Identity uniqueness")
    start = date.fromisoformat(oracle["valid_from"])
    end = date.fromisoformat(oracle["valid_to"]) if oracle["valid_to"] else None
    require(
        all(
            row["patient_token"] in patients
            and row["valid_from"] == start
            and row["valid_to"] == end
            for row in rows
        ),
        "Care-team validity/references",
    )
    require(
        len({(r["doctor_user"], r["patient_token"]) for r in rows}) == len(rows),
        "Care-team uniqueness",
    )
    require({row["doctor_user"] for row in rows} == set(DOCTORS), "Clinician logins")
    for doctor in DOCTORS:
        # Uses only the previously saved file, never regenerates expected memberships.
        expected = set(oracle["expected_patient_tokens"][doctor])
        actual = {row["patient_token"] for row in rows if row["doctor_user"] == doctor}
        require(actual == expected, "Saved doctor membership mismatch")
        boundaries = [(start - timedelta(days=1), set()), (start, expected)]
        if end:
            boundaries.extend([(end - timedelta(days=1), expected), (end, set())])
        for query_date, tokens in boundaries:
            active = {
                r["patient_token"]
                for r in rows
                if r["doctor_user"] == doctor
                and r["valid_from"] <= query_date
                and (r["valid_to"] is None or query_date < r["valid_to"])
            }
            require(active == tokens, "Half-open boundary mismatch")
    for name, table in tables.items():
        prefix = "identity" if name == "identity" else "silver"
        require(table.location().startswith(f"s3://warehouse/{prefix}/"), "Warehouse location")
    print(
        json.dumps(
            {
                "snapshots": {
                    name: table.current_snapshot().snapshot_id for name, table in tables.items()
                },
                "oracle_sha256": hashlib.sha256(content).hexdigest(),
                "saved_sets_verified": True,
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    # Do not let a failed SDK call echo credentials or identity rows.
    try:
        main()
    except Exception as error:  # noqa: BLE001 - sanitize SDK/identity output
        raise SystemExit(f"Ingestion check failed: {type(error).__name__}") from None
