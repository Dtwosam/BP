#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_COMPACT_DEDUPE_READINESS_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_COMPACT_DEDUPE_READINESS_ZONE:-us-east1-c}"
VM="${PHASE14_V4_COMPACT_DEDUPE_READINESS_VM:-bp-recorder}"
ENV_FILE="${PHASE14_V4_COMPACT_DEDUPE_READINESS_ENV_FILE:-/etc/bp/bp.env}"
EXPECTED_DEPLOYED_HEAD='52b4355d6f077373b873f7a6f42bc37a20ddbc7b'

fail() {
  printf 'PHASE14_V4_COMPACT_DEDUPE_READINESS_PREFLIGHT=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"

git fetch origin main --quiet || fail "fetch_main_failed"
git switch main >/dev/null || fail "switch_main_failed"
git pull --ff-only origin main >/dev/null || fail "pull_main_failed"

LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git rev-parse origin/main)"
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "main_head_mismatch"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

REPORT="$ROOT/scripts/report_v4_compact_dedupe_readiness.py"
[[ -r "$REPORT" ]] || fail "report_script_missing"
REPORT_B64="$(base64 < "$REPORT" | tr -d '\n')"

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail
REPO=/opt/bp
ENV_FILE=/etc/bp/bp.env
EXPECTED_DEPLOYED_HEAD='52b4355d6f077373b873f7a6f42bc37a20ddbc7b'

fail() {
  echo "PHASE14_V4_COMPACT_DEDUPE_READINESS_PREFLIGHT=FAIL:$1" >&2
  exit 1
}

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
DEPLOYED_HEAD="$(git -C "$REPO" rev-parse HEAD)"
[[ "$DEPLOYED_HEAD" == "$EXPECTED_DEPLOYED_HEAD" ]] ||
  fail "unexpected_deployed_head"

for unit in   bp-postgres.service   bp-recorder.service   bp-v3-frozen-predictor.service   bp-v3-paper-execution.service   bp-dashboard-api.service   bp-dashboard-web.service   bp-paper-execution.service   bp-live-predictor.service   bp-prospective-outcomes.service
do
  systemctl is-active --quiet "$unit" || fail "service_not_active:$unit"
done

for timer in   bp-storage-maintenance.timer   bp-storage-disk-health.timer   bp-v2-forward-coverage.timer   bp-v4-forward-coverage.timer
do
  systemctl is-enabled --quiet "$timer" || fail "timer_not_enabled:$timer"
  systemctl is-active --quiet "$timer" || fail "timer_not_active:$timer"
done

read_env() {
  awk -F= -v key="$2" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$1"
}

[[ "$(read_env "$ENV_FILE" MODE)" == "research" ]] || fail "mode_not_research"
[[ "$(read_env "$ENV_FILE" LIVE_TRADING_ENABLED)" == "false" ]] ||
  fail "live_trading_enabled"
[[ "$(read_env "$ENV_FILE" MAX_TRADE_SIZE_USD)" == "0" ]] ||
  fail "max_trade_size_nonzero"
[[ "$(read_env "$ENV_FILE" MAX_DAILY_LOSS_USD)" == "0" ]] ||
  fail "max_daily_loss_nonzero"

"$REPO/.venv/bin/python" - "$REPO/PROJECT_STATE.json" <<'PY'
import json
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
values = []


def walk(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "automatic_promotion":
                values.append(item)
            walk(item)
    elif isinstance(value, list):
        for item in value:
            walk(item)


walk(payload)
if not values or any(value is not False for value in values):
    raise SystemExit("automatic_promotion must remain false")
PY

sudo -u bp env   -u RECORDER_QUEUE_MAXSIZE   -u RECORDER_BATCH_SIZE   -u RECORDER_WRITER_WORKERS   -u RECORDER_FLUSH_INTERVAL_SECONDS   "$REPO/.venv/bin/python" - "$ENV_FILE" <<'PY'
import sys
from bp_engine.config import Settings

settings = Settings(_env_file=sys.argv[1])
if settings.recorder_queue_maxsize != 50000:
    raise SystemExit(
        f"expected recorder queue maxsize 50000, got {settings.recorder_queue_maxsize}"
    )
if settings.recorder_batch_size != 500:
    raise SystemExit(
        f"expected recorder batch size 500, got {settings.recorder_batch_size}"
    )
if settings.recorder_writer_workers != 4:
    raise SystemExit(
        f"expected 4 recorder writer workers, got {settings.recorder_writer_workers}"
    )
if abs(settings.recorder_flush_interval_seconds - 0.25) > 1e-9:
    raise SystemExit(
        "expected recorder flush interval 0.25, got "
        f"{settings.recorder_flush_interval_seconds}"
    )
print(f"RECORDER_QUEUE_MAXSIZE={settings.recorder_queue_maxsize}")
print(f"RECORDER_BATCH_SIZE={settings.recorder_batch_size}")
print(f"RECORDER_WRITER_WORKERS={settings.recorder_writer_workers}")
print(
    "RECORDER_FLUSH_INTERVAL_SECONDS="
    f"{settings.recorder_flush_interval_seconds}"
)
PY

tmp="$(mktemp -d /tmp/bp-v4-compact-dedupe-readiness.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT
chmod 0755 "$tmp"
printf '%s' '__REPORT_B64__' | base64 -d   > "$tmp/report_v4_compact_dedupe_readiness.py"
chmod 0644 "$tmp/report_v4_compact_dedupe_readiness.py"

printf 'DEPLOYED_HEAD=%s\n' "$DEPLOYED_HEAD"
printf 'DEPLOYED_WRITER_COMPACT_COMPATIBLE=false\n'
printf 'MIGRATION_REQUIRES_RUNTIME_WRITER_CUTOVER=true\n'

timeout --signal=TERM --kill-after=5s 900s   sudo -u bp env     PYTHONDONTWRITEBYTECODE=1     PYTHONPATH="$REPO/src"     "$REPO/.venv/bin/python"     "$tmp/report_v4_compact_dedupe_readiness.py"     --env-file "$ENV_FILE"

printf 'PHASE14_V4_COMPACT_DEDUPE_READINESS_PREFLIGHT=PASS\n'
printf 'REPORT_READ_ONLY=true\n'
printf 'PRODUCTION_MUTATION=false\n'
REMOTE

REMOTE_SCRIPT="${REMOTE_SCRIPT/__REPORT_B64__/$REPORT_B64}"
REMOTE_B64="$(printf '%s' "$REMOTE_SCRIPT" | base64 | tr -d '\n')"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'EXPECTED_DEPLOYED_HEAD=%s\n' "$EXPECTED_DEPLOYED_HEAD"
printf 'REPORT_READ_ONLY=true\n'
printf 'REPORT_PURPOSE=compact_dedupe_digest_index_readiness\n'
printf 'MIGRATION_REQUIRES_RUNTIME_WRITER_CUTOVER=true\n'

gcloud compute ssh "$VM"   --project="$PROJECT"   --zone="$ZONE"   --quiet   --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
