"""Draw docs/architecture.png: the deployed system as built.

    uv run --locked --with matplotlib python docs/architecture.py
"""

from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

OUT = Path(__file__).with_name("architecture.png")
fig, ax = plt.subplots(figsize=(18, 9.2))
ax.set_xlim(0, 180)
ax.set_ylim(14, 105)
ax.axis("off")

COLORS = {
    "src": ("#eaf2fb", "#2b5c8a"),
    "etl": ("#eef7ee", "#2e6b34"),
    "store": ("#eef7ee", "#2e6b34"),
    "query": ("#f1ecf9", "#55407f"),
    "user": ("#fbeeee", "#8a2b2b"),
    "audit": ("#f2f2f2", "#444444"),
    "test": ("#fdf6e0", "#7a5d00"),
}


def zone(x, y, w, h, title, kind):
    face, edge = COLORS[kind]
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.4,rounding_size=1.5",
            fc=face, ec="#bbbbbb", lw=1.2, ls="--", alpha=0.6,
        )
    )
    ax.text(x + 1, y + h - 1.2, title, fontsize=11.5, fontweight="bold", va="top", color=edge)


def box(x, y, w, h, title, body, kind):
    face, edge = COLORS[kind]
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h, boxstyle="round,pad=0.3,rounding_size=1",
            fc="white", ec=edge, lw=1.6,
        )
    )
    ax.text(x + w / 2, y + h - 1.4, title, ha="center", va="top", fontsize=10.5, fontweight="bold")
    ax.text(x + w / 2, y + h - 4.6, body, ha="center", va="top", fontsize=8.6, linespacing=1.35)


def arrow(p, q, label="", color="#333333", style="-|>", ls="-", lx=0, ly=0):
    ax.add_patch(
        FancyArrowPatch(p, q, arrowstyle=style, mutation_scale=14, color=color, lw=1.6, ls=ls)
    )
    if label:
        mx, my = (p[0] + q[0]) / 2 + lx, (p[1] + q[1]) / 2 + ly
        ax.text(mx, my, label, fontsize=8.3, ha="center", va="center", color=color,
                bbox=dict(fc="white", ec="none", pad=0.6))


ax.text(90, 103, "Governed Hospital Lakehouse: architecture as built", ha="center",
        fontsize=16, fontweight="bold")
ax.text(90, 99.6, "BDSRAE Level 3, Project 5  ·  one Docker Compose stack  ·  synthetic data only",
        ha="center", fontsize=10, color="#555555")

# Zones
zone(1, 52, 26, 44, "DATA SOURCE", "src")
zone(30, 52, 52, 44, "INGESTION AND STORAGE", "etl")
zone(85, 52, 44, 44, "QUERY AND POLICY", "query")
zone(132, 18, 47, 78, "USERS (own password, HTTPS)", "user")
zone(30, 16, 99, 32, "AUDIT TRAIL", "audit")
zone(1, 16, 26, 32, "EVIDENCE", "test")

# Source
box(3, 70, 22, 20, "Synthea 3.4.0",
    "synthetic patients,\nencounters, claims\nseed 42, Massachusetts\n1k and 10k patients\n(no real patient data)", "src")

# ETL + storage
box(32, 70, 22, 20, "Python ETL (uv)",
    "pandas + PyArrow\n+ PyIceberg\nHMAC patient tokens\nage bands, care teams", "etl")
box(57, 70, 23, 20, "Iceberg tables",
    "silver: patients,\nencounters, claims,\ncare_team\ngold: researcher\naggregates (cells >= 11)", "etl")
box(32, 54, 22, 13, "Restricted identity",
    "names, separate\nwarehouse, ETL only", "etl")
box(57, 54, 23, 13, "Storage + catalog",
    "Silo (S3) private store\nLakekeeper REST catalog\n(+ OpenFGA)", "store")

# Query layer
box(87, 66, 40, 24, "Trino 476",
    "single SQL entry point, password login over TLS\nfile-based policy: policies/rules.json\n"
    "· row filter: doctor sees own care team\n· column masks / hidden columns\n· deny by default, read-only, no DDL",
    "query")
box(87, 54, 31, 9.5, "Policy as code",
    "access-matrix.csv -> scripts/policy.py\n-> rules.json + version digest", "query")

# Users
box(134, 74, 43, 18, "Doctor (alice, bob)",
    "patients + encounters of own\nactive care-team only (600 each)\nclaims and identity denied", "user")
box(134, 54, 43, 17, "Researcher",
    "gold aggregates only, cells >= 11\nrow-level: token + month-level\ntimes; demographics denied", "user")
box(134, 34, 43, 17, "Billing",
    "claims with approved fields,\ntokenized patient id\ndemographics denied", "user")
box(134, 21, 43, 10, "Admin (read-only)",
    "read clinical + audit; identity denied", "user")

# Audit
box(88, 19, 39, 17, "Trino HTTP event listener",
    "QueryCreated + QueryCompleted\nfor every query, incl. denials\n(SQL text redacted)", "audit")
box(60, 19, 25, 17, "Audit collector",
    "FastAPI, token auth\nfsync to spool\nbefore 200 OK", "audit")
box(32, 19, 25, 17, "Audit writer -> Iceberg",
    "dedupe on (query, event)\nappend-only table\naudit.audit.query_events\nadmin-only", "audit")

# Evidence
box(3, 18, 22, 26, "Tests + CI",
    "unit 836\naccess matrix 585\nbypass 12/12 blocked\nlinkage 75.2% -> 0%\naudit outage 8/8\nbenchmark 1k + 10k:\n0 unauthorized rows\noverhead in noise", "test")

# Arrows
arrow((25, 78), (32, 80), "CSV")
arrow((54, 80), (57, 80))
arrow((43, 70), (43, 67))
arrow((68.5, 70), (68.5, 67))
arrow((87, 72), (80, 72), "reads Iceberg", lx=0, ly=2.2)
arrow((102.5, 63.5), (102.5, 66), color="#55407f")
for y in (83, 62.5, 42.5, 25.5):
    arrow((134, y), (127, 78), color="#8a2b2b", style="<|-|>")
arrow((122.5, 66), (122.5, 36), "event JSON", color="#444444", lx=0)
arrow((88, 27.5), (85, 27.5))
arrow((60, 27.5), (57, 27.5))
arrow((44.5, 36), (63, 54), "Iceberg append", color="#444444", lx=8, ly=-2)

fig.savefig(OUT, dpi=140, bbox_inches="tight", facecolor="white")
print(f"wrote {OUT}")
