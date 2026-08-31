from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
DECISIONS = ROOT / "docs" / "DECISION-LOG.md"


def git_show(path: str) -> str:
    return subprocess.check_output(
        ["git", "show", f"origin/main:{path}"],
        cwd=ROOT,
        text=True,
    )


def extract_section(text: str, start: str, end: str | None = None) -> str:
    start_index = text.index(start)
    if end is None:
        return text[start_index:].strip()
    end_index = text.index(end, start_index)
    return text[start_index:end_index].strip()


def restore_changelog() -> None:
    current = CHANGELOG.read_text()
    baseline = git_show("docs/CHANGELOG.md")

    section = extract_section(
        current,
        "## 0.14.2 — 31 August 2026",
        "\n## 0.14.1 — 30–31 August 2026",
    )
    if "## 0.14.2 — 31 August 2026" in baseline:
        raise RuntimeError("main baseline unexpectedly already contains 0.14.2")
    if not baseline.startswith("# Changelog\n\n"):
        raise RuntimeError("unexpected main changelog header")

    historical = baseline.removeprefix("# Changelog\n\n")
    restored = f"# Changelog\n\n{section}\n\n{historical.lstrip()}"
    if restored.count("## 0.14.2 — 31 August 2026") != 1:
        raise RuntimeError("restored changelog must contain exactly one 0.14.2 entry")
    CHANGELOG.write_text(restored)


def restore_decisions() -> None:
    current = DECISIONS.read_text()
    baseline = git_show("docs/DECISION-LOG.md")

    section = extract_section(
        current,
        "## D-029 — Prospective official outcomes reuse the canonical Gamma snapshot-to-label-to-evaluation chain",
    )
    if "## D-029 —" in baseline:
        raise RuntimeError("main baseline unexpectedly already contains D-029")

    restored = f"{baseline.rstrip()}\n\n{section}\n"
    if restored.count("## D-029 —") != 1:
        raise RuntimeError("restored decision log must contain exactly one D-029 entry")
    DECISIONS.write_text(restored)


def main() -> None:
    restore_changelog()
    restore_decisions()


if __name__ == "__main__":
    main()
