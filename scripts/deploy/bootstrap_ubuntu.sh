#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "Run this script as root, for example: sudo bash scripts/deploy/bootstrap_ubuntu.sh" >&2
  exit 1
fi

BP_ROOT=${BP_ROOT:-/opt/bp}
BP_USER=${BP_USER:-bp}
BP_GROUP=${BP_GROUP:-bp}
ENV_DIR=/etc/bp
ENV_FILE=${ENV_DIR}/bp.env
EVIDENCE_DIR=/var/lib/bp/evidence
ARCHIVE_DIR=/var/lib/bp/archive/raw

if [[ ! -f ${BP_ROOT}/pyproject.toml ]]; then
  echo "Expected the BP repository at ${BP_ROOT}. Clone it there before running bootstrap." >&2
  exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  chrony \
  git \
  openssl \
  python3 \
  python3-venv

if ! command -v docker >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io docker-compose-v2
elif ! docker compose version >/dev/null 2>&1; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 12):
    raise SystemExit("Python 3.12+ is required")
PY

systemctl enable --now docker
systemctl enable --now chrony
timedatectl set-timezone UTC

if ! getent group "${BP_GROUP}" >/dev/null; then
  groupadd --system "${BP_GROUP}"
fi
if ! id -u "${BP_USER}" >/dev/null 2>&1; then
  useradd \
    --system \
    --gid "${BP_GROUP}" \
    --home-dir /var/lib/bp \
    --create-home \
    --shell /usr/sbin/nologin \
    "${BP_USER}"
fi

python3 -m venv "${BP_ROOT}/.venv"
"${BP_ROOT}/.venv/bin/python" -m pip install --disable-pip-version-check -e "${BP_ROOT}"

install -d -m 0750 -o root -g "${BP_GROUP}" "${ENV_DIR}"
install -d -m 0750 -o "${BP_USER}" -g "${BP_GROUP}" \
  /var/lib/bp \
  "${EVIDENCE_DIR}" \
  "${ARCHIVE_DIR}"

if [[ ! -f ${ENV_FILE} ]]; then
  db_password=$(openssl rand -hex 24)
  cat >"${ENV_FILE}" <<EOF
MODE=research
LIVE_TRADING_ENABLED=false
TIMEZONE=UTC
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
POSTGRES_DB=bp
POSTGRES_USER=bp
POSTGRES_PASSWORD=${db_password}
DATABASE_URL=postgresql+psycopg://bp:${db_password}@127.0.0.1:5432/bp
RECORDER_QUEUE_MAXSIZE=50000
RECORDER_BATCH_SIZE=500
RECORDER_FLUSH_INTERVAL_SECONDS=0.25
POLYMARKET_REFRESH_INTERVAL_SECONDS=30
POLYMARKET_SUBSCRIPTION_GRACE_SECONDS=30
RECORDER_STALE_AFTER_SECONDS=10
RECORDER_MAX_CLOCK_SKEW_SECONDS=5
RECORDER_REQUIRE_NTP_SYNC=true
STORAGE_HOT_RAW_HOURS=24
STORAGE_ARCHIVE_RETENTION_HOURS=24
STORAGE_STATE_RETENTION_DAYS=90
STORAGE_ARCHIVE_DIR=/var/lib/bp/archive/raw
STORAGE_WARNING_FREE_GIB=25
STORAGE_CRITICAL_FREE_GIB=15
STORAGE_DELETE_BATCH_SIZE=50000
POLYMARKET_WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
BYBIT_SPOT_WS_URL=wss://stream.bybit.com/v5/public/spot
BYBIT_LINEAR_WS_URL=wss://stream.bybit.com/v5/public/linear
COINBASE_SPOT_WS_URL=wss://advanced-trade-ws.coinbase.com
EOF
fi

ensure_env_default() {
  local key=$1
  local value=$2
  if ! grep -q "^${key}=" "${ENV_FILE}"; then
    printf '%s=%s\n' "${key}" "${value}" >>"${ENV_FILE}"
  fi
}

ensure_env_default STORAGE_HOT_RAW_HOURS 24
ensure_env_default STORAGE_ARCHIVE_RETENTION_HOURS 24
ensure_env_default STORAGE_STATE_RETENTION_DAYS 90
ensure_env_default STORAGE_ARCHIVE_DIR /var/lib/bp/archive/raw
ensure_env_default STORAGE_WARNING_FREE_GIB 25
ensure_env_default STORAGE_CRITICAL_FREE_GIB 15
ensure_env_default STORAGE_DELETE_BATCH_SIZE 50000

chown root:"${BP_GROUP}" "${ENV_FILE}"
chmod 0640 "${ENV_FILE}"

chown -R root:"${BP_GROUP}" "${BP_ROOT}"
chmod -R g+rX "${BP_ROOT}"

install -m 0644 \
  "${BP_ROOT}/deploy/systemd/bp-postgres.service" \
  /etc/systemd/system/bp-postgres.service
install -m 0644 \
  "${BP_ROOT}/deploy/systemd/bp-recorder.service" \
  /etc/systemd/system/bp-recorder.service
install -m 0644 \
  "${BP_ROOT}/deploy/systemd/bp-storage-maintenance.service" \
  /etc/systemd/system/bp-storage-maintenance.service
install -m 0644 \
  "${BP_ROOT}/deploy/systemd/bp-storage-maintenance.timer" \
  /etc/systemd/system/bp-storage-maintenance.timer
install -m 0644 \
  "${BP_ROOT}/deploy/systemd/bp-storage-disk-health.service" \
  /etc/systemd/system/bp-storage-disk-health.service
install -m 0644 \
  "${BP_ROOT}/deploy/systemd/bp-storage-disk-health.timer" \
  /etc/systemd/system/bp-storage-disk-health.timer

systemctl daemon-reload
systemctl enable --now bp-postgres.service
sudo -u "${BP_USER}" "${BP_ROOT}/.venv/bin/python" \
  "${BP_ROOT}/scripts/deploy/ensure_storage_indexes.py" \
  --env-file "${ENV_FILE}"
systemctl enable --now bp-recorder.service

ntp_state=$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)
if [[ ${ntp_state,,} != yes ]]; then
  echo "Host NTP is not synchronized yet. Recorder service may restart until clock sync is ready." >&2
fi

systemctl is-active --quiet bp-postgres.service
systemctl is-active --quiet bp-recorder.service

echo "BP recorder deployment is active."
echo "Check logs with: journalctl -u bp-recorder -f"
echo "Phase 3 storage timer units are installed but intentionally not enabled."
echo "Verify storage manually before enabling them; see docs/PHASE-3-DEPLOYMENT.md."
