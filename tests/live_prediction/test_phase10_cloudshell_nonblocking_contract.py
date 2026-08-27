from pathlib import Path


ROOT = Path(__file__).parents[2]
CLOUD = ROOT / "scripts" / "deploy" / "phase10_cloudshell_accept.sh"


def test_phase10_cloudshell_launches_acceptance_unit_nonblocking() -> None:
    text = CLOUD.read_text(encoding="utf-8")

    assert "systemd-run" in text
    assert "--no-block" in text
    assert 'systemctl show "\\$UNIT_NAME"' in text
