#!/usr/bin/env bash
set -Eeuo pipefail

SHA="${PHASE14_V3_PAPER_HEAD:-}"
BRANCH="${PHASE14_V3_PAPER_BRANCH:-main}"
REPO="${PHASE14_V3_PAPER_REPO:-/opt/bp}"
ENV_FILE="${PHASE14_V3_PAPER_ENV_FILE:-/etc/bp/bp.env}"
SAFETY_FILE="${PHASE14_V3_PAPER_SAFETY_FILE:-/etc/bp/bp-prospective-runtime-safety.env}"
RUNTIME_ROOT="${PHASE14_V3_PAPER_RUNTIME_ROOT:-/var/lib/bp/runtime}"
STATE_ROOT="${PHASE14_V3_PAPER_STATE_ROOT:-/var/lib/bp/v3-paper}"
EVIDENCE_DIR="${PHASE14_V3_PAPER_EVIDENCE_DIR:-/var/lib/bp/evidence}"
MODEL_SOURCE_OVERRIDE="${PHASE14_V3_FROZEN_MODEL_PATH:-}"
FROZEN_MODEL_SHA=124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7

LEGACY_UNIT=bp-paper-execution.service
PREDICTOR_UNIT=bp-v3-frozen-predictor.service
V3_EXEC_UNIT=bp-v3-paper-execution.service
LEGACY_PATH="/etc/systemd/system/$LEGACY_UNIT"
PREDICTOR_PATH="/etc/systemd/system/$PREDICTOR_UNIT"
V3_EXEC_PATH="/etc/systemd/system/$V3_EXEC_UNIT"
CURRENT_LINK="$RUNTIME_ROOT/v3-paper-current"

fail() {
  echo "PHASE14_V3_FROZEN_PAPER_ROLLOUT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local path=$1
  local key=$2
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$path"
}

git_repo() {
  git -c safe.directory="$REPO" -C "$REPO" "$@"
}

require_zero_money() {
  local path mode live trade loss
  for path in "$ENV_FILE" "$SAFETY_FILE"; do
    [[ -f "$path" ]] || fail "safety_file_missing:$path"
    mode=$(read_env "$path" MODE)
    live=$(read_env "$path" LIVE_TRADING_ENABLED)
    trade=$(read_env "$path" MAX_TRADE_SIZE_USD)
    loss=$(read_env "$path" MAX_DAILY_LOSS_USD)
    [[ "$mode" == "research" ]] || fail "mode_not_research:$path"
    [[ "$live" == "false" ]] || fail "live_trading_not_false:$path"
    [[ "$trade" == "0" ]] || fail "max_trade_size_not_zero:$path"
    [[ "$loss" == "0" ]] || fail "max_daily_loss_not_zero:$path"
  done
}

require_active() {
  local unit=$1
  systemctl is-active --quiet "$unit" || fail "unit_not_active:$unit"
}

if [[ ! "$SHA" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_V3_PAPER_HEAD must be an exact 40-character verified main SHA" >&2
  exit 2
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PHASE14_V3_PAPER_BRANCH contains unsupported characters" >&2
  exit 2
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "run as root" >&2
  exit 2
fi
[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"

OLD_DEPLOYED_HEAD=$(git_repo rev-parse HEAD)
REMOTE_HEAD=$(git_repo rev-parse "refs/remotes/origin/$BRANCH")
[[ "$REMOTE_HEAD" == "$SHA" ]] || fail "prefetched_remote_branch_head_mismatch:$REMOTE_HEAD"
git_repo cat-file -e "$SHA^{commit}" || fail "candidate_commit_missing"

require_zero_money
for unit in   bp-postgres.service   bp-recorder.service   "$LEGACY_UNIT"   bp-prospective-outcomes.service   bp-v4-forward-coverage.timer; do
  require_active "$unit"
done

RECORDER_PID_BEFORE=$(systemctl show --property=MainPID --value bp-recorder.service)
[[ "$RECORDER_PID_BEFORE" =~ ^[1-9][0-9]*$ ]] || fail "invalid_recorder_pid_before"
LEGACY_PID_BEFORE=$(systemctl show --property=MainPID --value "$LEGACY_UNIT")
[[ "$LEGACY_PID_BEFORE" =~ ^[1-9][0-9]*$ ]] || fail "invalid_legacy_paper_pid_before"

required_paths=(
  src/bp_engine/v3_paper/service.py
  src/bp_engine/v3_paper/cli.py
  src/bp_engine/v3_paper/report.py
  src/bp_engine/v3_paper/report_cli.py
  src/bp_engine/execution/service.py
  src/bp_engine/execution/models.py
  src/bp_engine/execution/cli.py
  scripts/run_v3_frozen_paper.py
  scripts/run_v3_paper_execution.py
  scripts/report_v3_paper.py
  scripts/run_v1_paper_execution_isolated.py
  deploy/bp-paper-execution-v1-isolated.service
  deploy/bp-v3-frozen-predictor.service
  deploy/bp-v3-paper-execution.service
)
for path in "${required_paths[@]}"; do
  git_repo cat-file -e "$SHA:$path" || fail "candidate_path_missing:$path"
done

find_model() {
  local candidate digest root
  if [[ -n "$MODEL_SOURCE_OVERRIDE" ]]; then
    [[ -f "$MODEL_SOURCE_OVERRIDE" ]] || fail "model_override_missing"
    digest=$(sha256sum "$MODEL_SOURCE_OVERRIDE" | awk '{print $1}')
    [[ "$digest" == "$FROZEN_MODEL_SHA" ]] || fail "model_override_sha_mismatch:$digest"
    printf '%s\n' "$MODEL_SOURCE_OVERRIDE"
    return
  fi

  for root in /var/tmp /var/lib/bp/evidence; do
    [[ -d "$root" ]] || continue
    while IFS= read -r -d '' candidate; do
      digest=$(sha256sum "$candidate" | awk '{print $1}')
      if [[ "$digest" == "$FROZEN_MODEL_SHA" ]]; then
        printf '%s\n' "$candidate"
        return
      fi
    done < <(
      find "$root" -maxdepth 4 -type f         \( -name '*.joblib' -o -name 'model*' \)         -path '*v3*' -print0 2>/dev/null
    )
  done
  fail "exact_frozen_v3_model_not_found"
}

MODEL_SOURCE=$(find_model)
[[ -n "$MODEL_SOURCE" ]] || fail "model_source_empty"

VERSION_DIR="$RUNTIME_ROOT/v3-paper-$SHA"
STAGING_DIR="$RUNTIME_ROOT/.v3-paper-$SHA.staging"
MODEL_TARGET="$STATE_ROOT/frozen-model.joblib"
ACTIVATION_TARGET="$STATE_ROOT/activation.json"
ACTIVATION_TMP=""
ACTIVATION_SOURCE=""
ACTIVATION_INSTALLED=0
OLD_LINK_TARGET=""
LEGACY_BACKUP=""
PREDICTOR_BACKUP=""
V3_EXEC_BACKUP=""
LEGACY_PREEXISTED=0
PREDICTOR_PREEXISTED=0
V3_EXEC_PREEXISTED=0
PREDICTOR_WAS_ENABLED=0
PREDICTOR_WAS_ACTIVE=0
V3_EXEC_WAS_ENABLED=0
V3_EXEC_WAS_ACTIVE=0
ROLLBACK_ARMED=0
ACTIVATED_AT=""

capture_unit() {
  local path=$1
  local var_prefix=$2
  local backup
  if [[ -f "$path" ]]; then
    backup=$(mktemp "/var/tmp/${var_prefix}.XXXXXX")
    cp -a "$path" "$backup"
    case "$var_prefix" in
      bp-v3-legacy) LEGACY_PREEXISTED=1; LEGACY_BACKUP="$backup" ;;
      bp-v3-predictor) PREDICTOR_PREEXISTED=1; PREDICTOR_BACKUP="$backup" ;;
      bp-v3-exec) V3_EXEC_PREEXISTED=1; V3_EXEC_BACKUP="$backup" ;;
    esac
  fi
}

rollback() {
  set +e
  echo "PHASE14_V3_FROZEN_PAPER_ROLLBACK=START" >&2
  systemctl disable --now "$PREDICTOR_UNIT" "$V3_EXEC_UNIT" >/dev/null 2>&1 || true

  if (( LEGACY_PREEXISTED )); then
    cp -a "$LEGACY_BACKUP" "$LEGACY_PATH"
  else
    rm -f "$LEGACY_PATH"
  fi
  if (( PREDICTOR_PREEXISTED )); then
    cp -a "$PREDICTOR_BACKUP" "$PREDICTOR_PATH"
  else
    rm -f "$PREDICTOR_PATH"
  fi
  if (( V3_EXEC_PREEXISTED )); then
    cp -a "$V3_EXEC_BACKUP" "$V3_EXEC_PATH"
  else
    rm -f "$V3_EXEC_PATH"
  fi

  if [[ -n "$OLD_LINK_TARGET" ]]; then
    ln -sfn "$OLD_LINK_TARGET" "$CURRENT_LINK"
  else
    rm -f "$CURRENT_LINK"
  fi

  if (( ACTIVATION_INSTALLED )); then
    rm -f "$ACTIVATION_TARGET"
  fi

  systemctl daemon-reload >/dev/null 2>&1 || true
  systemctl restart "$LEGACY_UNIT" >/dev/null 2>&1 || true
  if (( PREDICTOR_WAS_ENABLED )); then systemctl enable "$PREDICTOR_UNIT" >/dev/null 2>&1 || true; fi
  if (( PREDICTOR_WAS_ACTIVE )); then systemctl start "$PREDICTOR_UNIT" >/dev/null 2>&1 || true; fi
  if (( V3_EXEC_WAS_ENABLED )); then systemctl enable "$V3_EXEC_UNIT" >/dev/null 2>&1 || true; fi
  if (( V3_EXEC_WAS_ACTIVE )); then systemctl start "$V3_EXEC_UNIT" >/dev/null 2>&1 || true; fi
  echo "PHASE14_V3_FROZEN_PAPER_ROLLBACK=COMPLETE" >&2
  echo "NOTE=append-only research rows created before rollback were intentionally preserved" >&2
}

cleanup() {
  rm -rf "$STAGING_DIR"
  rm -f "${ACTIVATION_TMP:-}" "${LEGACY_BACKUP:-}"     "${PREDICTOR_BACKUP:-}" "${V3_EXEC_BACKUP:-}"
}

on_exit() {
  local status=$?
  if (( status != 0 && ROLLBACK_ARMED == 1 )); then rollback; fi
  cleanup
  exit "$status"
}
trap on_exit EXIT

if [[ -L "$CURRENT_LINK" ]]; then OLD_LINK_TARGET=$(readlink "$CURRENT_LINK"); fi
capture_unit "$LEGACY_PATH" bp-v3-legacy
capture_unit "$PREDICTOR_PATH" bp-v3-predictor
capture_unit "$V3_EXEC_PATH" bp-v3-exec
systemctl is-enabled --quiet "$PREDICTOR_UNIT" 2>/dev/null && PREDICTOR_WAS_ENABLED=1 || true
systemctl is-active --quiet "$PREDICTOR_UNIT" 2>/dev/null && PREDICTOR_WAS_ACTIVE=1 || true
systemctl is-enabled --quiet "$V3_EXEC_UNIT" 2>/dev/null && V3_EXEC_WAS_ENABLED=1 || true
systemctl is-active --quiet "$V3_EXEC_UNIT" 2>/dev/null && V3_EXEC_WAS_ACTIVE=1 || true
ROLLBACK_ARMED=1

install -d -o root -g bp -m 0755 "$RUNTIME_ROOT"
if [[ ! -d "$VERSION_DIR" ]]; then
  rm -rf "$STAGING_DIR"
  install -d -o root -g bp -m 0755 "$STAGING_DIR"
  git_repo archive "$SHA" | tar -x -C "$STAGING_DIR"
  chown -R root:bp "$STAGING_DIR"
  chmod -R a-w "$STAGING_DIR"
  chmod -R a+rX "$STAGING_DIR"
  mv "$STAGING_DIR" "$VERSION_DIR"
fi
ln -sfn "$VERSION_DIR" "$CURRENT_LINK"

install -d -o bp -g bp -m 0750 "$STATE_ROOT"
install -o bp -g bp -m 0440 "$MODEL_SOURCE" "$MODEL_TARGET"
[[ "$(sha256sum "$MODEL_TARGET" | awk '{print $1}')" == "$FROZEN_MODEL_SHA" ]]   || fail "installed_model_sha_mismatch"

if [[ -f "$ACTIVATION_TARGET" ]]; then
  ACTIVATION_SOURCE="$ACTIVATION_TARGET"
  ACTIVATED_AT=$(
    "$REPO/.venv/bin/python" - "$ACTIVATION_TARGET" "$SHA" "$FROZEN_MODEL_SHA" <<'PY'
import json
import sys
from pathlib import Path

path, candidate_head, model_sha = sys.argv[1:]
payload = json.loads(Path(path).read_text(encoding="utf-8"))
expected = {
    "candidate_head": candidate_head,
    "model_sha256": model_sha,
    "prediction_version": "v3-frozen-paper-v1",
    "execution_version": "paper-execution-v3-frozen-v1",
    "paper_starting_cash_usd": "100.00",
    "paper_target_notional_usd": "5.00",
    "real_money_usd": "0.00",
    "automatic_promotion": False,
}
for key, value in expected.items():
    if payload.get(key) != value:
        raise SystemExit(f"existing activation manifest mismatch: {key}")
activated_at = payload.get("activated_at")
if not isinstance(activated_at, str) or not activated_at.endswith("Z"):
    raise SystemExit("existing activation timestamp is invalid")
print(activated_at)
PY
  ) || fail "existing_activation_manifest_mismatch"
else
  ACTIVATED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  ACTIVATION_TMP=$(mktemp /var/tmp/bp-v3-activation.XXXXXX.json)
  "$REPO/.venv/bin/python" - "$ACTIVATION_TMP" "$ACTIVATED_AT" "$SHA" "$FROZEN_MODEL_SHA" <<'PY'
import json
import sys
from pathlib import Path

path, activated_at, candidate_head, model_sha = sys.argv[1:]
payload = {
    "activated_at": activated_at,
    "candidate_head": candidate_head,
    "model_sha256": model_sha,
    "prediction_version": "v3-frozen-paper-v1",
    "execution_version": "paper-execution-v3-frozen-v1",
    "paper_starting_cash_usd": "100.00",
    "paper_target_notional_usd": "5.00",
    "real_money_usd": "0.00",
    "automatic_promotion": False,
}
Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
  chown bp:bp "$ACTIVATION_TMP"
  chmod 0440 "$ACTIVATION_TMP"
  ACTIVATION_SOURCE="$ACTIVATION_TMP"
fi

sudo -u bp env   MODE=research   LIVE_TRADING_ENABLED=false   MAX_TRADE_SIZE_USD=0   MAX_DAILY_LOSS_USD=0   PYTHONPATH="$VERSION_DIR/src"   "$REPO/.venv/bin/python"   "$VERSION_DIR/scripts/run_v3_frozen_paper.py"   --env-file "$ENV_FILE"   --model "$MODEL_TARGET"   --activation "$ACTIVATION_SOURCE"   --verify-model >/var/tmp/bp-v3-model-verify.json
grep -q '"verified": true' /var/tmp/bp-v3-model-verify.json   || fail "frozen_model_runtime_verification_failed"

if [[ "$ACTIVATION_SOURCE" == "$ACTIVATION_TMP" ]]; then
  install -o bp -g bp -m 0440 "$ACTIVATION_TMP" "$ACTIVATION_TARGET"
  ACTIVATION_INSTALLED=1
fi

install -o root -g root -m 0644   "$VERSION_DIR/deploy/bp-paper-execution-v1-isolated.service" "$LEGACY_PATH"
install -o root -g root -m 0644   "$VERSION_DIR/deploy/bp-v3-frozen-predictor.service" "$PREDICTOR_PATH"
install -o root -g root -m 0644   "$VERSION_DIR/deploy/bp-v3-paper-execution.service" "$V3_EXEC_PATH"
systemctl daemon-reload

systemctl restart "$LEGACY_UNIT"
require_active "$LEGACY_UNIT"
systemctl enable --now "$PREDICTOR_UNIT"
systemctl enable --now "$V3_EXEC_UNIT"
require_active "$PREDICTOR_UNIT"
require_active "$V3_EXEC_UNIT"
require_active bp-prospective-outcomes.service
require_active bp-v4-forward-coverage.timer
require_active bp-recorder.service

require_zero_money
RECORDER_PID_AFTER=$(systemctl show --property=MainPID --value bp-recorder.service)
[[ "$RECORDER_PID_AFTER" == "$RECORDER_PID_BEFORE" ]] || fail "recorder_pid_changed"
[[ "$(git_repo rev-parse HEAD)" == "$OLD_DEPLOYED_HEAD" ]]   || fail "deployed_checkout_changed"

VERIFY_JSON=$(mktemp /var/tmp/bp-v3-paper-db-verify.XXXXXX.json)
sudo -u bp env   PYTHONPATH="$VERSION_DIR/src"   "$REPO/.venv/bin/python" -   "$ENV_FILE" "$ACTIVATION_TARGET" "$VERIFY_JSON" <<'PY'
import json
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select

from bp_engine.config import Settings
from bp_engine.storage import schema

env_file, activation_path, output_path = sys.argv[1:]
settings = Settings(_env_file=env_file)
activation = json.loads(Path(activation_path).read_text(encoding="utf-8"))
activated_at = datetime.fromisoformat(activation["activated_at"].replace("Z", "+00:00"))
engine = create_engine(settings.database_url)
with engine.connect() as connection:
    v3_predictions = int(
        connection.execute(
            select(func.count()).select_from(schema.live_predictions).where(
                schema.live_predictions.c.prediction_version == "v3-frozen-paper-v1"
            )
        ).scalar_one()
    )
    pre_activation = int(
        connection.execute(
            select(func.count()).select_from(schema.live_predictions).where(
                schema.live_predictions.c.prediction_version == "v3-frozen-paper-v1",
                schema.live_predictions.c.market_start_at < activated_at,
            )
        ).scalar_one()
    )
    invalid_v3_orders = int(
        connection.execute(
            select(func.count())
            .select_from(
                schema.paper_orders.join(
                    schema.live_predictions,
                    schema.paper_orders.c.prediction_id
                    == schema.live_predictions.c.prediction_id,
                )
            )
            .where(
                schema.paper_orders.c.execution_version
                == "paper-execution-v3-frozen-v1",
                schema.live_predictions.c.prediction_version
                != "v3-frozen-paper-v1",
            )
        ).scalar_one()
    )
    v3_orders = int(
        connection.execute(
            select(func.count()).select_from(schema.paper_orders).where(
                schema.paper_orders.c.execution_version
                == "paper-execution-v3-frozen-v1"
            )
        ).scalar_one()
    )
    v3_fills = int(
        connection.execute(
            select(func.count())
            .select_from(
                schema.paper_fills.join(
                    schema.paper_orders,
                    schema.paper_fills.c.paper_order_id
                    == schema.paper_orders.c.paper_order_id,
                )
            )
            .where(
                schema.paper_orders.c.execution_version
                == "paper-execution-v3-frozen-v1"
            )
        ).scalar_one()
    )
if pre_activation != 0:
    raise SystemExit("pre-activation V3 paper prediction found")
if invalid_v3_orders != 0:
    raise SystemExit("V3 paper order sourced from non-V3 prediction")
payload = {
    "v3_prediction_count": v3_predictions,
    "v3_order_count": v3_orders,
    "v3_fill_count": v3_fills,
    "pre_activation_prediction_count": pre_activation,
    "invalid_v3_order_source_count": invalid_v3_orders,
}
Path(output_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
engine.dispose()
PY

EVIDENCE_PATH="$EVIDENCE_DIR/phase14-v3-frozen-paper-${SHA:0:12}-$(date -u +%Y%m%dT%H%M%SZ).json"
install -d -o bp -g bp -m 0750 "$EVIDENCE_DIR"
"$REPO/.venv/bin/python" -   "$EVIDENCE_PATH" "$VERIFY_JSON" "$ACTIVATION_TARGET"   "$OLD_DEPLOYED_HEAD" "$SHA" "$MODEL_SOURCE" "$MODEL_TARGET"   "$RECORDER_PID_BEFORE" "$RECORDER_PID_AFTER" "$LEGACY_PID_BEFORE" <<'PY'
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

(
    evidence_path,
    verify_path,
    activation_path,
    deployed_head,
    candidate_head,
    model_source,
    model_target,
    recorder_pid_before,
    recorder_pid_after,
    legacy_pid_before,
) = sys.argv[1:]
payload = {
    "verdict": "PASS",
    "recorded_at": datetime.now(UTC).isoformat(),
    "candidate_head": candidate_head,
    "deployed_checkout_head_unchanged": deployed_head,
    "runtime_path": f"/var/lib/bp/runtime/v3-paper-{candidate_head}",
    "activation": json.loads(Path(activation_path).read_text(encoding="utf-8")),
    "database_verification": json.loads(Path(verify_path).read_text(encoding="utf-8")),
    "model_source_path": model_source,
    "model_installed_path": model_target,
    "model_sha256": "124627e15cab3997b8abe54ec5237450d976ab5682953f45a1399a76b6dae0e7",
    "legacy_paper_service": "active",
    "v3_predictor_service": "active",
    "v3_paper_execution_service": "active",
    "prospective_outcomes_service": "active",
    "v4_forward_timer": "active",
    "recorder_pid_before": int(recorder_pid_before),
    "recorder_pid_after": int(recorder_pid_after),
    "recorder_restarted": recorder_pid_before != recorder_pid_after,
    "legacy_paper_pid_before": int(legacy_pid_before),
    "safety": {
        "mode": "research",
        "live_trading_enabled": False,
        "max_trade_size_usd": 0,
        "max_daily_loss_usd": 0,
        "real_money_usd": 0,
        "model_refit_performed": False,
        "threshold_tuning_performed": False,
        "automatic_promotion": False,
        "live_order_path_enabled": False,
    },
}
Path(evidence_path).write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n",
    encoding="utf-8",
)
PY
chown bp:bp "$EVIDENCE_PATH"
chmod 0640 "$EVIDENCE_PATH"

rm -f "$VERIFY_JSON" /var/tmp/bp-v3-model-verify.json
ROLLBACK_ARMED=0

echo "PHASE14_V3_FROZEN_PAPER_ROLLOUT=PASS"
echo "ACTIVATED_AT=$ACTIVATED_AT"
echo "CANDIDATE_HEAD=$SHA"
echo "DEPLOYED_CHECKOUT_HEAD=$OLD_DEPLOYED_HEAD"
echo "MODEL_SOURCE=$MODEL_SOURCE"
echo "MODEL_SHA256=$FROZEN_MODEL_SHA"
echo "VIRTUAL_STARTING_CASH_USD=100.00"
echo "VIRTUAL_TARGET_NOTIONAL_USD=5.00"
echo "REAL_MONEY_USD=0.00"
echo "EVIDENCE_PATH=$EVIDENCE_PATH"
cat "$EVIDENCE_PATH"
