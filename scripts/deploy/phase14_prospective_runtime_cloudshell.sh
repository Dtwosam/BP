#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_PROSPECTIVE_RUNTIME_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_PROSPECTIVE_RUNTIME_ZONE:-us-east1-c}"
VM="${PHASE14_PROSPECTIVE_RUNTIME_VM:-bp-recorder}"
BRANCH="${PHASE14_PROSPECTIVE_RUNTIME_BRANCH:-phase14-prospective-outcome-sync}"
EXPECTED_HEAD="${PHASE14_PROSPECTIVE_RUNTIME_HEAD:-}"
ENV_FILE="${PHASE14_PROSPECTIVE_RUNTIME_ENV_FILE:-/etc/bp/bp.env}"

if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_PROSPECTIVE_RUNTIME_HEAD must be the exact 40-character verified candidate SHA" >&2
  exit 2
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PHASE14_PROSPECTIVE_RUNTIME_BRANCH contains unsupported characters" >&2
  exit 2
fi
if [[ "$ENV_FILE" != /* ]]; then
  echo "PHASE14_PROSPECTIVE_RUNTIME_ENV_FILE must be an absolute path" >&2
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
printf -v BRANCH_Q '%q' "$BRANCH"
printf -v ENV_FILE_Q '%q' "$ENV_FILE"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail

SHA="${PHASE14_PROSPECTIVE_RUNTIME_HEAD:?}"
BRANCH="${PHASE14_PROSPECTIVE_RUNTIME_BRANCH:?}"
ENV_FILE="${PHASE14_PROSPECTIVE_RUNTIME_ENV_FILE:?}"
REPO=/opt/bp
SHORT="${SHA:0:12}"
WT="/var/tmp/bp-phase14-prospective-runtime-${SHORT}-$$"
PREDICTOR_UNIT=bp-live-predictor.service
OUTCOME_UNIT=bp-prospective-outcomes.service
CORE_SERVICES=(
  bp-recorder.service
  bp-postgres.service
  bp-dashboard-api.service
  bp-dashboard-web.service
  bp-paper-execution.service
)

fail() {
  echo "PHASE14_PROSPECTIVE_RUNTIME_INSTALL=FAIL"
  echo "REASON=$1"
  exit 1
}

cleanup() {
  git -C /opt/bp worktree remove --force "$WT" >/dev/null 2>&1 || true
}
trap cleanup EXIT

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
  done < <(git -C /opt/bp status --porcelain --untracked-files=all)
}

[[ -d "$REPO/.git" ]] || fail "missing_opt_bp_repo"
[[ -r "$ENV_FILE" ]] || fail "missing_environment_file"
[[ -x "$REPO/.venv/bin/python" ]] || fail "missing_python_runtime"
id -u bp >/dev/null 2>&1 || fail "missing_bp_user"
validate_deployed_checkout

for service in "${CORE_SERVICES[@]}"; do
  systemctl is-active --quiet "$service" || fail "core_service_not_active_before:$service"
done

MODE=$(read_env MODE)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$MODE" != "research" || \
      "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || \
      "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  fail "research_zero_money_boundary_not_satisfied"
fi

git -C /opt/bp fetch --no-tags origin \
  "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
FETCHED=$(git -C /opt/bp rev-parse "refs/remotes/origin/$BRANCH")
if [[ "$FETCHED" != "$SHA" ]]; then
  fail "candidate_head_changed"
fi

git -C /opt/bp worktree add --detach "$WT" "$SHA"
WORKTREE_HEAD=$(git -C "$WT" rev-parse HEAD)
[[ "$WORKTREE_HEAD" == "$SHA" ]] || fail "candidate_worktree_head_mismatch"

BP_CANDIDATE_ROOT="$WT" \
BP_ENV_FILE="$ENV_FILE" \
bash "$WT/scripts/deploy/phase14_prospective_runtime_install.sh" "$SHA"

DEPLOYED_HEAD=$(git -C /opt/bp rev-parse HEAD)
[[ "$DEPLOYED_HEAD" == "$SHA" ]] || fail "deployed_head_mismatch_after_install"
validate_deployed_checkout

for service in "${CORE_SERVICES[@]}" "$PREDICTOR_UNIT" "$OUTCOME_UNIT"; do
  systemctl is-active --quiet "$service" || fail "service_not_active_after:$service"
done
for service in "$PREDICTOR_UNIT" "$OUTCOME_UNIT"; do
  systemctl is-enabled --quiet "$service" || fail "service_not_enabled_after:$service"
done

MODE=$(read_env MODE)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$MODE" != "research" || \
      "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || \
      "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  fail "research_zero_money_boundary_changed_after_install"
fi

echo "PHASE14_PROSPECTIVE_RUNTIME_HEAD=$SHA"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "PREDICTOR_SERVICE=$(systemctl is-active "$PREDICTOR_UNIT")"
echo "PREDICTOR_ENABLED=$(systemctl is-enabled "$PREDICTOR_UNIT")"
echo "OUTCOME_SERVICE=$(systemctl is-active "$OUTCOME_UNIT")"
echo "OUTCOME_ENABLED=$(systemctl is-enabled "$OUTCOME_UNIT")"
for service in "${CORE_SERVICES[@]}"; do
  echo "CORE_SERVICE=${service}:$(systemctl is-active "$service")"
done
echo "MODE=$MODE"
echo "LIVE_TRADING_ENABLED=$LIVE_TRADING_ENABLED"
echo "MAX_TRADE_SIZE_USD=$MAX_TRADE_SIZE_USD"
echo "MAX_DAILY_LOSS_USD=$MAX_DAILY_LOSS_USD"
echo "PHASE14_PROSPECTIVE_RUNTIME_INSTALL=PASS"
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "PHASE14_PROSPECTIVE_RUNTIME_HEAD=$EXPECTED_HEAD"
echo "Running exact-head research-only prospective runtime installation..."

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo env PHASE14_PROSPECTIVE_RUNTIME_HEAD=$HEAD_Q PHASE14_PROSPECTIVE_RUNTIME_BRANCH=$BRANCH_Q PHASE14_PROSPECTIVE_RUNTIME_ENV_FILE=$ENV_FILE_Q bash"
