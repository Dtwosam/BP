from pathlib import Path

HELPER = Path("scripts/deploy/phase4_cloudshell_expand_and_accept.sh")


def test_phase4_cloudshell_helper_uses_gce_safe_lowercase_snapshot_timestamp() -> None:
    script = HELPER.read_text(encoding="utf-8")

    assert "$(date -u +%Y%m%dt%H%M%Sz)" in script
    assert "$(date -u +%Y%m%dT%H%M%SZ)" not in script
