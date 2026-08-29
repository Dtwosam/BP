#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
HOST_ROOT=/opt/bp
REPO="${BP_REPO:-$HOST_ROOT}"
ENV_FILE="${BP_ENV_FILE:-/etc/bp/bp.env}"
HOST_PY="$HOST_ROOT/.venv/bin/python"
EVIDENCE_ROOT=/var/lib/bp/evidence/phase13-improvement-loop
RUNTIME_ROOT=/var/lib/bp/phase13-runtime
EVALUATION_TIMEOUT_SECONDS="${PHASE13_EVALUATION_TIMEOUT_SECONDS:-900}"
VENV="$RUNTIME_ROOT/bp-phase13-venv-${EXPECTED_HEAD:0:12}-$$"

ACCEPTED_PHASE9_5M_RUN_ID="phase9-300-c9f0e00eb7836af08008c66909f8f179"
ACCEPTED_PHASE9_5M_SHA="c9f0e00eb7836af08008c66909f8f179f03089413426508469353c75bcbcae24"
ACCEPTED_PHASE8_5M_RUN_ID="phase8-300-efdf493067e9d56419afc4d88452bec6"
ACCEPTED_PHASE8_5M_SHA="efdf493067e9d56419afc4d88452bec6effb871482664d19f109b3bbe4dd1d93"
ACCEPTED_PHASE7_5M_RUN_ID="phase7-300-0a822e17ceced11742bf6d3bc8214f44"
ACCEPTED_PHASE7_5M_SHA="0a822e17ceced11742bf6d3bc8214f44f4755c7bc23bb1d3f2dcfa897f3edcc0"

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi
if [[ ${EUID} -ne 0 ]]; then
  echo "Phase 13 host acceptance must run as root" >&2
  exit 2
fi
if ! [[ "$EVALUATION_TIMEOUT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "PHASE13_EVALUATION_TIMEOUT_SECONDS must be a positive integer" >&2
  exit 2
fi

actual_head="${BP_VERIFIED_HEAD:-}"
if [[ -d "$REPO/.git" ]]; then
  actual_head=$(git -C "$REPO" rev-parse HEAD)
fi
if [[ -z "$actual_head" || "$actual_head" != "$EXPECTED_HEAD" ]]; then
  echo "PHASE13_HOST_ACCEPTANCE=FAIL" >&2
  echo "REASON=candidate_provenance_mismatch" >&2
  echo "EXPECTED_HEAD=$EXPECTED_HEAD" >&2
  echo "ACTUAL_HEAD=${actual_head:-missing}" >&2
  exit 2
fi

required_files=(
  "$REPO/src/bp_engine/improvement/challenger.py"
  "$REPO/src/bp_engine/improvement/service.py"
  "$REPO/src/bp_engine/improvement/source.py"
  "$REPO/src/bp_engine/improvement/repository.py"
  "$REPO/src/bp_engine/storage/improvement_schema.py"
  "$REPO/migrations/0013_improvement_loop.sql"
  "$REPO/scripts/run_improvement.py"
)
for path in "${required_files[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "missing Phase 13 candidate file: $path" >&2
    exit 2
  fi
done
if [[ ! -f "$ENV_FILE" || ! -x "$HOST_PY" ]]; then
  echo "missing production environment or host Python" >&2
  exit 2
fi

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

MODE=$(read_env MODE)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$MODE" != "research" || "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  echo "Phase 13 requires research mode, live disabled, and zero real-money limits" >&2
  exit 3
fi
if ! grep -q '^DATABASE_URL=.' "$ENV_FILE"; then
  echo "database connection setting is missing from the configured environment file" >&2
  exit 3
fi

services=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
)
assert_services_active() {
  local service
  for service in "${services[@]}"; do
    if [[ "$(systemctl is-active "$service" || true)" != "active" ]]; then
      echo "PHASE13_HOST_ACCEPTANCE=FAIL" >&2
      echo "REASON=service_not_active" >&2
      echo "SERVICE=$service" >&2
      exit 4
    fi
  done
}

assert_services_active

install -d -o bp -g bp "$EVIDENCE_ROOT" "$RUNTIME_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$EVIDENCE_ROOT/$stamp"
install -d -o bp -g bp "$run_dir"

cleanup() {
  set +e
  rm -rf "$VENV"
  set -e
}
trap cleanup EXIT

sudo -u bp "$HOST_PY" -m venv "$VENV"
sudo -u bp "$VENV/bin/python" -m pip install --disable-pip-version-check "$REPO" \
  > "$run_dir/candidate-python-install.txt"

run_candidate() {
  sudo -u bp env \
    MODE=research \
    LIVE_TRADING_ENABLED=false \
    MAX_TRADE_SIZE_USD=0 \
    MAX_DAILY_LOSS_USD=0 \
    BP_ENV_FILE="$ENV_FILE" \
    "$@"
}

export PHASE13_ACCEPTED_PHASE9_5M_RUN_ID="$ACCEPTED_PHASE9_5M_RUN_ID"
export PHASE13_ACCEPTED_PHASE9_5M_SHA="$ACCEPTED_PHASE9_5M_SHA"
export PHASE13_ACCEPTED_PHASE8_5M_RUN_ID="$ACCEPTED_PHASE8_5M_RUN_ID"
export PHASE13_ACCEPTED_PHASE8_5M_SHA="$ACCEPTED_PHASE8_5M_SHA"
export PHASE13_ACCEPTED_PHASE7_5M_RUN_ID="$ACCEPTED_PHASE7_5M_RUN_ID"
export PHASE13_ACCEPTED_PHASE7_5M_SHA="$ACCEPTED_PHASE7_5M_SHA"

set +e
run_candidate timeout --signal=TERM --kill-after=15s "${EVALUATION_TIMEOUT_SECONDS}s" \
  "$VENV/bin/python" - <<'PY' > "$run_dir/research-acceptance.txt" 2>&1
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import create_engine

from bp_engine.config import get_settings
from bp_engine.dashboard.repository import PostgresDashboardRepository
from bp_engine.dashboard.service import build_dashboard_snapshot
from bp_engine.improvement.challenger import build_spread_guard_experiment
from bp_engine.improvement.models import EvidenceRole, PromotionDecision
from bp_engine.improvement.service import (
    ImprovementDecisionError,
    evaluate_experiment,
    record_decision,
    register_experiment,
)
from bp_engine.improvement.source import load_champion_ref, load_phase9_report
from bp_engine.storage import schema
from bp_engine.storage import improvement_schema  # noqa: F401

P9_ID = os.environ["PHASE13_ACCEPTED_PHASE9_5M_RUN_ID"]
P9_SHA = os.environ["PHASE13_ACCEPTED_PHASE9_5M_SHA"]
P8_ID = os.environ["PHASE13_ACCEPTED_PHASE8_5M_RUN_ID"]
P8_SHA = os.environ["PHASE13_ACCEPTED_PHASE8_5M_SHA"]
P7_ID = os.environ["PHASE13_ACCEPTED_PHASE7_5M_RUN_ID"]
P7_SHA = os.environ["PHASE13_ACCEPTED_PHASE7_5M_SHA"]

settings = get_settings()
if settings.mode != "research":
    raise SystemExit("MODE is not research")
if settings.live_trading_enabled is not False:
    raise SystemExit("LIVE_TRADING_ENABLED is not false")
if Decimal(str(settings.max_trade_size_usd)) != Decimal("0"):
    raise SystemExit("MAX_TRADE_SIZE_USD is not zero")
if Decimal(str(settings.max_daily_loss_usd)) != Decimal("0"):
    raise SystemExit("MAX_DAILY_LOSS_USD is not zero")

engine = create_engine(settings.database_url, pool_pre_ping=True)
schema.metadata.create_all(engine)
now = datetime.now(UTC)

with engine.begin() as connection:
    champion = load_champion_ref(connection, P9_ID)
    phase9 = load_phase9_report(connection, P9_ID)
    actual_chain = {
        "calibration_run_id": champion.calibration_run_id,
        "calibration_semantic_sha256": champion.calibration_semantic_sha256,
        "backtest_run_id": champion.backtest_run_id,
        "backtest_semantic_sha256": champion.backtest_semantic_sha256,
        "training_run_id": champion.training_run_id,
        "training_semantic_sha256": champion.training_semantic_sha256,
    }
    expected_chain = {
        "calibration_run_id": P9_ID,
        "calibration_semantic_sha256": P9_SHA,
        "backtest_run_id": P8_ID,
        "backtest_semantic_sha256": P8_SHA,
        "training_run_id": P7_ID,
        "training_semantic_sha256": P7_SHA,
    }
    if actual_chain != expected_chain:
        raise SystemExit(f"accepted champion chain mismatch: {actual_chain}")
    if phase9["run_id"] != P9_ID or phase9["semantic_sha256"] != P9_SHA:
        raise SystemExit("stored Phase 9 report identity mismatch")
    if phase9["source_backtest_run_id"] != P8_ID:
        raise SystemExit("stored Phase 9 report backtest id mismatch")
    if phase9["source_backtest_semantic_sha256"] != P8_SHA:
        raise SystemExit("stored Phase 9 report backtest hash mismatch")
    if phase9["source_training_run_id"] != P7_ID:
        raise SystemExit("stored Phase 9 report training id mismatch")
    if phase9["source_training_semantic_sha256"] != P7_SHA:
        raise SystemExit("stored Phase 9 report training hash mismatch")
    print("CHAMPION_CHAIN=PASS")

    experiment = build_spread_guard_experiment(connection, created_at=now)
    first_registration = register_experiment(connection, experiment)
    second_registration = register_experiment(connection, experiment)
    if not second_registration.existing or second_registration.created:
        raise SystemExit("second experiment registration was not idempotent")
    if experiment.semantic_sha256 is None:
        raise SystemExit("experiment semantic_sha256 missing")
    print("EXPERIMENT_IDEMPOTENT=PASS")

    first_evaluation = evaluate_experiment(
        connection,
        experiment_id=experiment.experiment_id,
        created_at=now,
    )
    second_evaluation = evaluate_experiment(
        connection,
        experiment_id=experiment.experiment_id,
        created_at=now,
    )
    if first_evaluation.evaluation_id != second_evaluation.evaluation_id:
        raise SystemExit("evaluation id changed on idempotent rerun")
    if first_evaluation.semantic_sha256 != second_evaluation.semantic_sha256:
        raise SystemExit("evaluation semantic_sha256 changed on idempotent rerun")
    if first_evaluation.promotion_eligible:
        raise SystemExit("legacy-only evaluation unexpectedly became promotion_eligible")
    if "independent_confirmation_missing" not in first_evaluation.ineligibility_reasons:
        raise SystemExit("legacy-only evaluation did not fail independent confirmation gate")
    if any(
        item.role in {EvidenceRole.FRESH_HOLDOUT, EvidenceRole.PROSPECTIVE_PAPER}
        for item in first_evaluation.evidence_manifest
    ):
        raise SystemExit("acceptance must not fabricate fresh confirmation evidence")
    print("EVALUATION_IDEMPOTENT=PASS")

    try:
        record_decision(
            connection,
            evaluation_id=first_evaluation.evaluation_id,
            decision=PromotionDecision.PROMOTE_CHALLENGER,
            rationale="Acceptance must prove ineligible challengers cannot be promoted.",
            created_at=now,
        )
    except ImprovementDecisionError as exc:
        if "not promotion eligible" not in str(exc):
            raise
    else:
        raise SystemExit("ineligible challenger promotion was not blocked")
    print("PROMOTION_GUARD=PASS")

    keep = record_decision(
        connection,
        evaluation_id=first_evaluation.evaluation_id,
        decision=PromotionDecision.KEEP_CHAMPION,
        rationale=(
            "Phase 13 acceptance has no independent fresh confirmation; keep the accepted "
            "Phase 9 champion."
        ),
        created_at=now,
    )
    keep_again = record_decision(
        connection,
        evaluation_id=first_evaluation.evaluation_id,
        decision=PromotionDecision.KEEP_CHAMPION,
        rationale=(
            "Phase 13 acceptance has no independent fresh confirmation; keep the accepted "
            "Phase 9 champion."
        ),
        created_at=now,
    )
    if keep.decision_id != keep_again.decision_id:
        raise SystemExit("keep_champion decision was not idempotent")
    if keep.resulting_champion != champion:
        raise SystemExit("keep_champion changed the accepted champion")
    print("DECISION=keep_champion")
    print("PROMOTION_ELIGIBLE=false")

repository = PostgresDashboardRepository(engine)
snapshot = build_dashboard_snapshot(repository, now=now, history_limit=100)
if snapshot["mode"]["execution_available"] is not False:
    raise SystemExit("execution_available is not False")
if snapshot["mode"]["live_trading_enabled"] is not False:
    raise SystemExit("dashboard live trading boundary is not false")
paper = repository.get_paper_execution_evidence(history_limit=100)
reconciliation = paper["paper_pnl"]["reconciliation"]
if reconciliation["status"] != "OK" or reconciliation["violation_count"] != 0:
    raise SystemExit(f"paper reconciliation failed: {reconciliation}")
print("RECONCILIATION=PASS")

summary = {
    "experiment_id": experiment.experiment_id,
    "experiment_first_created": first_registration.created,
    "evaluation_id": first_evaluation.evaluation_id,
    "evaluation_semantic_sha256": first_evaluation.semantic_sha256,
    "promotion_eligible": first_evaluation.promotion_eligible,
    "ineligibility_reasons": list(first_evaluation.ineligibility_reasons),
    "decision_id": keep.decision_id,
    "decision": keep.decision.value,
    "reconciliation": reconciliation,
}
print(json.dumps(summary, sort_keys=True, default=str))
engine.dispose()
PY
acceptance_rc=$?
set -e
if [[ "$acceptance_rc" -eq 124 || "$acceptance_rc" -eq 137 ]]; then
  echo "PHASE13_HOST_ACCEPTANCE=FAIL" >&2
  echo "REASON=evaluation_timeout" >&2
  tail -n 160 "$run_dir/research-acceptance.txt" || true
  exit "$acceptance_rc"
fi
if [[ "$acceptance_rc" -ne 0 ]]; then
  echo "PHASE13_HOST_ACCEPTANCE=FAIL" >&2
  echo "REASON=research_acceptance_failed" >&2
  tail -n 160 "$run_dir/research-acceptance.txt" || true
  exit "$acceptance_rc"
fi

for token in \
  CHAMPION_CHAIN=PASS \
  EXPERIMENT_IDEMPOTENT=PASS \
  EVALUATION_IDEMPOTENT=PASS \
  PROMOTION_GUARD=PASS \
  DECISION=keep_champion \
  PROMOTION_ELIGIBLE=false \
  RECONCILIATION=PASS; do
  if ! grep -qx "$token" "$run_dir/research-acceptance.txt"; then
    echo "PHASE13_HOST_ACCEPTANCE=FAIL" >&2
    echo "REASON=missing_acceptance_token" >&2
    echo "TOKEN=$token" >&2
    exit 7
  fi
done

assert_services_active
echo "SERVICES_ACTIVE=PASS" | tee "$run_dir/services.txt"

echo "CANDIDATE_HEAD=$EXPECTED_HEAD"
echo "EVIDENCE_DIR=$run_dir"
echo "LIVE_TRADING_ENABLED=false"
echo "MAX_TRADE_SIZE_USD=0"
echo "MAX_DAILY_LOSS_USD=0"
echo "PHASE13_HOST_ACCEPTANCE=PASS"
