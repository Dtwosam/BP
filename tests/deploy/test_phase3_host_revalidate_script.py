import subprocess
from pathlib import Path


def test_phase3_host_revalidate_script_has_valid_bash_syntax() -> None:
    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "deploy"
        / "phase3_host_revalidate.sh"
    )
    result = subprocess.run(
        ["bash", "-n", str(script)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
