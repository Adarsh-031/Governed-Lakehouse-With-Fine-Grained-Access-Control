"""Keep the STRIDE analysis linked to every executable bypass inventory case."""

import csv
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
THREAT_MODEL = ROOT / "docs" / "threat-model.md"
CASE_INVENTORY = ROOT / "tests" / "bypass" / "cases.csv"
THREAT_ID = re.compile(r"\b[STRIDE]-\d{2}\b")


def _section(markdown, heading, next_heading):
    return markdown.split(heading, 1)[1].split(next_heading, 1)[0]


def test_threat_model_covers_every_stride_category():
    markdown = THREAT_MODEL.read_text(encoding="utf-8")
    stride = _section(markdown, "## 4. STRIDE analysis", "## 5. Bypass-case traceability")
    categories = set(re.findall(r"(?m)^\|\s*([STRIDE])\s*\|", stride))
    assert categories == set("STRIDE")


def test_every_bypass_inventory_case_maps_to_a_documented_threat():
    markdown = THREAT_MODEL.read_text(encoding="utf-8")
    stride = _section(markdown, "## 4. STRIDE analysis", "## 5. Bypass-case traceability")
    traceability = _section(
        markdown, "## 5. Bypass-case traceability", "## 6. Important limitations"
    )
    threat_ids = set(THREAT_ID.findall(stride))
    case_to_threats = {}
    for line in traceability.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        case_name = cells[0].strip()
        if re.fullmatch(r"[a-z_]+", case_name):
            case_to_threats[case_name] = set(THREAT_ID.findall(cells[1]))

    with CASE_INVENTORY.open(newline="", encoding="utf-8") as source:
        inventory = {row["case"] for row in csv.DictReader(source)}
    assert set(case_to_threats) == inventory
    assert all(case_to_threats.values())
    assert set.union(*case_to_threats.values()) <= threat_ids
