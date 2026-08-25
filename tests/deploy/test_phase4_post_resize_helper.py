from pathlib import Path

HELPER = Path("scripts/deploy/phase4_host_post_resize_accept.sh")


def test_post_resize_helper_uses_pipefail_safe_summary_selection() -> None:
    script = HELPER.read_text(encoding="utf-8")

    assert "sort -nr | head -n 1 | cut -d' ' -f2-" not in script
    assert "awk 'NR == 1" in script


def test_post_resize_helper_recovers_matching_successful_acceptance_log() -> None:
    script = HELPER.read_text(encoding="utf-8")

    assert 'grep -q "^VERDICT=PASS$" "$LOG"' in script
    assert 'grep -q "^HEAD=$EXPECTED_HEAD$" "$LOG"' in script
    assert "Existing host acceptance already passed" in script
