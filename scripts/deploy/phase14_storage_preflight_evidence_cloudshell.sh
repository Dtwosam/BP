#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE14_PARTITIONED_STORAGE_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_PARTITIONED_STORAGE_ZONE:-us-east1-c}"
VM="${PHASE14_PARTITIONED_STORAGE_VM:-bp-recorder}"
EXPECTED_FROM_HEAD="${PHASE14_PARTITIONED_STORAGE_FROM_HEAD:-}"
EXPECTED_HEAD="${PHASE14_PARTITIONED_STORAGE_HEAD:-}"

if [[ ! "$EXPECTED_FROM_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_PARTITIONED_STORAGE_FROM_HEAD must be the exact 40-character deployed SHA" >&2
  exit 2
fi
if [[ ! "$EXPECTED_HEAD" =~ ^[0-9a-f]{40}$ ]]; then
  echo "PHASE14_PARTITIONED_STORAGE_HEAD must be the exact 40-character candidate SHA" >&2
  exit 2
fi

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
if [[ -z "$ROOT" ]]; then
  echo "run this helper from a BP repository working tree" >&2
  exit 2
fi
cd "$ROOT"

LOCAL_HEAD=$(git rev-parse HEAD)
[[ "$LOCAL_HEAD" == "$EXPECTED_HEAD" ]] || {
  echo "local_candidate_head_mismatch:$LOCAL_HEAD" >&2
  exit 2
}
if [[ -n "$(git status --porcelain --untracked-files=all)" ]]; then
  echo "local_working_tree_dirty" >&2
  exit 2
fi

if ! ARCHIVE_EVIDENCE=$(python - <<'PY'
from __future__ import annotations

import json
import re
from pathlib import Path

payload = json.loads(Path("PROJECT_STATE.json").read_text(encoding="utf-8"))
followup = payload.get("phase_14_storage_reliability_followup") or {}
archive_evidence = followup.get("archive_recovery_host_evidence")
if not isinstance(archive_evidence, str) or not re.fullmatch(
    r"/mnt/bp-data/evidence/phase14-storage-recovery-24-48h-[0-9]{8}T[0-9]{6}Z\.json",
    archive_evidence,
):
    raise SystemExit(1)
print(archive_evidence)
PY
); then
  echo "archive_evidence_binding_invalid" >&2
  exit 2
fi

PREFLIGHT="scripts/deploy/phase14_partitioned_storage_preflight_cloudshell.sh"
VERIFIER="scripts/deploy/verify_phase14_storage_preflight.py"
[[ -f "$PREFLIGHT" ]] || { echo "missing $PREFLIGHT" >&2; exit 2; }
[[ -f "$VERIFIER" ]] || { echo "missing $VERIFIER" >&2; exit 2; }

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TRANSCRIPT="${PHASE14_STORAGE_PREFLIGHT_TRANSCRIPT:-$HOME/bp-phase14-storage-preflight-$STAMP.txt}"
VERIFIED="${PHASE14_STORAGE_PREFLIGHT_VERIFIED:-$HOME/bp-phase14-storage-preflight-$STAMP.json}"

[[ "$TRANSCRIPT" != "$VERIFIED" ]] || {
  echo "preflight_evidence_paths_must_differ" >&2
  exit 2
}
(set -o noclobber; : > "$TRANSCRIPT") || {
  echo "preflight_transcript_path_exists" >&2
  exit 2
}

PHASE14_PARTITIONED_STORAGE_ARCHIVE_EVIDENCE="$ARCHIVE_EVIDENCE" \
  bash "$PREFLIGHT" | tee -a "$TRANSCRIPT"

(set -o noclobber; : > "$VERIFIED") || {
  echo "preflight_verified_path_exists" >&2
  exit 2
}
if ! python "$VERIFIER" \
  --input "$TRANSCRIPT" \
  --expected-from-head "$EXPECTED_FROM_HEAD" \
  --expected-head "$EXPECTED_HEAD" \
  --expected-project "$PROJECT" \
  --expected-zone "$ZONE" \
  --expected-vm "$VM" \
  > "$VERIFIED"; then
  rm -f "$VERIFIED"
  exit 1
fi

VERIFIED_SHA256=$(sha256sum "$VERIFIED" | awk '{print $1}')
[[ "$VERIFIED_SHA256" =~ ^[0-9a-f]{64}$ ]] || {
  echo "verified_preflight_digest_invalid" >&2
  exit 2
}

echo "PHASE14_STORAGE_PREFLIGHT_EVIDENCE=PASS"
echo "ARCHIVE_EVIDENCE=$ARCHIVE_EVIDENCE"
echo "TRANSCRIPT=$TRANSCRIPT"
echo "VERIFIED=$VERIFIED"
echo "VERIFIED_SHA256=$VERIFIED_SHA256"
