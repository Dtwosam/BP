import subprocess
from pathlib import Path


def _root() -> Path:
    return Path(__file__).resolve().parents[2]


def _host_script() -> Path:
    return _root() / "scripts" / "deploy" / "phase14_host_acceptance.sh"


def _cloud_helper() -> Path:
    return _root() / "scripts" / "deploy" / "phase14_cloudshell_accept.sh"


def test_phase14_host_acceptance_script_has_valid_bash_syntax() -> None:
    result = subprocess.run(
        ["bash", "-n", str(_host_script())],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


def test_phase14_candidate_build_uses_writable_exported_source() -> None:
    host = _host_script().read_text()
    helper = _cloud_helper().read_text()

    assert 'REPO="${BP_REPO:-$HOST_ROOT}"' in host
    assert 'actual_head="${BP_VERIFIED_HEAD:-}"' in host
    assert 'SRC="\\$RUNTIME_ROOT/bp-phase14-src-' in helper
    assert 'install -d -o bp -g bp "\\$RUNTIME_ROOT" "\\$SRC"' in helper
    assert 'git -C "\\$WT" archive --format=tar "\\$SHA" | sudo -u bp tar -xf - -C "\\$SRC"' in helper
    assert 'BP_REPO="\\$SRC" BP_VERIFIED_HEAD="\\$WORKTREE_HEAD"' in helper
