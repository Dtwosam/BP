#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROJECT="${PHASE14_PARTITIONED_STORAGE_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
ZONE="${PHASE14_PARTITIONED_STORAGE_ZONE:-us-east1-c}"
VM="${PHASE14_PARTITIONED_STORAGE_VM:-bp-recorder}"
BRANCH="${PHASE14_PARTITIONED_STORAGE_BRANCH:-main}"
MIN_FREE_GIB="${PHASE14_PARTITIONED_STORAGE_MIN_FREE_GIB:-40}"
ENV_FILE="${PHASE14_PARTITIONED_STORAGE_ENV_FILE:-/etc/bp/bp.env}"
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
if ! [[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]]; then
  echo "PHASE14_PARTITIONED_STORAGE_BRANCH contains unsupported characters" >&2
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
import sys
from pathlib import Path

raw = Path("PROJECT_STATE.json").read_bytes()


def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    payload: dict[str, object] = {}
    for key, value in pairs:
        if key in payload:
            raise SystemExit(f"project state JSON contains duplicate key: {key}")
        payload[key] = value
    return payload


def reject_nonfinite_constant(value: str) -> object:
    raise SystemExit(f"project state JSON contains non-finite constant: {value}")


payload = json.loads(
    raw,
    object_pairs_hook=reject_duplicate_keys,
    parse_constant=reject_nonfinite_constant,
)
if not isinstance(payload, dict):
    raise SystemExit("project state JSON root is not an object")

followup = payload.get("phase_14_storage_reliability_followup") or {}
archive_evidence = followup.get("archive_recovery_host_evidence")
if not isinstance(archive_evidence, str) or not re.fullmatch(
    r"/mnt/bp-data/evidence/phase14-storage-recovery-24-48h-[0-9]{8}T[0-9]{6}Z\.json",
    archive_evidence,
):
    print("archive_evidence_binding_invalid", file=sys.stderr)
    raise SystemExit(1)

values: list[object] = []


def walk(value: object) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "automatic_promotion":
                values.append(item)
            walk(item)
    elif isinstance(value, list):
        for item in value:
            walk(item)


walk(payload)
if not values or any(item is not False for item in values):
    print("automatic_promotion_binding_invalid", file=sys.stderr)
    raise SystemExit(1)

print(archive_evidence)
PY
); then
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
echo "AUTOMATIC_PROMOTION=false" >> "$TRANSCRIPT"

PHASE14_PARTITIONED_STORAGE_ARCHIVE_EVIDENCE="$ARCHIVE_EVIDENCE" \
PHASE14_PARTITIONED_STORAGE_BRANCH="$BRANCH" \
PHASE14_PARTITIONED_STORAGE_ENV_FILE="$ENV_FILE" \
  bash "$PREFLIGHT" | tee -a "$TRANSCRIPT"

(set -o noclobber; : > "$VERIFIED") || {
  echo "preflight_verified_path_exists" >&2
  exit 2
}
if ! python "$VERIFIER" \
  --input "$TRANSCRIPT" \
  --expected-from-head "$EXPECTED_FROM_HEAD" \
  --expected-head "$EXPECTED_HEAD" \
  --expected-branch "$BRANCH" \
  --expected-project "$PROJECT" \
  --expected-zone "$ZONE" \
  --expected-vm "$VM" \
  --expected-archive-evidence "$ARCHIVE_EVIDENCE" \
  --expected-env-file "$ENV_FILE" \
  --min-free-gib "$MIN_FREE_GIB" \
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
