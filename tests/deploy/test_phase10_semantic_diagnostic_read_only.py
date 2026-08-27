from pathlib import Path


def test_phase10_semantic_diagnostic_uses_read_only_database_connection() -> None:
    script = Path("scripts/deploy/phase10_semantic_hash_diagnostic.py").read_text(
        encoding="utf-8"
    )

    assert "with engine.connect() as connection:" in script
    assert "engine.begin" not in script
    assert "from sqlalchemy import" in script
    assert " insert" not in script
    assert " update" not in script
    assert " delete" not in script
    assert "INSERT " not in script
    assert "UPDATE " not in script
    assert "DELETE " not in script
