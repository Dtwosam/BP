#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_FEATURE_LATENCY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_FEATURE_LATENCY_ZONE:-us-east1-c}"
VM="${PHASE14_V4_FEATURE_LATENCY_VM:-bp-recorder}"

EXPECTED_SHADOW_UNIT='bp-v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f.service'
EXPECTED_RELEASE='/var/lib/bp/runtime/v4-source-time-fresh-book-shadow-d58b2cbedc3f00617b8d4611e538981b2865e369'
EXPECTED_VENV='/var/lib/bp/runtime/v4-paper-venv-d58b2cbedc3f00617b8d4611e538981b2865e369'
EXPECTED_MODEL_SHA256='6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf'
CONDITION_2104='0x1ac81ed6b7ee9b6c336cd537bee971851dacf908c0ded1e6bd5db5cc65549b01'
CONDITION_2109='0xeb3058016b76daebf09c032462bd2599104ae61a64d1f08f92d9ac5cb8f2543e'

fail() {
  printf 'PHASE14_V4_FEATURE_INFERENCE_LATENCY_STATUS=FAIL:%s\n' "$1" >&2
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

REPORT="$ROOT/scripts/report_v4_feature_inference_latency.py"
[[ -r "$REPORT" ]] || fail "report_script_missing"
REPORT_B64="$(base64 < "$REPORT" | tr -d '\n')"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'REPORT_READ_ONLY=true\n'

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="set -Eeuo pipefail
UNIT='$EXPECTED_SHADOW_UNIT'
RELEASE='$EXPECTED_RELEASE'
VENV='$EXPECTED_VENV'
MODEL="\$RELEASE/frozen-v4-model.joblib"

systemctl is-active --quiet \"\$UNIT\" || {
  echo PHASE14_V4_FEATURE_INFERENCE_LATENCY_STATUS=FAIL:shadow_not_active >&2
  exit 1
}
systemctl is-active --quiet bp-recorder.service || {
  echo PHASE14_V4_FEATURE_INFERENCE_LATENCY_STATUS=FAIL:recorder_not_active >&2
  exit 1
}
if systemctl is-active --quiet bp-phase15-fast-live-source.service; then
  echo PHASE14_V4_FEATURE_INFERENCE_LATENCY_STATUS=FAIL:fast_live_source_active >&2
  exit 1
fi
test -x \"\$VENV/bin/python\" || {
  echo PHASE14_V4_FEATURE_INFERENCE_LATENCY_STATUS=FAIL:venv_missing >&2
  exit 1
}
test -r \"\$MODEL\" || {
  echo PHASE14_V4_FEATURE_INFERENCE_LATENCY_STATUS=FAIL:model_missing >&2
  exit 1
}
[[ \"\$(sha256sum \"\$MODEL\" | awk '{print \$1}')\" == '$EXPECTED_MODEL_SHA256' ]] || {
  echo PHASE14_V4_FEATURE_INFERENCE_LATENCY_STATUS=FAIL:model_sha_mismatch >&2
  exit 1
}

printf '%s' '$REPORT_B64' | base64 -d | sudo -u bp env \
  PYTHONDONTWRITEBYTECODE=1 \
  PYTHONPATH=\"\$RELEASE/src\" \
  \"\$VENV/bin/python\" - \
  --env-file /etc/bp/bp.env \
  --model-path \"\$MODEL\" \
  --condition-id '$CONDITION_2104' \
  --condition-id '$CONDITION_2109'
"
