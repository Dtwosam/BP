from pathlib import Path

RESUME = Path("scripts/deploy/phase14_v2_gate_b_resume_cloudshell.sh")
RECOVERY = Path("src/bp_engine/v2_research/label_recovery.py")
CLI = Path("src/bp_engine/v2_research/label_recovery_cli.py")
TESTS = Path("tests/v2_research/test_gate_b_label_recovery.py")


def rep(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one anchor, found {count}")
    return text.replace(old, new, 1)


# P1: persist a durable one-shot holdout-attempt marker before any final-holdout read.
s = RESUME.read_text(encoding="utf-8")
s = rep(
    s,
    'PLAN_SHA256=${PHASE14_V2_GATE_B_RESUME_PLAN_SHA256:?}\nARCHIVE=',
    'PLAN_SHA256=${PHASE14_V2_GATE_B_RESUME_PLAN_SHA256:?}\nHOLDOUT_ATTEMPT="$PARTIAL_DIR/holdout-attempt.json"\nARCHIVE=',
    "resume marker path",
)
s = rep(
    s,
    '  if [[ -f "$PARTIAL_DIR/holdout.json" ]]; then\n    echo "HOLDOUT_TOUCHED=true" >&2',
    '  if [[ -f "$HOLDOUT_ATTEMPT" || -f "$PARTIAL_DIR/holdout.json" ]]; then\n    echo "HOLDOUT_TOUCHED=true" >&2\n    [[ ! -f "$HOLDOUT_ATTEMPT" ]] || echo "HOLDOUT_ATTEMPT_FILE=$HOLDOUT_ATTEMPT" >&2',
    "resume fail marker reporting",
)
s = rep(
    s,
    'test ! -e "$PARTIAL_DIR/summary.json" || fail "summary_already_present"\n[[ "$(sha256sum "$PARTIAL_DIR/plan.json"',
    'test ! -e "$PARTIAL_DIR/summary.json" || fail "summary_already_present"\ntest ! -e "$HOLDOUT_ATTEMPT" || fail "holdout_attempt_already_present"\n[[ "$(sha256sum "$PARTIAL_DIR/plan.json"',
    "resume preexisting marker guard",
)
marker_fn = r'''
write_holdout_attempt_marker() {
  sudo -u bp "$REPO/.venv/bin/python" - "$HOLDOUT_ATTEMPT" "$HELPER_HEAD" "$PLAN_SHA256" <<'PYMARKER'
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

path = Path(sys.argv[1])
payload = {
    "stage": "final_holdout_evaluation_attempted",
    "recorded_at": datetime.now(UTC).isoformat(),
    "helper_head": sys.argv[2],
    "frozen_plan_file_sha256": sys.argv[3],
    "holdout_touched": True,
    "evaluation_result_known": False,
}
with path.open("x", encoding="utf-8") as handle:
    handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    handle.flush()
    os.fsync(handle.fileno())
dir_fd = os.open(path.parent, os.O_RDONLY)
try:
    os.fsync(dir_fd)
finally:
    os.close(dir_fd)
PYMARKER
  chmod 0640 "$HOLDOUT_ATTEMPT"
}
'''
s = rep(
    s,
    'run_research() {\n  sudo -u bp env \\\n    MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 \\\n    PYTHONPATH="$RUNTIME_ROOT/src" \\\n    "$REPO/.venv/bin/python" "$RUNTIME_ROOT/scripts/run_v2_gate_b_research.py" \\\n    --env-file "$ENV_FILE" "$@"\n}\n\nrun_label_audit',
    'run_research() {\n  sudo -u bp env \\\n    MODE=research LIVE_TRADING_ENABLED=false MAX_TRADE_SIZE_USD=0 MAX_DAILY_LOSS_USD=0 \\\n    PYTHONPATH="$RUNTIME_ROOT/src" \\\n    "$REPO/.venv/bin/python" "$RUNTIME_ROOT/scripts/run_v2_gate_b_research.py" \\\n    --env-file "$ENV_FILE" "$@"\n}\n' + marker_fn + '\nrun_label_audit',
    "resume marker function",
)
s = rep(
    s,
    'test ! -e "$HOLDOUT" || fail "holdout_preexists_before_evaluation"\nrun_research evaluate-holdout',
    'test ! -e "$HOLDOUT" || fail "holdout_preexists_before_evaluation"\nwrite_holdout_attempt_marker || fail "holdout_attempt_marker_write_failed"\nrun_research evaluate-holdout',
    "resume marker before evaluation",
)
s = rep(
    s,
    '"$REPO/.venv/bin/python" - "$PLAN" "$SELECTION" "$HOLDOUT" "$SUMMARY" "$HELPER_HEAD" "$DEPLOYED_HEAD" "$PLAN_SHA256" <<\'PY\'\n',
    '"$REPO/.venv/bin/python" - "$PLAN" "$SELECTION" "$HOLDOUT" "$HOLDOUT_ATTEMPT" "$SUMMARY" "$HELPER_HEAD" "$DEPLOYED_HEAD" "$PLAN_SHA256" <<\'PY\'\n',
    "resume summary marker arg",
)
s = rep(
    s,
    'plan_path, selection_path, holdout_path, output_path, helper_head, deployed_head, frozen_plan_file_sha = sys.argv[1:]\n',
    'plan_path, selection_path, holdout_path, holdout_attempt_path, output_path, helper_head, deployed_head, frozen_plan_file_sha = sys.argv[1:]\n',
    "resume summary marker unpack",
)
s = rep(
    s,
    '    "production_database_mutated_by_resume": False,\n    "plan": {',
    '    "production_database_mutated_by_resume": False,\n    "holdout_attempt": {\n        "artifact": holdout_attempt_path,\n        "sha256": sha(holdout_attempt_path),\n        "holdout_touched": True,\n    },\n    "plan": {',
    "resume summary marker evidence",
)
s = rep(
    s,
    'echo "HOLDOUT_FILE=$HOLDOUT"\necho "SUMMARY_FILE=$SUMMARY"',
    'echo "HOLDOUT_ATTEMPT_FILE=$HOLDOUT_ATTEMPT"\necho "HOLDOUT_FILE=$HOLDOUT"\necho "SUMMARY_FILE=$SUMMARY"',
    "resume success marker echo",
)
RESUME.write_text(s, encoding="utf-8")

# P2: stamp each Gamma response at actual receipt time, not at recovery start.
r = RECOVERY.read_text(encoding="utf-8")
r = rep(r, "from collections.abc import Mapping", "from collections.abc import Callable, Mapping", "callable import")
r = rep(
    r,
    'class GateBLabelRecoveryIntegrityError(RuntimeError):\n    """Raised when frozen Gate B non-holdout label recovery cannot stay leakage-safe."""\n\n\ndef _require_aware_utc',
    'class GateBLabelRecoveryIntegrityError(RuntimeError):\n    """Raised when frozen Gate B non-holdout label recovery cannot stay leakage-safe."""\n\n\ndef _utc_now() -> datetime:\n    return datetime.now(UTC)\n\n\ndef _require_aware_utc',
    "utc clock helper",
)
r = rep(
    r,
    '    plan: dict[str, Any],\n    observed_at: datetime,\n) -> dict[str, Any]:\n    """Append missing canonical labels for frozen non-holdout IDs only."""\n    observed_at = _require_aware_utc(observed_at)\n    repository = HistoricalRepository()',
    '    plan: dict[str, Any],\n    clock: Callable[[], datetime] = _utc_now,\n) -> dict[str, Any]:\n    """Append missing canonical labels for frozen non-holdout IDs only."""\n    repository = HistoricalRepository()',
    "recovery clock signature",
)
r = rep(
    r,
    '        payload = await client.get_market_by_slug(identity["slug"])\n        if payload is None:',
    '        payload = await client.get_market_by_slug(identity["slug"])\n        response_observed_at = _require_aware_utc(clock())\n        if payload is None:',
    "receipt timestamp after response",
)
r = r.replace("downloaded_at=observed_at", "downloaded_at=response_observed_at")
r = r.replace("generated_at=observed_at", "generated_at=response_observed_at")
RECOVERY.write_text(r, encoding="utf-8")

c = CLI.read_text(encoding="utf-8")
c = rep(c, "from datetime import UTC, datetime\n", "", "remove early timestamp import")
c = rep(
    c,
    '        GammaClient(),\n        plan=plan,\n        observed_at=datetime.now(UTC),\n',
    '        GammaClient(),\n        plan=plan,\n',
    "cli recovery timestamp",
)
CLI.write_text(c, encoding="utf-8")

# Adapt existing recovery tests to the clock API; the first closure proves the clock
# is consulted only after the Gamma response has been received.
t = TESTS.read_text(encoding="utf-8")
t = rep(
    t,
    '    observed_at = STARTS["holdout"] + timedelta(hours=1)\n\n    report = await recover_gate_b_non_holdout_labels(\n        engine,\n        client,\n        plan=_plan(),\n        observed_at=observed_at,\n    )',
    '    observed_at = STARTS["holdout"] + timedelta(hours=1)\n\n    def clock() -> datetime:\n        assert client.calls == [missing_slug]\n        return observed_at\n\n    report = await recover_gate_b_non_holdout_labels(\n        engine,\n        client,\n        plan=_plan(),\n        clock=clock,\n    )',
    "first recovery clock test",
)
t = t.replace(
    'observed_at=STARTS["holdout"] + timedelta(hours=1),',
    'clock=lambda: STARTS["holdout"] + timedelta(hours=1),',
)
t = rep(
    t,
    '        observed_at=observed_at,\n    )\n    second = await recover_gate_b_non_holdout_labels(',
    '        clock=lambda: observed_at,\n    )\n    second = await recover_gate_b_non_holdout_labels(',
    "idempotent first clock",
)
t = rep(
    t,
    '        observed_at=observed_at + timedelta(minutes=1),\n    )',
    '        clock=lambda: observed_at + timedelta(minutes=1),\n    )',
    "idempotent second clock",
)
TESTS.write_text(t, encoding="utf-8")
