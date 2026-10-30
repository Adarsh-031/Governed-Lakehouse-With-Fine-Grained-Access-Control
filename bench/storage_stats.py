"""Print stored rows, bytes, files and snapshot per silver table (runs in the ingest image)."""

import json
import warnings

from ingest.pipeline import etl_catalog

TABLES = ("patients", "encounters", "claims", "care_team")


def main():
    warnings.filterwarnings("ignore")
    catalog = etl_catalog()
    stats = {}
    for name in TABLES:
        table = catalog.load_table(("silver", name))
        files = [task.file for task in table.scan().plan_files()]
        snapshot = table.current_snapshot()
        stats[name] = {
            "table_rows": sum(f.record_count for f in files),
            "stored_bytes": sum(f.file_size_in_bytes for f in files),
            "data_files": len(files),
            "snapshot_id": snapshot.snapshot_id if snapshot else None,
        }
    print(json.dumps(stats, sort_keys=True))


if __name__ == "__main__":
    main()
