from pathlib import Path


def test_phase10_semantic_diagnostic_reports_full_rebuild_markers() -> None:
    script = Path("scripts/deploy/phase10_semantic_hash_diagnostic.py").read_text(
        encoding="utf-8"
    )

    assert "DIAGNOSTIC_REBUILT_DECISION_RECOVERED" in script
    assert "DIAGNOSTIC_REBUILT_SEMANTIC_HASH_RECOVERED" in script
    assert "DIAGNOSTIC_REBUILD_ERROR_ROWS" in script
