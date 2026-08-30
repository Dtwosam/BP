import subprocess
from pathlib import Path


def _script() -> Path:
    return (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "deploy"
        / "phase14_host_acceptance.sh"
    )


def test_phase14_host_acceptance_script_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(_script())],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_phase14_candidate_build_uses_writable_staged_source() -> None:
    script = _script().read_text()
    assert 'CANDIDATE_SRC="$RUNTIME_ROOT/candidate-src-' in script
    assert 'cp -a "$REPO/." "$CANDIDATE_SRC/"' in script
    assert 'chown -R bp:bp "$CANDIDATE_SRC"' in script
    assert 'pip install --disable-pip-version-check --no-cache-dir "$CANDIDATE_SRC"' in script
