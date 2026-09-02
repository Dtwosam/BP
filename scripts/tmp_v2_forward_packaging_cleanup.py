from pathlib import Path

for path in (
    "docs/MASTER-SOURCE-OF-TRUTH.md",
    "docs/superpowers/specs/2026-09-02-phase-14-v2-forward-coverage-collector-design.md",
):
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).rstrip() + "\n"
    file_path.write_text(normalized, encoding="utf-8")
