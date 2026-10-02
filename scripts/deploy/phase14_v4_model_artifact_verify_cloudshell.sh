#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_MODEL_VERIFY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_MODEL_VERIFY_ZONE:-us-east1-c}"
VM="${PHASE14_V4_MODEL_VERIFY_VM:-bp-recorder}"
EXPECTED_SHA256="6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"
EXPECTED_SIZE_BYTES=230132

fail() {
  printf 'PHASE14_V4_MODEL_ARTIFACT_VERIFY=FAIL:%s\n' "$1" >&2
  exit 1
}

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"

git fetch origin main --quiet || fail "fetch_main_failed"
LOCAL_HEAD="$(git rev-parse HEAD)"
REMOTE_MAIN="$(git rev-parse origin/main)"
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

REMOTE_SCRIPT=$(cat <<'REMOTE'
set -Eeuo pipefail
EXPECTED_SHA256="6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"
EXPECTED_SIZE_BYTES=230132

fail() {
  printf 'PHASE14_V4_MODEL_ARTIFACT_VERIFY=FAIL:%s\n' "$1" >&2
  exit 1
}

roots=()
for root in /var/lib/bp/evidence /var/lib/bp/runtime /opt/bp; do
  [[ -d "$root" ]] && roots+=("$root")
done
(( ${#roots[@]} > 0 )) || fail "canonical_roots_missing"

matches=()
while IFS= read -r -d '' path; do
  size="$(stat -c '%s' "$path" 2>/dev/null || true)"
  [[ "$size" == "$EXPECTED_SIZE_BYTES" ]] || continue
  digest="$(sha256sum "$path" | awk '{print $1}')"
  [[ "$digest" == "$EXPECTED_SHA256" ]] || continue
  matches+=("$path")
done < <(find "${roots[@]}" -xdev -type f -size "${EXPECTED_SIZE_BYTES}c" -print0 2>/dev/null)

(( ${#matches[@]} > 0 )) || fail "frozen_v4_model_artifact_not_found"

mapfile -t unique_matches < <(printf '%s\n' "${matches[@]}" | sort -u)
(( ${#unique_matches[@]} == 1 )) || {
  printf 'MATCH_COUNT=%s\n' "${#unique_matches[@]}" >&2
  printf 'MATCH=%s\n' "${unique_matches[@]}" >&2
  fail "frozen_v4_model_artifact_not_unique"
}

MODEL_PATH="${unique_matches[0]}"
printf 'MODEL_PATH=%s\n' "$MODEL_PATH"
printf 'MODEL_SHA256=%s\n' "$EXPECTED_SHA256"
printf 'MODEL_SIZE_BYTES=%s\n' "$EXPECTED_SIZE_BYTES"
printf 'MODEL_DESERIALIZED=false\n'
printf 'HOLDOUT_LABELS_READ=false\n'
printf 'DATABASE_ACCESS=none\n'
printf 'DATABASE_WRITES_PERFORMED=false\n'
printf 'SERVICE_MUTATION_PERFORMED=false\n'
printf 'ORDER_SUBMISSION_PERFORMED=false\n'
printf 'PHASE14_V4_MODEL_ARTIFACT_VERIFY=PASS\n'
REMOTE
)

REMOTE_B64="$(printf '%s' "$REMOTE_SCRIPT" | base64 | tr -d '\n')"

printf 'PROJECT=%s\n' "$PROJECT"
printf 'VM=%s\n' "$VM"
printf 'ZONE=%s\n' "$ZONE"
printf 'CONTROL_MAIN=%s\n' "$LOCAL_HEAD"
printf 'EXPECTED_MODEL_SHA256=%s\n' "$EXPECTED_SHA256"
printf 'EXPECTED_MODEL_SIZE_BYTES=%s\n' "$EXPECTED_SIZE_BYTES"
printf 'VERIFY_READ_ONLY=true\n'

gcloud compute ssh "$VM" \
  --project="$PROJECT" \
  --zone="$ZONE" \
  --quiet \
  --command="printf '%s' '$REMOTE_B64' | base64 -d | sudo bash"
