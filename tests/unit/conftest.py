"""Shared clinical and restricted ingestion source fixtures."""

import pandas as pd
import pytest

from ingest import pipeline as etl


@pytest.fixture
def frames():
    rows = {
        "patients": [
            ["p1", "1980-02-29", "F", "NA", "test", "02139", "Test", None, "Patient"],
            ["p2", None, "M", None, None, "invalid", None, None, None],
        ],
        "encounters": [
            [
                "e1",
                "p1",
                "2024-01-01T01:00:00.123456+01:00",
                None,
                "ambulatory",
                "00123",
                "visit",
                None,
                None,
            ],
            ["e2", "p2", None, None, None, None, None, None, None],
        ],
        "claims": [["c1", "p1", "e1", "payer1", "payer2"]],
        "claims_transactions": [
            ["t1", "c1", "0", "p1", "e1", "CHARGE", "100.01", None, "00456", "procedure", "0.00"],
            ["t2", "c1", "1", "p1", "e1", "PAYMENT", None, "COPAY", "00456", "procedure", "10.00"],
            ["t3", "c1", "2", "p1", "e1", "PAYMENT", None, "ECHECK", "00456", "procedure", "60.00"],
            ["t4", "c1", "3", "p1", "e1", "TRANSFEROUT", None, None, "00456", "procedure", "0.00"],
            [
                "t5",
                "c1",
                "4",
                "p1",
                "e1",
                "TRANSFERIN",
                "30.01",
                None,
                "00456",
                "procedure",
                "0.00",
            ],
            ["t6", "c1", "5", "p1", "e1", "PAYMENT", None, "ECHECK", "00456", "procedure", "20.00"],
            ["t7", "c1", "6", "p1", "e1", "PAYMENT", None, "CASH", "00456", "procedure", "10.01"],
            ["t8", "c1", "10", "p1", "e1", "CHARGE", "2.99", None, "00789", "second", "0.00"],
            ["t9", "c1", "11", "p1", "e1", "PAYMENT", None, "CC", "00789", "second", "2.99"],
        ],
        "payers": [["payer1", "Primary"], ["payer2", "Secondary"]],
    }
    return {
        name: pd.DataFrame(value, columns=etl.SOURCE_COLUMNS[name].split(), dtype="string")
        for name, value in rows.items()
    }
