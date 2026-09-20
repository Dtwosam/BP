#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V3_PAPER_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V3_PAPER_ZONE:-us-east1-c}"
VM="${PHASE14_V3_PAPER_VM:-bp-recorder}"
BRANCH="${PHASE14_V3_PAPER_BRANCH:-main}"
EXPECTED_HEAD="${PHASE14_V3_PAPER_HEAD:-}"
ENV_FILE="${PHASE14_V3_PAPER_ENV_FILE:-/etc/bp/bp.env}"
MODEL_PATH="${PHASE14_V3_FROZEN_MODEL_PATH:-}"

if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_V3_PAPER_HEAD must be the exact 40-character verified main SHA" >&2
  exit 2
fi
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PHASE14_V3_PAPER_BRANCH contains unsupported characters" >&2
  exit 2
fi
if [[ "$ENV_FILE" != /* ]]; then
  echo "PHASE14_V3_PAPER_ENV_FILE must be an absolute path" >&2
  exit 2
fi
if [[ -n "$MODEL_PATH" && "$MODEL_PATH" != /* ]]; then
  echo "PHASE14_V3_FROZEN_MODEL_PATH must be an absolute remote path" >&2
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
printf -v MODEL_PATH_Q '%q' "$MODEL_PATH"

read -r -d '' REMOTE_SCRIPT <<'REMOTE' || true
set -Eeuo pipefail

SHA="${PHASE14_V3_PAPER_HEAD:?}"
BRANCH="${PHASE14_V3_PAPER_BRANCH:?}"
ENV_FILE="${PHASE14_V3_PAPER_ENV_FILE:?}"
MODEL_PATH="${PHASE14_V3_FROZEN_MODEL_PATH:-}"
REPO=/opt/bp
INSTALLER_PATH=scripts/deploy/phase14_v3_frozen_paper_install.sh

fail() {
  echo "PHASE14_V3_PAPER_CLOUDSHELL=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

git_repo() {
  git -c safe.directory="$REPO" -C "$REPO" "$@"
}

[[ -d "$REPO/.git" ]] || fail "deployed_repo_missing"
[[ -x "$REPO/.venv/bin/python" ]] || fail "production_python_missing"

DEPLOYED_HEAD_BEFORE=$(git_repo rev-parse HEAD)
git_repo fetch --quiet origin "refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
REMOTE_HEAD=$(git_repo rev-parse "refs/remotes/origin/$BRANCH")
[[ "$REMOTE_HEAD" == "$SHA" ]] || fail "remote_branch_head_mismatch:$REMOTE_HEAD"
git_repo cat-file -e "$SHA^{commit}" || fail "candidate_commit_missing"
git_repo cat-file -e "$SHA:$INSTALLER_PATH" || fail "installer_missing_at_candidate"

INSTALLER=$(mktemp /var/tmp/bp-v3-paper-launch.XXXXXX.sh)
cleanup() {
  rm -f "$INSTALLER"
}
trap cleanup EXIT

git_repo show "$SHA:$INSTALLER_PATH" > "$INSTALLER"
chmod 0755 "$INSTALLER"

if [[ -n "$MODEL_PATH" ]]; then
  PHASE14_V3_FROZEN_MODEL_PATH="$MODEL_PATH"   PHASE14_V3_PAPER_HEAD="$SHA"   PHASE14_V3_PAPER_BRANCH="$BRANCH"   PHASE14_V3_PAPER_ENV_FILE="$ENV_FILE"     bash "$INSTALLER"
else
  PHASE14_V3_PAPER_HEAD="$SHA"   PHASE14_V3_PAPER_BRANCH="$BRANCH"   PHASE14_V3_PAPER_ENV_FILE="$ENV_FILE"     bash "$INSTALLER"
fi

DEPLOYED_HEAD_AFTER=$(git_repo rev-parse HEAD)
[[ "$DEPLOYED_HEAD_AFTER" == "$DEPLOYED_HEAD_BEFORE" ]]   || fail "deployed_checkout_changed_by_launcher"

echo "PHASE14_V3_PAPER_CLOUDSHELL=PASS"
echo "CANDIDATE_HEAD=$SHA"
echo "DEPLOYED_CHECKOUT_HEAD=$DEPLOYED_HEAD_AFTER"
REMOTE

gcloud compute ssh "$VM"   --project "$PROJECT"   --zone "$ZONE"   --command "sudo env PHASE14_V3_PAPER_HEAD=$HEAD_Q PHASE14_V3_PAPER_BRANCH=$BRANCH_Q PHASE14_V3_PAPER_ENV_FILE=$ENV_FILE_Q PHASE14_V3_FROZEN_MODEL_PATH=$MODEL_PATH_Q bash -s"   <<< "$REMOTE_SCRIPT"
