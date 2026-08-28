#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE10_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE10_ZONE:-us-east1-c}"
VM="${PHASE10_VM:-bp-recorder}"
BRANCH="${PHASE10_BRANCH:-build/phase-10-live-prediction-engine}"
EXPECTED_HEAD="${PHASE10_HEAD:-}"

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "PHASE10_HEAD must be set to the verified candidate SHA" >&2
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

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "PHASE10_HEAD=$EXPECTED_HEAD"

REMOTE_SCRIPT=$(cat <<REMOTE
set -Eeuo pipefail
SHA='$EXPECTED_HEAD'
BRANCH='$BRANCH'
SHORT=\${SHA:0:12}
RUNTIME_ROOT=/var/lib/bp/phase10-runtime
LOG=/var/lib/bp/evidence/phase10-host-acceptance-latest.log
UNIT="bp-phase10-host-acceptance-\$SHORT"
UNIT_NAME="\$UNIT.service"

show_result() {
  echo "UNIT=\$UNIT_NAME"
  systemctl show "\$UNIT_NAME" -p ActiveState -p SubState -p ExecMainStatus --no-pager || true
  echo "LOG=\$LOG"
  cat "\$LOG" 2>/dev/null || true
}

if systemctl show "\$UNIT_NAME" -p LoadState --value 2>/dev/null | grep -qv '^not-found$'; then
  ACTIVE=\$(systemctl show "\$UNIT_NAME" -p ActiveState --value 2>/dev/null || true)
  SUB=\$(systemctl show "\$UNIT_NAME" -p SubState --value 2>/dev/null || true)
  if [[ "\$ACTIVE" == "activating" || "\$SUB" == "start" || "\$SUB" == "running" ]]; then
    echo "Existing Phase 10 acceptance job is still running; reattaching."
  elif grep -q '^PHASE10_HOST_ACCEPTANCE=PASS$' "\$LOG" 2>/dev/null; then
    show_result
    exit 0
  elif grep -Eq '^PHASE10_HOST_ACCEPTANCE=(PENDING|FAIL)$' "\$LOG" 2>/dev/null; then
    show_result
    exit 7
  else
    systemctl stop "\$UNIT_NAME" >/dev/null 2>&1 || true
    systemctl reset-failed "\$UNIT_NAME" >/dev/null 2>&1 || true
  fi
fi

ACTIVE=\$(systemctl show "\$UNIT_NAME" -p ActiveState --value 2>/dev/null || true)
SUB=\$(systemctl show "\$UNIT_NAME" -p SubState --value 2>/dev/null || true)
if [[ "\$ACTIVE" != "activating" && "\$SUB" != "start" && "\$SUB" != "running" ]]; then
  WT="/var/tmp/bp-phase10-wt-\$SHORT-\$\$"
  SRC="\$RUNTIME_ROOT/bp-phase10-src-\$SHORT-\$\$"
  JOB="\$RUNTIME_ROOT/bp-phase10-host-acceptance-\$SHORT-\$\$.sh"

  git -C /opt/bp fetch --no-tags origin \
    "refs/heads/\$BRANCH:refs/remotes/origin/\$BRANCH"
  FETCHED=\$(git -C /opt/bp rev-parse "refs/remotes/origin/\$BRANCH")
  if [[ "\$FETCHED" != "\$SHA" ]]; then
    echo "PHASE10_HOST_ACCEPTANCE=FAIL"
    echo "REASON=candidate_head_changed"
    echo "EXPECTED_HEAD=\$SHA"
    echo "FETCHED_HEAD=\$FETCHED"
    exit 2
  fi

  git -C /opt/bp worktree prune
  git -C /opt/bp worktree add --detach "\$WT" "\$SHA"
  WORKTREE_HEAD=\$(git -C "\$WT" rev-parse HEAD)
  if [[ "\$WORKTREE_HEAD" != "\$SHA" ]]; then
    echo "PHASE10_HOST_ACCEPTANCE=FAIL"
    echo "REASON=worktree_head_mismatch"
    echo "EXPECTED_HEAD=\$SHA"
    echo "WORKTREE_HEAD=\$WORKTREE_HEAD"
    git -C /opt/bp worktree remove --force "\$WT" >/dev/null 2>&1 || true
    exit 2
  fi

  install -d -o bp -g bp "\$RUNTIME_ROOT" "\$SRC"
  git -C "\$WT" archive --format=tar "\$SHA" | sudo -u bp tar -xf - -C "\$SRC"
  git -C /opt/bp worktree remove --force "\$WT"

  cat > "\$JOB" <<JOB
#!/usr/bin/env bash
set -Eeuo pipefail
SRC='\$SRC'
SHA='\$SHA'
LOG='\$LOG'
cleanup() {
  rm -rf "\$SRC" >/dev/null 2>&1 || true
  rm -f "\$JOB" >/dev/null 2>&1 || true
}
trap cleanup EXIT
set +e
BP_REPO="\$SRC" BP_VERIFIED_HEAD="\$WORKTREE_HEAD" bash \\
  "\$SRC/scripts/deploy/phase10_host_acceptance.sh" "\$SHA" \\
  >"\$LOG" 2>&1
RC=\\\$?
set -e
if [[ "\\\$RC" -ne 0 ]]; then
  echo "PHASE10_WRAPPER_RC=\\\$RC" >>"\$LOG"
fi
exit "\\\$RC"
JOB
  chmod 700 "\$JOB"
  : > "\$LOG"

  systemd-run \
    --no-block \
    --unit="\$UNIT" \
    --property=Type=oneshot \
    --property=RemainAfterExit=yes \
    --property=StandardOutput=journal \
    --property=StandardError=journal \
    "\$JOB"
  echo "Started disconnect-resilient Phase 10 acceptance job \$UNIT_NAME."
fi

while true; do
  ACTIVE=\$(systemctl show "\$UNIT_NAME" -p ActiveState --value 2>/dev/null || true)
  SUB=\$(systemctl show "\$UNIT_NAME" -p SubState --value 2>/dev/null || true)
  if [[ "\$ACTIVE" != "activating" && "\$SUB" != "start" && "\$SUB" != "running" ]]; then
    break
  fi
  sleep 10
done

show_result
RC=\$(systemctl show "\$UNIT_NAME" -p ExecMainStatus --value 2>/dev/null || echo 1)
if [[ "\$RC" != "0" ]]; then
  echo "--- UNIT JOURNAL ---"
  journalctl -u "\$UNIT_NAME" -n 80 --no-pager || true
  exit "\$RC"
fi
if ! grep -q '^PHASE10_HOST_ACCEPTANCE=PASS$' "\$LOG"; then
  echo "PHASE10_HOST_ACCEPTANCE=FAIL"
  echo "REASON=missing_pass_token"
  exit 8
fi

echo "PHASE10_HOST_ACCEPTANCE=PASS"
REMOTE
)
REMOTE_B64=$(printf '%s' "$REMOTE_SCRIPT" | base64 -w0)

echo "Connecting to $VM for Phase 10 host acceptance..."
gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"