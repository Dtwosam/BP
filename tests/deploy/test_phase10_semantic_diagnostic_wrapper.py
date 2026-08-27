from pathlib import Path


def test_phase10_semantic_diagnostic_uses_isolated_candidate_venv() -> None:
    script = Path("scripts/deploy/phase10_cloudshell_semantic_diagnose.sh").read_text(
        encoding="utf-8"
    )

    assert 'VENV="/var/tmp/bp-phase10-diag-venv-' in script
    assert 'python -m venv "\\$VENV"' in script
    assert '"\\$VENV/bin/python" -m pip install' in script
    assert '"\\$VENV/bin/python" "\\$DIAG_PY"' in script
    assert '/opt/bp/.venv/bin/python "\\$DIAG_PY"' not in script
