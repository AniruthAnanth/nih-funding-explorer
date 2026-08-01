"""Canonical filesystem locations. Everything downstream imports from here so
no module hard-codes a path."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

CONFIG = ROOT / "config" / "config.yaml"
REFERENCE = ROOT / "reference"

DATA = ROOT / "data"
RAW = DATA / "raw"
INTERIM = DATA / "interim"
PROCESSED = DATA / "processed"

OUTPUTS = ROOT / "outputs"
TABLES = OUTPUTS / "tables"
FIGURES = OUTPUTS / "figures"

LOGS = ROOT / "logs"
MANIFEST = RAW / "manifest.json"
WORKFLOW_MANIFEST = OUTPUTS / "workflow_manifest.json"

for _d in (RAW, INTERIM, PROCESSED, TABLES, FIGURES, LOGS):
    _d.mkdir(parents=True, exist_ok=True)
