from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RESUME = ROOT / "scripts" / "deploy" / "phase14_v2_gate_b_resume_cloudshell.sh"
RECOVERY = ROOT / "src" / "bp_engine" / "v2_research" / "label_recovery.py"


def test_resume_persists_holdout_attempt_marker_before_one_shot_evaluation() -> None:
    source = RESUME.read_text(encoding="utf-8")

    assert 'HOLDOUT_ATTEMPT="$PARTIAL_DIR/holdout-attempt.json"' in source
    assert 'test ! -e "$HOLDOUT_ATTEMPT"' in source
    assert '[[ -f "$HOLDOUT_ATTEMPT" || -f "$PARTIAL_DIR/holdout.json" ]]' in source
    assert "write_holdout_attempt_marker" in source
    assert '"holdout_touched": True' in source

    marker_write = source.index("write_holdout_attempt_marker")
    evaluation = source.index("run_research evaluate-holdout")
    assert marker_write < evaluation


def test_recovery_captures_gamma_receipt_time_after_each_response() -> None:
    source = RECOVERY.read_text(encoding="utf-8")

    request = source.index('payload = await client.get_market_by_slug(identity["slug"])')
    receipt = source.index("response_observed_at = _require_aware_utc(clock())")
    snapshot = source.index("downloaded_at=response_observed_at")
    label = source.index("generated_at=response_observed_at")

    assert request < receipt < snapshot
    assert request < receipt < label
    assert "clock: Callable[[], datetime] = _utc_now" in source
    recovery_start = source.index("async def recover_gate_b_non_holdout_labels(")
    recovery_signature = source[recovery_start:request]
    assert "observed_at: datetime" not in recovery_signature
