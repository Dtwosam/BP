from pathlib import Path


def test_phase10_semantic_diagnostic_uses_isolated_candidate_venv() -> None:
    script = Path("scripts/deploy/phase10_cloudshell_semantic_diagnose.sh").read_text(
        encoding="utf-8"
    )

    assert 'VENV="/var/tmp/bp-phase10-diag-venv-' in script
    assert 'STAGE="/var/tmp/bp-phase10-diag-src-' in script
    assert 'install -d -o bp -g bp "\\$STAGE"' in script
    assert 'git -C "\\$WT" archive "\\$SHA" | sudo -u bp tar -x -C "\\$STAGE"' in script
    assert 'python -m venv "\\$VENV"' in script
    assert '"\\$VENV/bin/python" -m pip install --disable-pip-version-check "\\$STAGE"' in script
    assert 'PYTHONPATH="\\$STAGE/src"' in script
    assert '"\\$VENV/bin/python" "\\$DIAG_PY"' in script
    assert '/opt/bp/.venv/bin/python "\\$DIAG_PY"' not in script
    assert 'pip install --disable-pip-version-check "\\$WT"' not in script
