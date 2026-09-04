#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

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

PREFLIGHT="scripts/deploy/phase14_partitioned_storage_preflight_cloudshell.sh"
VERIFIER="scripts/deploy/verify_phase14_storage_preflight.py"
[[ -f "$PREFLIGHT" ]] || { echo "missing $PREFLIGHT" >&2; exit 2; }
[[ -f "$VERIFIER" ]] || { echo "missing $VERIFIER" >&2; exit 2; }

STAMP=$(date -u +%Y%m%dT%H%M%SZ)
TRANSCRIPT="${PHASE14_STORAGE_PREFLIGHT_TRANSCRIPT:-$HOME/bp-phase14-storage-preflight-$STAMP.txt}"
VERIFIED="${PHASE14_STORAGE_PREFLIGHT_VERIFIED:-$HOME/bp-phase14-storage-preflight-$STAMP.json}"

bash "$PREFLIGHT" | tee "$TRANSCRIPT"

python "$VERIFIER" \
  --input "$TRANSCRIPT" \
  --expected-from-head "$EXPECTED_FROM_HEAD" \
  --expected-head "$EXPECTED_HEAD" \
  > "$VERIFIED"

echo "PHASE14_STORAGE_PREFLIGHT_EVIDENCE=PASS"
echo "TRANSCRIPT=$TRANSCRIPT"
echo "VERIFIED=$VERIFIED"
