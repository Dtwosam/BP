#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_ZONE:-us-east1-c}"
VM="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_VM:-bp-recorder}"
HELPER_HEAD="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_HELPER_HEAD:-}"
DEPLOYED_HEAD="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_DEPLOYED_HEAD:-}"
RUNTIME_BRANCH="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_RUNTIME_BRANCH:-ops/phase14-v4-compact-dedupe-steady-runtime}"
READINESS_EVIDENCE="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_READINESS_EVIDENCE:-}"
READINESS_SHA256="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_READINESS_SHA256:-}"
BLOCKER_EVIDENCE="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_BLOCKER_EVIDENCE:-}"
BLOCKER_SHA256="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_BLOCKER_SHA256:-}"
APPROVAL="${PHASE14_V4_COMPACT_DEDUPE_MIGRATION_APPROVAL:-}"

EVIDENCE_CONTROL_MAIN="df2987bb07472675b9d1efeb0e35c19c48d75c3a"
EVIDENCE_DEPLOYED_HEAD="52b4355d6f077373b873f7a6f42bc37a20ddbc7b"
EVIDENCE_WRITER_CANDIDATE="f160e8823cd2232adbd25dfc94de91bcec98219a"

fail() {
  echo "PHASE14_V4_COMPACT_DEDUPE_MIGRATION_GATE=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$HELPER_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "helper_head_invalid"
[[ "$DEPLOYED_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "deployed_head_invalid"
[[ "$READINESS_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "readiness_sha256_invalid"
[[ "$BLOCKER_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "blocker_sha256_invalid"
[[ "$RUNTIME_BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || fail "runtime_branch_invalid"
[[ "$READINESS_EVIDENCE" == /* ]] || fail "readiness_evidence_not_absolute"
[[ "$BLOCKER_EVIDENCE" == /* ]] || fail "blocker_evidence_not_absolute"
[[ -r "$READINESS_EVIDENCE" ]] || fail "readiness_evidence_missing"
[[ -r "$BLOCKER_EVIDENCE" ]] || fail "blocker_evidence_missing"

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ "$(git rev-parse HEAD)" == "$HELPER_HEAD" ]] || fail "local_helper_head_mismatch"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] || fail "local_working_tree_dirty"

REMOTE_MAIN="$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')"
[[ "$REMOTE_MAIN" == "$HELPER_HEAD" ]] || fail "remote_main_changed"

REMOTE_RUNTIME="$(
  git ls-remote --heads origin "refs/heads/$RUNTIME_BRANCH" |
    awk 'NR==1 {print $1}'
)"
[[ -n "$REMOTE_RUNTIME" ]] || fail "runtime_branch_missing"
[[ "$REMOTE_RUNTIME" == "$DEPLOYED_HEAD" ]] || fail "runtime_branch_head_changed"

ACTUAL_READINESS_SHA256="$(sha256sum "$READINESS_EVIDENCE" | awk '{print $1}')"
ACTUAL_BLOCKER_SHA256="$(sha256sum "$BLOCKER_EVIDENCE" | awk '{print $1}')"
[[ "$ACTUAL_READINESS_SHA256" == "$READINESS_SHA256" ]] ||
  fail "readiness_evidence_sha256_mismatch"
[[ "$ACTUAL_BLOCKER_SHA256" == "$BLOCKER_SHA256" ]] ||
  fail "blocker_evidence_sha256_mismatch"

"$ROOT/.venv/bin/python" -   "$READINESS_EVIDENCE"   "$BLOCKER_EVIDENCE"   "$EVIDENCE_CONTROL_MAIN"   "$EVIDENCE_DEPLOYED_HEAD"   "$EVIDENCE_WRITER_CANDIDATE" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

readiness_path = Path(sys.argv[1])
blocker_path = Path(sys.argv[2])
control_main = sys.argv[3]
evidence_deployed = sys.argv[4]
writer_candidate = sys.argv[5]


def extract_json(text_value: str) -> dict[str, object]:
    decoder = json.JSONDecoder()
    cursor = 0
    while True:
        cursor = text_value.find("{", cursor)
        if cursor < 0:
            raise SystemExit("evidence JSON object not found")
        try:
            value, _ = decoder.raw_decode(text_value[cursor:])
        except json.JSONDecodeError:
            cursor += 1
            continue
        if isinstance(value, dict):
            return value
        cursor += 1


def require_marker(text_value: str, marker: str) -> None:
    if marker not in text_value:
        raise SystemExit(f"required evidence marker missing: {marker}")


readiness_text = readiness_path.read_text(encoding="utf-8")
readiness = extract_json(readiness_text)

for marker in (
    f"CONTROL_MAIN={control_main}",
    f"EXPECTED_DEPLOYED_HEAD={evidence_deployed}",
    f"DEPLOYED_HEAD={evidence_deployed}",
    f"RUNTIME_CANDIDATE={writer_candidate}",
    "REPORT_READ_ONLY=true",
    "PRODUCTION_MUTATION=false",
):
    require_marker(readiness_text, marker)

signals = dict(readiness.get("signals") or {})
for key in (
    "canonical_keys_only",
    "current_primary_contract_ok",
    "no_compact_indexes_present",
    "no_invalid_indexes",
    "no_prepared_transactions",
    "transient_headroom_ok",
):
    if signals.get(key) is not True:
        raise SystemExit(f"readiness evidence signal is not true: {key}")

safety = dict(readiness.get("safety") or {})
if safety.get("database_read_only_required") is not True:
    raise SystemExit("readiness evidence is not marked database-read-only")
for key in (
    "database_writes_performed",
    "service_mutation_performed",
    "order_submission_performed",
    "raw_dedupe_keys_emitted",
):
    if safety.get(key) is not False:
        raise SystemExit(f"readiness safety signal is not false: {key}")

blocker_text = blocker_path.read_text(encoding="utf-8")
blocker = extract_json(blocker_text)
for marker in (
    f"CONTROL_MAIN={control_main}",
    f"EXPECTED_DEPLOYED_HEAD={evidence_deployed}",
    f"DEPLOYED_HEAD={evidence_deployed}",
    "REPORT_READ_ONLY=true",
    "PRODUCTION_MUTATION=false",
):
    require_marker(blocker_text, marker)

pid_summary = dict(blocker.get("pid_summary") or {})
if list(pid_summary.get("persistent_pids") or []):
    raise SystemExit("blocker evidence has persistent PIDs")
if list(pid_summary.get("appeared_pids") or []):
    raise SystemExit("blocker evidence has end-of-window appeared PIDs")

blocker_signals = dict(blocker.get("signals") or {})
expected_blocker_signals = {
    "persistent_long_transaction_count": 0,
    "persistent_writer_like_count": 0,
    "writer_quiesce_likely_required_for_bounded_reindex": False,
}
for key, expected in expected_blocker_signals.items():
    if blocker_signals.get(key) != expected:
        raise SystemExit(
            f"blocker evidence signal mismatch: {key}="
            f"{blocker_signals.get(key)!r}"
        )

blocker_safety = dict(blocker.get("safety") or {})
if blocker_safety.get("database_read_only_required") is not True:
    raise SystemExit("blocker evidence is not marked database-read-only")
for key in (
    "database_writes_performed",
    "service_mutation_performed",
    "order_submission_performed",
    "raw_query_text_emitted",
):
    if blocker_safety.get(key) is not False:
        raise SystemExit(f"blocker safety signal is not false: {key}")

print("LOCAL_EVIDENCE_VALIDATION=PASS")
PY

EXPECTED_APPROVAL="I_APPROVE_PHASE14_V4_COMPACT_DEDUPE_MIGRATION:$HELPER_HEAD:$DEPLOYED_HEAD:$READINESS_SHA256:$BLOCKER_SHA256"
[[ "$APPROVAL" == "$EXPECTED_APPROVAL" ]] || fail "production_approval_mismatch"

command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

FILES=(
  scripts/run_v4_compact_dedupe_migration.py
  scripts/report_v4_dedupe_reindex_blockers.py
)
PAYLOAD=""
for path in "${FILES[@]}"; do
  [[ -r "$ROOT/$path" ]] || fail "required_script_missing:$path"
  encoded="$(base64 < "$ROOT/$path" | tr -d '\n')"
  PAYLOAD+="$path:$encoded"$'\n'
done
PAYLOAD_B64="$(printf '%s' "$PAYLOAD" | base64 | tr -d '\n')"

printf -v DEPLOYED_Q '%q' "$DEPLOYED_HEAD"
printf -v HELPER_Q '%q' "$HELPER_HEAD"
printf -v READINESS_SHA_Q '%q' "$READINESS_SHA256"
printf -v BLOCKER_SHA_Q '%q' "$BLOCKER_SHA256"

read -r -d '' REMOTE <<'REMOTE_SCRIPT' || true
set -Eeuo pipefail

REPO=/opt/bp
tmp="$(mktemp -d /tmp/bp-v4-compact-dedupe-migration.XXXXXX)"
trap 'rm -rf "$tmp"' EXIT

printf '%s' '__PAYLOAD_B64__' | base64 -d |
  while IFS=: read -r path encoded; do
    name="${path##*/}"
    printf '%s' "$encoded" | base64 -d > "$tmp/$name"
  done
chmod 0644 "$tmp"/*.py

set +e
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$REPO/src:$tmp"   "$REPO/.venv/bin/python" "$tmp/run_v4_compact_dedupe_migration.py"   --repo "$REPO"   --env-file /etc/bp/bp.env   --safety-file /etc/bp/bp-prospective-runtime-safety.env   --expected-deployed-head __DEPLOYED_HEAD__   --helper-head __HELPER_HEAD__   --readiness-evidence-sha256 __READINESS_SHA256__   --blocker-evidence-sha256 __BLOCKER_SHA256__   --execute
rc=$?
set -e

if (( rc != 0 )); then
  echo "PHASE14_V4_COMPACT_DEDUPE_MIGRATION_GATE=FAIL"
  exit "$rc"
fi
REMOTE_SCRIPT

REMOTE="${REMOTE/__PAYLOAD_B64__/$PAYLOAD_B64}"
REMOTE="${REMOTE/__DEPLOYED_HEAD__/$DEPLOYED_Q}"
REMOTE="${REMOTE/__HELPER_HEAD__/$HELPER_Q}"
REMOTE="${REMOTE/__READINESS_SHA256__/$READINESS_SHA_Q}"
REMOTE="${REMOTE/__BLOCKER_SHA256__/$BLOCKER_SHA_Q}"

echo "PROJECT=$PROJECT"
echo "VM=$VM"
echo "ZONE=$ZONE"
echo "HELPER_HEAD=$HELPER_HEAD"
echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "RUNTIME_BRANCH=$RUNTIME_BRANCH"
echo "READINESS_EVIDENCE=$READINESS_EVIDENCE"
echo "READINESS_SHA256=$READINESS_SHA256"
echo "BLOCKER_EVIDENCE=$BLOCKER_EVIDENCE"
echo "BLOCKER_SHA256=$BLOCKER_SHA256"
echo "OPERATION=COMPACT_DEDUPE_UNIQUE_INDEX_MIGRATION"
echo "PRODUCTION_MUTATION=true"
echo "DATABASE_SCHEMA_MUTATION=true"
echo "IRREVERSIBLE_BOUNDARY=DROP_RAW_EVENT_DEDUPE_PRIMARY_CONSTRAINT"
echo "This helper mutates production PostgreSQL and requires exact fresh approval."

REMOTE_OUTPUT="$(mktemp)"
set +e
printf '%s' "$REMOTE" |
  gcloud compute ssh "$VM"     --project="$PROJECT"     --zone="$ZONE"     --quiet     --command='REMOTE_SCRIPT_PATH="$(mktemp /tmp/bp-v4-compact-dedupe-migration.XXXXXX.sh)" && cat > "$REMOTE_SCRIPT_PATH" && sudo bash "$REMOTE_SCRIPT_PATH"; rc=$?; rm -f "$REMOTE_SCRIPT_PATH"; exit "$rc"'     2>&1 | tee "$REMOTE_OUTPUT"
PIPE_RC=("${PIPESTATUS[@]}")
set -e

STREAM_RC="${PIPE_RC[0]}"
GCLOUD_RC="${PIPE_RC[1]}"
TEE_RC="${PIPE_RC[2]}"

if ! grep -Eq '^PHASE14_V4_COMPACT_DEDUPE_MIGRATION_GATE=(PASS|FAIL)$' "$REMOTE_OUTPUT"; then
  rm -f "$REMOTE_OUTPUT"
  fail "remote_terminal_marker_missing:stream_rc=$STREAM_RC:gcloud_rc=$GCLOUD_RC:tee_rc=$TEE_RC"
fi

rm -f "$REMOTE_OUTPUT"

(( GCLOUD_RC == 0 )) || exit "$GCLOUD_RC"
(( STREAM_RC == 0 )) || fail "remote_script_stream_failed:rc=$STREAM_RC"
(( TEE_RC == 0 )) || fail "remote_output_capture_failed:rc=$TEE_RC"
