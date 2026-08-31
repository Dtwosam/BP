from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECISIONS = ROOT / "docs" / "DECISION-LOG.md"
BASE = "de907d324c7ee4ec46e2dfef1eb516dbb3fa8348"
MARKER = "## D-031 — Negative prospective profitability remains a fail; research daemons may continue collecting evidence without promotion"

current = DECISIONS.read_text(encoding="utf-8")
if current.count(MARKER) != 1:
    raise SystemExit("expected exactly one D-031 section in current decision log")
new_section = MARKER + current.split(MARKER, 1)[1]

base = subprocess.check_output(
    ["git", "show", f"{BASE}:docs/DECISION-LOG.md"],
    text=True,
)
if MARKER in base:
    raise SystemExit("D-031 unexpectedly exists in restoration base")

DECISIONS.write_text(base.rstrip() + "\n\n" + new_section.rstrip() + "\n", encoding="utf-8")
