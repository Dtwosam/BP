from pathlib import Path


def test_phase10_semantic_diagnostic_wrapper_pins_full_rebuild_probe() -> None:
    script = Path("scripts/deploy/phase10_cloudshell_semantic_diagnose.sh").read_text(
        encoding="utf-8"
    )

    assert 'DIAG_SHA="86be59a3fcc585a2c065c05c4b201d58c73ace8e"' in script
