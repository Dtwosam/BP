#!/usr/bin/env bash
set -euo pipefail
umask 077

BP_ROOT=${BP_ROOT:-/opt/bp}
ENV_FILE=${BP_ENV_FILE:-/etc/bp/bp.env}
EVIDENCE_DIR=${BP_EVIDENCE_DIR:-/var/lib/bp/evidence}
SOAK_HOURS=${SOAK_HOURS:-24}
SOAK_MINIMUM_HOURS=${SOAK_MINIMUM_HOURS:-24}

if [[ ! -r ${ENV_FILE} ]]; then
  echo "Cannot read ${ENV_FILE}. Run with sudo or an authorized account." >&2
  exit 1
fi
if ! systemctl is-active --quiet bp-recorder.service; then
  echo "bp-recorder.service is not active; refusing to certify the soak." >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "${ENV_FILE}"
set +a

mkdir -p "${EVIDENCE_DIR}"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
report=${EVIDENCE_DIR}/phase2-soak-${stamp}.json

set +e
"${BP_ROOT}/.venv/bin/python" "${BP_ROOT}/scripts/soak_report.py" \
  --hours "${SOAK_HOURS}" \
  --minimum-hours "${SOAK_MINIMUM_HOURS}" | tee "${report}"
status=${PIPESTATUS[0]}
set -e

if [[ ${status} -ne 0 ]]; then
  echo "Phase 2 soak gate failed. Recent recorder logs:" >&2
  journalctl -u bp-recorder --since "${SOAK_HOURS} hours ago" --no-pager -n 250 >&2 || true
  exit "${status}"
fi

echo "Phase 2 soak gate passed. Evidence: ${report}"
