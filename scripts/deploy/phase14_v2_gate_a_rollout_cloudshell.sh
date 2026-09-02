#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V2_GATE_A_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V2_GATE_A_ZONE:-us-east1-c}"
VM="${PHASE14_V2_GATE_A_VM:-bp-recorder}"
BRANCH="${PHASE14_V2_GATE_A_BRANCH:-main}"
EXPECTED_HEAD="${PHASE14_V2_GATE_A_HEAD:-}"
EXPECTED_FROM_HEAD="${PHASE14_V2_GATE_A_FROM_HEAD:-be1f82f65d15b2e172495e6ae934ec9a78648c32}"
ENV_FILE="${PHASE14_V2_GATE_A_ENV_FILE:-/etc/bp/bp.env}"
MAX_FORWARD_WAIT_SECONDS="${PHASE14_V2_GATE_A_MAX_FORWARD_WAIT_SECONDS:-720}"

if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_V2_GATE_A_HEAD must be the exact 40-character verified candidate SHA" >&2
  exit 2
fi
if [[ ! "$EXPECTED_FROM_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_V2_GATE_A_FROM_HEAD must be the exact expected deployed SHA" >&2
  exit 2
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PHASE14_V2_GATE_A_BRANCH contains unsupported characters" >&2
  exit 2
fi
if [[ "$ENV_FILE" != /* ]]; then
  echo "PHASE14_V2_GATE_A_ENV_FILE must be an absolute path" >&2
  exit 2
fi
if ! [[ "$MAX_FORWARD_WAIT_SECONDS" =~ ^[1-9][0-9]*$ ]]; then
  echo "PHASE14_V2_GATE_A_MAX_FORWARD_WAIT_SECONDS must be a positive integer" >&2
  exit 2
fi
if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud is required; run this helper from Google Cloud Shell" >&2
  exit 2
fi
if ! gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q .; then
  echo "no active gcloud account; authorize Cloud Shell and rerun" >&2
  exit 2
fi

gcloud config set project "$PROJECT" >/dev/null

printf -v HEAD_Q '%q' "$EXPECTED_HEAD"
printf -v FROM_HEAD_Q '%q' "$EXPECTED_FROM_HEAD"
printf -v BRANCH_Q '%q' "$BRANCH"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"
printf -v MAX_WAIT_Q '%q' "$MAX_FORWARD_WAIT_SECONDS"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

SHA="${PHASE14_V2_GATE_A_HEAD:?}"
EXPECTED_FROM_HEAD="${PHASE14_V2_GATE_A_FROM_HEAD:?}"
BRANCH="${PHASE14_V2_GATE_A_BRANCH:?}"
ENV_FILE="${PHASE14_V2_GATE_A_ENV_FILE:?}"
MAX_FORWARD_WAIT_SECONDS="${PHASE14_V2_GATE_A_MAX_FORWARD_WAIT_SECONDS:?}"
REPO=/opt/bp
SHORT="${SHA:0:12}"
WT="/var/tmp/bp-phase14-v2-gate-a-${SHORT}-$$"
ROLLBACK_WT="/var/tmp/bp-phase14-v2-gate-a-rollback-${SHORT}-$$"
RECORDER_UNIT=bp-recorder.service
PREDICTOR_UNIT=bp-live-predictor.service
OUTCOME_UNIT=bp-prospective-outcomes.service
CORE_SERVICES=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
  bp-live-predictor.service
  bp-prospective-outcomes.service
)
ROLLBACK_ARMED=0
OLD_HEAD=""
ROLLOUT_STARTED=""
DISK_BEFORE=""
DISK_AFTER=""
SOAK_FILE=""
SNAPSHOT_FILE=""
PROVENANCE_FILE=""
TARGET_FILE=""
TARGET_CONDITION_ID=""
TARGET_START=""
TARGET_END=""
FEATURE_STATS_FILE=""
FEATURE_VERIFY_FILE=""
COVERAGE_FILE=""

fail() {
  echo "PHASE14_V2_GATE_A_ROLLOUT=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

validate_deployed_checkout() {
  local entry
  local code
  local path

  while IFS= read -r entry; do
    [[ -n "$entry" ]] || continue
    code=${entry:0:2}
    path=${entry:3}
    if [[ "$code" == "??" ]]; then
      case "$path" in
        .node/*|apps/dashboard/.next/*|apps/dashboard/node_modules/*|apps/dashboard/tsconfig.tsbuildinfo)
          ;;
        *)
          fail "unexpected_deployed_checkout_change:$path"
          ;;
      esac
    else
      case "$path" in
        apps/dashboard/next-env.d.ts|apps/dashboard/tsconfig.json)
          ;;
        *)
          fail "unexpected_deployed_checkout_change:$path"
          ;;
      esac
    fi
  done < <(git -C "$REPO" status --porcelain --untracked-files=all)
}

validate_rollout_scope() {
  local path
  local recorder_seen=0
  local v2_seen=0

  while IFS= read -r path; do
    [[ -n "$path" ]] || continue
    case "$path" in
      PROJECT_STATE.json|docs/*|.github/workflows/ci.yml)
        ;;
      scripts/generate_v2_features.py|scripts/report_v2_feature_coverage.py|scripts/deploy/phase14_v2_gate_a_rollout_cloudshell.sh)
        ;;
      src/bp_engine/recorder/state.py)
        recorder_seen=1
        ;;
      src/bp_engine/features/v2_*.py)
        v2_seen=1
        ;;
      tests/features/test_v2_*.py|tests/recorder/test_state_reducer.py|tests/deploy/test_phase14_v2_gate_a_rollout.py)
        ;;
      *)
        fail "unexpected_rollout_path:$path"
        ;;
    esac
  done < <(git -C "$REPO" diff --name-only "$OLD_HEAD" "$SHA")

  (( recorder_seen )) || fail "recorder_provenance_change_missing"
  (( v2_seen )) || fail "v2_feature_change_missing"

  local frozen_path
  for frozen_path in \
    src/bp_engine/features/service.py \
    src/bp_engine/live_prediction \
    src/bp_engine/calibration \
    src/bp_engine/execution; do
    if ! git -C "$REPO" diff --quiet "$OLD_HEAD" "$SHA" -- "$frozen_path"; then
      fail "frozen_v1_path_changed:$frozen_path"
    fi
  done
}

require_research_zero_money() {
  local mode
  local live_trading_enabled
  local max_trade_size_usd
  local max_daily_loss_usd

  mode=$(read_env MODE)
  live_trading_enabled=$(read_env LIVE_TRADING_ENABLED)
  max_trade_size_usd=$(read_env MAX_TRADE_SIZE_USD)
  max_daily_loss_usd=$(read_env MAX_DAILY_LOSS_USD)
  if [[ "$mode" != "research" || \
        "$live_trading_enabled" != "false" || \
        "$max_trade_size_usd" != "0" || \
        "$max_daily_loss_usd" != "0" ]]; then
    fail "research_zero_money_boundary_not_satisfied"
  fi
  MODE=$mode
  LIVE_TRADING_ENABLED=$live_trading_enabled
  MAX_TRADE_SIZE_USD=$max_trade_size_usd
  MAX_DAILY_LOSS_USD=$max_daily_loss_usd
}

require_services_active() {
  local service
  for service in "${CORE_SERVICES[@]}"; do
    systemctl is-active --quiet "$service" || fail "service_not_active:$service"
  done
  systemctl is-enabled --quiet "$PREDICTOR_UNIT" || fail "service_not_enabled:$PREDICTOR_UNIT"
  systemctl is-enabled --quiet "$OUTCOME_UNIT" || fail "service_not_enabled:$OUTCOME_UNIT"
}

run_disk_health() {
  local destination=$1
  if ! sudo -u bp "$REPO/.venv/bin/python" "$REPO/scripts/storage_maintenance.py" disk-health \
      --env-file "$ENV_FILE" > "$destination"; then
    cat "$destination" >&2 || true
    fail "disk_health_command_failed"
  fi
  "$REPO/.venv/bin/python" - "$destination" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("status") != "ok":
    raise SystemExit(f"disk status is not ok: {payload.get('status')!r}")
PY
}

run_soak_gate() {
  SOAK_FILE=$(mktemp /var/tmp/bp-phase14-v2-gate-a-soak.XXXXXX.json)
  if ! sudo -u bp bash -c \
      'set -a; source "$1"; set +a; exec "$2" "$3" --hours 0.01 --minimum-hours 0.008' \
      _ "$ENV_FILE" "$REPO/.venv/bin/python" "$REPO/scripts/soak_report.py" > "$SOAK_FILE"; then
    cat "$SOAK_FILE" >&2 || true
    fail "post_restart_soak_failed"
  fi
  "$REPO/.venv/bin/python" - "$SOAK_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
required = {
    "polymarket/market",
    "bybit/spot",
    "bybit/linear",
    "coinbase/spot",
}
if payload.get("passed") is not True:
    raise SystemExit("soak report did not pass")
feeds = payload.get("feeds") or {}
missing = sorted(label for label in required if int((feeds.get(label) or {}).get("event_count", 0)) <= 0)
if missing:
    raise SystemExit(f"required feeds missing post-restart events: {missing}")
PY
}

verify_dashboard_safety() {
  SNAPSHOT_FILE=$(mktemp /var/tmp/bp-phase14-v2-gate-a-snapshot.XXXXXX.json)
  if ! curl -fsS http://127.0.0.1:8787/api/v1/snapshot > "$SNAPSHOT_FILE"; then
    fail "dashboard_snapshot_unavailable"
  fi
  "$REPO/.venv/bin/python" - "$SNAPSHOT_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

snapshot = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
mode = snapshot.get("mode") or {}
if mode.get("trading_mode") != "RESEARCH":
    raise SystemExit("dashboard left RESEARCH mode")
if mode.get("live_trading_enabled") is not False:
    raise SystemExit("dashboard reports live trading enabled")
if mode.get("execution_available") is not False:
    raise SystemExit("dashboard reports real execution available")
PY
}

prove_real_last_trade_provenance() {
  PROVENANCE_FILE=$(mktemp /var/tmp/bp-phase14-v2-gate-a-provenance.XXXXXX.json)
  if ! sudo -u bp env PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" - \
      "$ENV_FILE" "$ROLLOUT_STARTED" 150 > "$PROVENANCE_FILE" <<'PY'; then
from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime

from sqlalchemy import create_engine, select

from bp_engine.config import Settings
from bp_engine.storage.schema import market_state_1s


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


env_file, started_raw, wait_raw = sys.argv[1:]
started = parse(started_raw)
deadline = time.monotonic() + int(wait_raw)
settings = Settings(_env_file=env_file)
engine = create_engine(settings.database_url)
proof = None

while time.monotonic() < deadline and proof is None:
    with engine.connect() as connection:
        rows = list(
            connection.execute(
                select(market_state_1s)
                .where(
                    market_state_1s.c.source == "polymarket",
                    market_state_1s.c.stream == "market",
                    market_state_1s.c.bucket_at >= started,
                )
                .order_by(
                    market_state_1s.c.asset_id,
                    market_state_1s.c.bucket_at,
                    market_state_1s.c.id,
                )
            ).mappings()
        )

    complete = []
    preservation = None
    previous_by_asset = {}
    for row in rows:
        state = row["state"] if isinstance(row["state"], dict) else {}
        required = (
            "last_trade_price",
            "last_trade_source_at",
            "last_trade_received_at",
            "last_trade_event_dedupe_key",
        )
        if all(state.get(key) not in (None, "") for key in required):
            complete.append(row)
        asset = str(row["asset_id"] or "")
        previous = previous_by_asset.get(asset)
        if previous is not None:
            prev_state = previous["state"] if isinstance(previous["state"], dict) else {}
            same_trade = all(prev_state.get(key) == state.get(key) for key in required)
            generic_advanced = utc(row["last_event_at"]) > utc(previous["last_event_at"])
            if (
                same_trade
                and generic_advanced
                and all(state.get(key) not in (None, "") for key in required)
            ):
                preservation = {
                    "asset_id": asset,
                    "earlier_last_event_at": utc(previous["last_event_at"]).isoformat(),
                    "later_last_event_at": utc(row["last_event_at"]).isoformat(),
                    "last_trade_source_at": state["last_trade_source_at"],
                    "last_trade_received_at": state["last_trade_received_at"],
                    "last_trade_event_dedupe_key": state["last_trade_event_dedupe_key"],
                }
                break
        previous_by_asset[asset] = row

    if complete and preservation is not None:
        latest = complete[-1]
        latest_state = latest["state"]
        proof = {
            "real_last_trade_provenance": True,
            "condition_id": str(latest["instrument"]),
            "asset_id": str(latest["asset_id"]),
            "last_trade_price": latest_state["last_trade_price"],
            "last_trade_source_at": latest_state["last_trade_source_at"],
            "last_trade_received_at": latest_state["last_trade_received_at"],
            "last_trade_event_dedupe_key": latest_state["last_trade_event_dedupe_key"],
            "generic_activity_preserved_trade_timestamp": preservation,
        }
        break
    time.sleep(5)

engine.dispose()
if proof is None:
    raise SystemExit("no real timestamped last-trade preservation proof observed in bounded window")
print(json.dumps(proof, sort_keys=True))
PY
    cat "$PROVENANCE_FILE" >&2 || true
    fail "real_last_trade_provenance_not_proven"
  fi
}

wait_for_forward_5m_market() {
  TARGET_FILE=$(mktemp /var/tmp/bp-phase14-v2-gate-a-target.XXXXXX.json)
  if ! sudo -u bp env PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" - \
      "$ENV_FILE" "$ROLLOUT_STARTED" "$MAX_FORWARD_WAIT_SECONDS" > "$TARGET_FILE" <<'PY'; then
from __future__ import annotations

import json
import sys
import time
from datetime import UTC, datetime

from sqlalchemy import create_engine, select

from bp_engine.config import Settings
from bp_engine.storage.schema import polymarket_markets


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


env_file, started_raw, wait_raw = sys.argv[1:]
started = parse(started_raw)
deadline = time.monotonic() + int(wait_raw)
settings = Settings(_env_file=env_file)
engine = create_engine(settings.database_url)
found = None
while time.monotonic() < deadline and found is None:
    now = datetime.now(UTC)
    with engine.connect() as connection:
        row = connection.execute(
            select(
                polymarket_markets.c.condition_id,
                polymarket_markets.c.slug,
                polymarket_markets.c.start_at,
                polymarket_markets.c.end_at,
            )
            .where(
                polymarket_markets.c.horizon_seconds == 300,
                polymarket_markets.c.start_at >= started,
                polymarket_markets.c.end_at <= now,
            )
            .order_by(polymarket_markets.c.start_at)
            .limit(1)
        ).mappings().first()
    if row is not None:
        found = {
            "condition_id": str(row["condition_id"]),
            "slug": str(row["slug"]),
            "start_at": utc(row["start_at"]).isoformat(),
            "end_at": utc(row["end_at"]).isoformat(),
        }
        break
    time.sleep(10)
engine.dispose()
if found is None:
    raise SystemExit("no complete 5m market starting after rollout boundary in bounded wait")
print(json.dumps(found, sort_keys=True))
PY
    cat "$TARGET_FILE" >&2 || true
    fail "forward_5m_market_not_observed"
  fi

  mapfile -t target_fields < <("$REPO/.venv/bin/python" - "$TARGET_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
for key in ("condition_id", "start_at", "end_at"):
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise SystemExit(f"invalid forward target field: {key}")
    print(value)
PY
  )
  [[ ${#target_fields[@]} -eq 3 ]] || fail "invalid_forward_target_fields"
  TARGET_CONDITION_ID=${target_fields[0]}
  TARGET_START=${target_fields[1]}
  TARGET_END=${target_fields[2]}
}

generate_and_verify_v2_features() {
  FEATURE_STATS_FILE=$(mktemp /var/tmp/bp-phase14-v2-gate-a-feature-stats.XXXXXX.json)
  if ! sudo -u bp env PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" \
      "$REPO/scripts/generate_v2_features.py" \
      --start "$TARGET_START" \
      --end "$TARGET_END" \
      --env-file "$ENV_FILE" \
      --preserve-existing > "$FEATURE_STATS_FILE"; then
    cat "$FEATURE_STATS_FILE" >&2 || true
    fail "forward_v2_feature_generation_failed"
  fi
  "$REPO/.venv/bin/python" - "$FEATURE_STATS_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

stats = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if int(stats.get("targets_considered", 0)) != 1:
    raise SystemExit("completed-target window must contain exactly one V2 target")
if int(stats.get("planned_rows", 0)) != 4:
    raise SystemExit("completed target must plan exactly four V2 rows")
if int(stats.get("inserted", 0)) + int(stats.get("existing", 0)) != 4:
    raise SystemExit("completed target must materialize exactly four immutable V2 rows")
PY

  FEATURE_VERIFY_FILE=$(mktemp /var/tmp/bp-phase14-v2-gate-a-feature-verify.XXXXXX.json)
  if ! sudo -u bp env PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" - \
      "$ENV_FILE" "$TARGET_CONDITION_ID" "$TARGET_START" "$TARGET_END" \
      > "$FEATURE_VERIFY_FILE" <<'PY'; then
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime

from sqlalchemy import create_engine, select

from bp_engine.config import Settings
from bp_engine.storage.schema import market_features

V2 = "core-v2-last-trade"
EXPECTED_OFFSETS = {60, 120, 180, 240}


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def parse(value: object) -> datetime:
    if isinstance(value, datetime):
        return utc(value)
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)


env_file, target_condition_id, target_start_raw, target_end_raw = sys.argv[1:]
target_start = parse(target_start_raw)
target_end = parse(target_end_raw)
settings = Settings(_env_file=env_file)
engine = create_engine(settings.database_url)
with engine.connect() as connection:
    rows = list(
        connection.execute(
            select(market_features).where(
                market_features.c.feature_version == V2,
                market_features.c.condition_id == target_condition_id,
            )
        ).mappings()
    )
engine.dispose()
if len(rows) != 4:
    raise SystemExit(f"expected exactly four forward core-v2-last-trade rows, got {len(rows)}")
offsets = {int(row["feature_offset_seconds"]) for row in rows}
if offsets != EXPECTED_OFFSETS:
    raise SystemExit(f"unexpected fixed V2 offsets: {sorted(offsets)}")
violations = []
for row in rows:
    if int(row["horizon_seconds"]) != 300:
        violations.append(f"wrong_horizon:{row['condition_id']}")
    if utc(row["market_start_at"]) != target_start or utc(row["market_end_at"]) != target_end:
        violations.append(f"wrong_market_window:{row['condition_id']}")
    feature_at = utc(row["feature_at"])
    for name, raw in dict(row["source_cutoffs"] or {}).items():
        cutoff = parse(raw)
        if cutoff > feature_at:
            violations.append(f"future_source_cutoff:{row['condition_id']}:{name}")
if violations:
    raise SystemExit(";".join(violations))
print(json.dumps({
    "feature_version": V2,
    "condition_id": target_condition_id,
    "forward_row_count": len(rows),
    "offsets": sorted(offsets),
    "future_source_cutoff": 0,
}, sort_keys=True))
PY
    cat "$FEATURE_VERIFY_FILE" >&2 || true
    fail "forward_v2_feature_validation_failed"
  fi
}

run_coverage_report() {
  COVERAGE_FILE=$(mktemp /var/tmp/bp-phase14-v2-gate-a-coverage.XXXXXX.json)
  if ! sudo -u bp env PYTHONPATH="$REPO/src" "$REPO/.venv/bin/python" \
      "$REPO/scripts/report_v2_feature_coverage.py" \
      --env-file "$ENV_FILE" > "$COVERAGE_FILE"; then
    cat "$COVERAGE_FILE" >&2 || true
    fail "v2_coverage_report_failed"
  fi
  "$REPO/.venv/bin/python" - "$COVERAGE_FILE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
if report.get("policy_selected") is not False:
    raise SystemExit("coverage report selected policy")
if report.get("automatic_promotion") is not False:
    raise SystemExit("coverage report enabled automatic promotion")
if report.get("future_cutoff_violation_count") != 0:
    raise SystemExit("coverage report found future cutoffs")
if int(report.get("row_count", 0)) < 4:
    raise SystemExit("coverage report has fewer than four V2 rows")
PY
}

rollback_v2_gate_a_rollout() {
  set +e
  echo "Rolling back Phase 14 V2 Gate A runtime..." >&2
  git -C "$REPO" worktree add --detach "$ROLLBACK_WT" "$OLD_HEAD" >/dev/null 2>&1
  if [[ -d "$ROLLBACK_WT/.git" || -f "$ROLLBACK_WT/.git" ]]; then
    BP_CANDIDATE_ROOT="$ROLLBACK_WT" \
    BP_ENV_FILE="$ENV_FILE" \
    bash "$ROLLBACK_WT/scripts/deploy/phase14_prospective_runtime_install.sh" "$OLD_HEAD" >&2
  else
    git -C "$REPO" checkout --detach --force "$OLD_HEAD" >&2
  fi
  systemctl restart "$RECORDER_UNIT" >&2
  systemctl is-active --quiet "$RECORDER_UNIT" || true
  set -e
}

cleanup() {
  local rc=$?
  trap - EXIT
  set +e
  if (( rc != 0 && ROLLBACK_ARMED )); then
    rollback_v2_gate_a_rollout
  fi
  for file in \
    "$DISK_BEFORE" "$DISK_AFTER" "$SOAK_FILE" "$SNAPSHOT_FILE" \
    "$PROVENANCE_FILE" "$TARGET_FILE" "$FEATURE_STATS_FILE" \
    "$FEATURE_VERIFY_FILE" "$COVERAGE_FILE"; do
    [[ -n "$file" ]] && rm -f "$file"
  done
  git -C "$REPO" worktree remove --force "$WT" >/dev/null 2>&1 || true
  git -C "$REPO" worktree remove --force "$ROLLBACK_WT" >/dev/null 2>&1 || true
  set -e
  exit "$rc"
}
trap cleanup EXIT

[[ -d "$REPO/.git" ]] || fail "missing_opt_bp_repo"
[[ -r "$ENV_FILE" ]] || fail "missing_environment_file"
[[ -x "$REPO/.venv/bin/python" ]] || fail "missing_python_runtime"
id -u bp >/dev/null 2>&1 || fail "missing_bp_user"
validate_deployed_checkout

OLD_HEAD=$(git -C "$REPO" rev-parse HEAD)
[[ "$OLD_HEAD" == "$EXPECTED_FROM_HEAD" ]] || fail "unexpected_deployed_head:$OLD_HEAD"
[[ "$SHA" != "$OLD_HEAD" ]] || fail "candidate_already_deployed"
require_services_active
require_research_zero_money
verify_dashboard_safety

DISK_BEFORE=$(mktemp /var/tmp/bp-phase14-v2-gate-a-disk-before.XXXXXX.json)
run_disk_health "$DISK_BEFORE"

git -C /opt/bp fetch --no-tags origin \
  "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
FETCHED=$(git -C /opt/bp rev-parse "refs/remotes/origin/$BRANCH")
if [[ "$FETCHED" != "$SHA" ]]; then
  fail "candidate_head_changed"
fi
git -C "$REPO" merge-base --is-ancestor "$OLD_HEAD" "$SHA" || fail "candidate_not_descendant_of_deployed_head"
validate_rollout_scope

git -C "$REPO" worktree add --detach "$WT" "$SHA"
[[ "$(git -C "$WT" rev-parse HEAD)" == "$SHA" ]] || fail "candidate_worktree_head_mismatch"

ROLLOUT_STARTED=$(date -u +%Y-%m-%dT%H:%M:%SZ)
BP_CANDIDATE_ROOT="$WT" \
BP_ENV_FILE="$ENV_FILE" \
bash "$WT/scripts/deploy/phase14_prospective_runtime_install.sh" "$SHA"

[[ "$(git -C "$REPO" rev-parse HEAD)" == "$SHA" ]] || fail "deployed_head_mismatch_after_runtime_install"
validate_deployed_checkout
ROLLBACK_ARMED=1

systemctl restart "$RECORDER_UNIT"
for _ in $(seq 1 30); do
  systemctl is-active --quiet "$RECORDER_UNIT" && break
  sleep 1
done
systemctl is-active --quiet "$RECORDER_UNIT" || fail "recorder_not_active_after_restart"

sleep 10
require_services_active
require_research_zero_money
prove_real_last_trade_provenance
run_soak_gate
wait_for_forward_5m_market
generate_and_verify_v2_features
run_coverage_report
verify_dashboard_safety
require_services_active
require_research_zero_money

DISK_AFTER=$(mktemp /var/tmp/bp-phase14-v2-gate-a-disk-after.XXXXXX.json)
run_disk_health "$DISK_AFTER"
[[ "$(git -C "$REPO" rev-parse HEAD)" == "$SHA" ]] || fail "deployed_head_changed_after_rollout"
validate_deployed_checkout

ROLLBACK_ARMED=0
install -d -o bp -g bp /var/lib/bp/evidence
stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence_file="/var/lib/bp/evidence/phase14-v2-gate-a-rollout-$stamp.txt"
{
  echo "PHASE14_V2_GATE_A_ROLLOUT=PASS"
  echo "OLD_HEAD=$OLD_HEAD"
  echo "NEW_HEAD=$(git -C "$REPO" rev-parse HEAD)"
  echo "ROLLOUT_STARTED=$ROLLOUT_STARTED"
  echo "RECORDER_ACTIVE=$(systemctl is-active "$RECORDER_UNIT")"
  for service in "${CORE_SERVICES[@]}"; do
    echo "SERVICE=${service}:$(systemctl is-active "$service")"
  done
  echo "MODE=$MODE"
  echo "LIVE_TRADING_ENABLED=$LIVE_TRADING_ENABLED"
  echo "MAX_TRADE_SIZE_USD=$MAX_TRADE_SIZE_USD"
  echo "MAX_DAILY_LOSS_USD=$MAX_DAILY_LOSS_USD"
  echo "PROVENANCE_PROOF=$(tr -d '\n' < "$PROVENANCE_FILE")"
  echo "FORWARD_TARGET=$(tr -d '\n' < "$TARGET_FILE")"
  echo "FEATURE_GENERATION=$(tr -d '\n' < "$FEATURE_STATS_FILE")"
  echo "FEATURE_VALIDATION=$(tr -d '\n' < "$FEATURE_VERIFY_FILE")"
  echo "COVERAGE_REPORT=$(tr -d '\n' < "$COVERAGE_FILE")"
  echo "SOAK_REPORT=$(tr -d '\n' < "$SOAK_FILE")"
  echo "DISK_BEFORE=$(tr -d '\n' < "$DISK_BEFORE")"
  echo "DISK_AFTER=$(tr -d '\n' < "$DISK_AFTER")"
} | tee "$evidence_file"
chown bp:bp "$evidence_file"
chmod 0640 "$evidence_file"

echo "EVIDENCE_FILE=$evidence_file"
echo "PHASE14_V2_GATE_A_ROLLOUT=PASS"
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "PHASE14_V2_GATE_A_FROM_HEAD=$EXPECTED_FROM_HEAD"
echo "PHASE14_V2_GATE_A_HEAD=$EXPECTED_HEAD"
echo "Running exact-head research-only V2 Gate A rollout and forward acceptance..."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_V2_GATE_A_HEAD=$HEAD_Q PHASE14_V2_GATE_A_FROM_HEAD=$FROM_HEAD_Q PHASE14_V2_GATE_A_BRANCH=$BRANCH_Q PHASE14_V2_GATE_A_ENV_FILE=$ENV_FILE_Q PHASE14_V2_GATE_A_MAX_FORWARD_WAIT_SECONDS=$MAX_WAIT_Q bash"
