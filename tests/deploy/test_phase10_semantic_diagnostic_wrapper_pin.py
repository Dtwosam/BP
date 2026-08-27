from pathlib import Path


def test_phase10_semantic_diagnostic_wrapper_pins_full_rebuild_probe() -> None:
    script = Path("scripts/deploy/phase10_cloudshell_semantic_diagnose.sh").read_text(
        encoding="utf-8"
    )

    assert 'DIAG_SHA="eb6dde6956d4b72a55a303b909c9b39ec6aab812"' in script
