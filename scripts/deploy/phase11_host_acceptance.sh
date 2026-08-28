#!/usr/bin/env bash
set -Eeuo pipefail

EXPECTED_HEAD="${1:-}"
HOST_REPO=/opt/bp
REPO="${BP_REPO:-$HOST_REPO}"
ENV_FILE=/etc/bp/bp.env
HOST_PY="$HOST_REPO/.venv/bin/python"
EVIDENCE_ROOT=/var/lib/bp/evidence/phase11-dashboard
RUNTIME_ROOT=/var/lib/bp/phase11-runtime
NODE_VERSION=24.20.0
NODE_ROOT="$RUNTIME_ROOT/node-v$NODE_VERSION"
API_PORT="${PHASE11_API_PORT:-18787}"
WEB_PORT="${PHASE11_WEB_PORT:-13000}"
VENV="$RUNTIME_ROOT/bp-phase11-venv-${EXPECTED_HEAD:0:12}-$$"
API_SERVICE=bp-phase11-dashboard-api.service
WEB_SERVICE=bp-phase11-dashboard-web.service
API_UNIT="/run/systemd/system/$API_SERVICE"
WEB_UNIT="/run/systemd/system/$WEB_SERVICE"

if [[ -z "$EXPECTED_HEAD" ]]; then
  echo "usage: $0 EXPECTED_HEAD" >&2
  exit 2
fi
if [[ ${EUID} -ne 0 ]]; then
  echo "Phase 11 host acceptance must run as root" >&2
  exit 2
fi
if ! [[ "$API_PORT" =~ ^[0-9]+$ ]] || (( API_PORT < 1024 || API_PORT > 65535 )); then
  echo "PHASE11_API_PORT must be an unprivileged TCP port" >&2
  exit 2
fi
if ! [[ "$WEB_PORT" =~ ^[0-9]+$ ]] || (( WEB_PORT < 1024 || WEB_PORT > 65535 )); then
  echo "PHASE11_WEB_PORT must be an unprivileged TCP port" >&2
  exit 2
fi
if [[ "$API_PORT" == "$WEB_PORT" ]]; then
  echo "Phase 11 API and web acceptance ports must differ" >&2
  exit 2
fi

actual_head="${BP_VERIFIED_HEAD:-}"
if [[ -z "$actual_head" || "$actual_head" != "$EXPECTED_HEAD" ]]; then
  echo "candidate provenance mismatch" >&2
  echo "EXPECTED_HEAD=$EXPECTED_HEAD" >&2
  echo "BP_VERIFIED_HEAD=${actual_head:-missing}" >&2
  exit 2
fi

required_files=(
  "$REPO/apps/dashboard/package.json"
  "$REPO/deploy/systemd/bp-dashboard-api.service"
  "$REPO/deploy/systemd/bp-dashboard-web.service"
  "$REPO/src/bp_engine/dashboard/api.py"
  "$REPO/src/bp_engine/dashboard/repository.py"
  "$REPO/src/bp_engine/dashboard/service.py"
)
for path in "${required_files[@]}"; do
  if [[ ! -f "$path" ]]; then
    echo "missing Phase 11 candidate file: $path" >&2
    exit 2
  fi
done
if [[ ! -f "$ENV_FILE" || ! -x "$HOST_PY" ]]; then
  echo "missing production environment or host Python" >&2
  exit 2
fi

read_env() {
  local key=$1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE"
}

MODE=$(read_env MODE)
LIVE_TRADING_ENABLED=$(read_env LIVE_TRADING_ENABLED)
MAX_TRADE_SIZE_USD=$(read_env MAX_TRADE_SIZE_USD)
MAX_DAILY_LOSS_USD=$(read_env MAX_DAILY_LOSS_USD)
if [[ "$MODE" != "research" || "$LIVE_TRADING_ENABLED" != "false" || \
      "$MAX_TRADE_SIZE_USD" != "0" || "$MAX_DAILY_LOSS_USD" != "0" ]]; then
  echo "Phase 11 requires research mode, live disabled, and zero trading limits" >&2
  exit 3
fi

RECORDER_BEFORE=$(systemctl is-active bp-recorder || true)
POSTGRES_BEFORE=$(systemctl is-active bp-postgres || true)
if [[ "$RECORDER_BEFORE" != "active" || "$POSTGRES_BEFORE" != "active" ]]; then
  echo "recorder and PostgreSQL must be active before Phase 11 acceptance" >&2
  exit 4
fi

for unit in bp-dashboard-api.service bp-dashboard-web.service; do
  if ! grep -qx 'User=bp' "$REPO/deploy/systemd/$unit" || \
     ! grep -qx 'Group=bp' "$REPO/deploy/systemd/$unit" || \
     ! grep -qx 'NoNewPrivileges=true' "$REPO/deploy/systemd/$unit" || \
     ! grep -qx 'IPAddressDeny=any' "$REPO/deploy/systemd/$unit" || \
     ! grep -qx 'IPAddressAllow=localhost' "$REPO/deploy/systemd/$unit"; then
    echo "$unit does not satisfy the Phase 11 privilege/network contract" >&2
    exit 4
  fi
done

install -d -o bp -g bp "$EVIDENCE_ROOT" "$RUNTIME_ROOT"
stamp=$(date -u +%Y%m%dT%H%M%SZ)
run_dir="$EVIDENCE_ROOT/$stamp"
install -d -o bp -g bp "$run_dir"

install_node_runtime() (
  if [[ -x "$NODE_ROOT/bin/node" ]] && \
     [[ "$($NODE_ROOT/bin/node --version)" == "v$NODE_VERSION" ]]; then
    return
  fi

  local machine node_arch archive tmp expected
  machine=$(uname -m)
  case "$machine" in
    x86_64) node_arch=x64 ;;
    aarch64|arm64) node_arch=arm64 ;;
    *) echo "unsupported Node architecture: $machine" >&2; return 1 ;;
  esac
  archive="node-v${NODE_VERSION}-linux-${node_arch}.tar.xz"
  tmp=$(mktemp -d)
  trap 'rm -rf "$tmp"' EXIT

  if ! command -v curl >/dev/null 2>&1 || ! command -v xz >/dev/null 2>&1; then
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl xz-utils
  fi

  curl --fail --silent --show-error --location \
    "https://nodejs.org/dist/v${NODE_VERSION}/${archive}" -o "$tmp/$archive"
  curl --fail --silent --show-error --location \
    "https://nodejs.org/dist/v${NODE_VERSION}/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
  expected=$(awk -v file="$archive" '$2 == file {print $1; exit}' "$tmp/SHASUMS256.txt")
  if [[ -z "$expected" ]]; then
    echo "published Node checksum not found for $archive" >&2
    return 1
  fi
  printf '%s  %s\n' "$expected" "$tmp/$archive" | sha256sum -c -

  rm -rf "$NODE_ROOT"
  install -d -o root -g bp "$NODE_ROOT"
  tar -xJf "$tmp/$archive" -C "$NODE_ROOT" --strip-components=1
  chown -R root:bp "$NODE_ROOT"
  chmod -R g+rX "$NODE_ROOT"
  "$NODE_ROOT/bin/node" --version | tee "$run_dir/node-version.txt"
  env PATH="$NODE_ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "$NODE_ROOT/bin/npm" --version | tee "$run_dir/npm-version.txt"
)

cleanup() {
  set +e
  systemctl stop "$WEB_SERVICE" >/dev/null 2>&1 || true
  systemctl stop "$API_SERVICE" >/dev/null 2>&1 || true
  rm -f "$WEB_UNIT" "$API_UNIT"
  systemctl daemon-reload >/dev/null 2>&1 || true
  rm -rf "$VENV"
  set -e
}
trap cleanup EXIT

systemctl stop "$WEB_SERVICE" >/dev/null 2>&1 || true
systemctl stop "$API_SERVICE" >/dev/null 2>&1 || true
rm -f "$WEB_UNIT" "$API_UNIT"
systemctl daemon-reload

if ss -ltn | grep -Eq "127\\.0\\.0\\.1:(${API_PORT}|${WEB_PORT})[[:space:]]"; then
  echo "Phase 11 acceptance port is already in use" >&2
  exit 4
fi

install_node_runtime
sudo -u bp "$HOST_PY" -m venv "$VENV"
sudo -u bp "$VENV/bin/python" -m pip install --disable-pip-version-check "$REPO" \
  | tee "$run_dir/candidate-python-install.txt"

DASHBOARD_DIR="$REPO/apps/dashboard"
sudo -u bp env \
  PATH="$NODE_ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_ROOT/bin/npm" --prefix "$DASHBOARD_DIR" install --ignore-scripts --no-audit --no-fund \
  | tee "$run_dir/dashboard-npm-install.txt"
sudo -u bp env \
  PATH="$NODE_ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_ROOT/bin/npm" --prefix "$DASHBOARD_DIR" test \
  | tee "$run_dir/dashboard-test.txt"
sudo -u bp env \
  PATH="$NODE_ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_ROOT/bin/npm" --prefix "$DASHBOARD_DIR" run typecheck \
  | tee "$run_dir/dashboard-typecheck.txt"
sudo -u bp env \
  PATH="$NODE_ROOT/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
  NEXT_TELEMETRY_DISABLED=1 \
  "$NODE_ROOT/bin/npm" --prefix "$DASHBOARD_DIR" run build \
  | tee "$run_dir/dashboard-build.txt"

cat > "$API_UNIT" <<EOF
[Unit]
Description=BP Phase 11 acceptance dashboard API
After=network-online.target

[Service]
Type=simple
User=bp
Group=bp
WorkingDirectory=$REPO
EnvironmentFile=$ENV_FILE
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=PYTHONUNBUFFERED=1
ExecStart=$VENV/bin/python -m bp_engine.dashboard --host 127.0.0.1 --port $API_PORT
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=any
IPAddressAllow=localhost
EOF

cat > "$WEB_UNIT" <<EOF
[Unit]
Description=BP Phase 11 acceptance dashboard web
Requires=$API_SERVICE
After=$API_SERVICE

[Service]
Type=simple
User=bp
Group=bp
WorkingDirectory=$DASHBOARD_DIR
Environment=NODE_ENV=production
Environment=NEXT_TELEMETRY_DISABLED=1
Environment=BP_DASHBOARD_API_URL=http://127.0.0.1:$API_PORT/api/v1/snapshot
ExecStart=$NODE_ROOT/bin/node $DASHBOARD_DIR/node_modules/next/dist/bin/next start -H 127.0.0.1 -p $WEB_PORT
Restart=on-failure
RestartSec=2
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=full
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
IPAddressDeny=any
IPAddressAllow=localhost
EOF

systemctl daemon-reload
systemctl start "$API_SERVICE"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$API_PORT/health" > "$run_dir/api-health.json"; then
    break
  fi
  sleep 1
done
if [[ "$(systemctl is-active "$API_SERVICE" || true)" != "active" ]]; then
  systemctl --no-pager --full status "$API_SERVICE" >&2 || true
  journalctl -u "$API_SERVICE" -n 80 --no-pager >&2 || true
  exit 5
fi

systemctl start "$WEB_SERVICE"
for _ in $(seq 1 30); do
  if curl -fsS "http://127.0.0.1:$WEB_PORT/" > "$run_dir/dashboard.html"; then
    break
  fi
  sleep 1
done
if [[ "$(systemctl is-active "$WEB_SERVICE" || true)" != "active" ]]; then
  systemctl --no-pager --full status "$WEB_SERVICE" >&2 || true
  journalctl -u "$WEB_SERVICE" -n 80 --no-pager >&2 || true
  exit 5
fi

if [[ "$(systemctl show "$API_SERVICE" -p User --value)" != "bp" || \
      "$(systemctl show "$WEB_SERVICE" -p User --value)" != "bp" ]]; then
  echo "Phase 11 runtime services are not running as bp" >&2
  exit 5
fi

curl -fsS "http://127.0.0.1:$API_PORT/api/v1/snapshot" > "$run_dir/api-snapshot.json"
curl -fsS "http://127.0.0.1:$WEB_PORT/api/snapshot" > "$run_dir/web-snapshot.json"
mutation_status=$(curl -sS -o "$run_dir/api-mutation.json" -w '%{http_code}' \
  -X POST "http://127.0.0.1:$API_PORT/api/v1/snapshot")
if [[ "$mutation_status" != "405" ]]; then
  echo "dashboard API mutation request was not rejected" >&2
  exit 6
fi

sudo -u bp "$VENV/bin/python" - "$run_dir/api-snapshot.json" "$run_dir/web-snapshot.json" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

api = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
web = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
for label, snapshot in (("api", api), ("web", web)):
    mode = snapshot.get("mode") or {}
    if mode != {
        "trading_mode": "RESEARCH",
        "live_trading_enabled": False,
        "execution_available": False,
        "paper_execution_available": False,
    }:
        raise SystemExit(f"{label} snapshot mode is not fail-closed research: {mode!r}")
    paper = snapshot.get("paper_pnl") or {}
    if paper.get("status") != "UNAVAILABLE_UNTIL_PHASE_12" or paper.get("value") is not None:
        raise SystemExit(f"{label} snapshot manufactured paper P&L")
    for key in ("active_markets", "feed_health", "performance", "prediction_history"):
        if not isinstance(snapshot.get(key), list):
            raise SystemExit(f"{label} snapshot {key} is not a list")
    generated = datetime.fromisoformat(snapshot["generated_at"])
    if generated.tzinfo is None:
        raise SystemExit(f"{label} snapshot generated_at is not timezone-aware")
    age = (datetime.now(UTC) - generated.astimezone(UTC)).total_seconds()
    if age < -5 or age > 120:
        raise SystemExit(f"{label} snapshot is stale or future-dated: age={age}")
    for row in snapshot["performance"]:
        total = int(row["total_predictions"])
        evaluated = int(row["evaluated_predictions"])
        if evaluated < 0 or total < evaluated:
            raise SystemExit(f"{label} snapshot has invalid performance counts")

if not api["feed_health"]:
    raise SystemExit("feed health is empty while recorder acceptance requires live system visibility")
if not api["performance"] or sum(int(row["total_predictions"]) for row in api["performance"]) == 0:
    raise SystemExit("prediction performance ledger is empty")

print(f"ACTIVE_MARKETS={len(api['active_markets'])}")
print(f"FEED_ROWS={len(api['feed_health'])}")
print(f"PERFORMANCE_ROWS={len(api['performance'])}")
print(f"PREDICTION_HISTORY_ROWS={len(api['prediction_history'])}")
print(f"EVALUATED_PREDICTIONS={sum(int(row['evaluated_predictions']) for row in api['performance'])}")
PY

if ! grep -q 'Prediction operator dashboard' "$run_dir/dashboard.html" || \
   ! grep -q 'Read-only by design' "$run_dir/dashboard.html"; then
  echo "dashboard HTML does not expose the operator/read-only identity" >&2
  exit 6
fi

if ! ss -ltn | grep -Eq "127\\.0\\.0\\.1:${API_PORT}[[:space:]]" || \
   ! ss -ltn | grep -Eq "127\\.0\\.0\\.1:${WEB_PORT}[[:space:]]"; then
  echo "Phase 11 services are not listening on expected loopback ports" >&2
  ss -ltn >&2 || true
  exit 6
fi
if ss -ltn | grep -Eq "(0\\.0\\.0\\.0|\\[::\\]):(${API_PORT}|${WEB_PORT})[[:space:]]"; then
  echo "Phase 11 service exposed a non-loopback listener" >&2
  ss -ltn >&2 || true
  exit 6
fi

RECORDER_AFTER=$(systemctl is-active bp-recorder || true)
if [[ "$RECORDER_AFTER" != "active" ]]; then
  echo "bp-recorder was disturbed by Phase 11 acceptance" >&2
  exit 7
fi

cp "$REPO/deploy/systemd/bp-dashboard-api.service" "$run_dir/bp-dashboard-api.service"
cp "$REPO/deploy/systemd/bp-dashboard-web.service" "$run_dir/bp-dashboard-web.service"
printf '%s\n' "$EXPECTED_HEAD" > "$run_dir/candidate-head.txt"

echo "PHASE11_HOST_ACCEPTANCE=PASS"
echo "HEAD=$EXPECTED_HEAD"
echo "API_LISTENER=127.0.0.1:$API_PORT"
echo "WEB_LISTENER=127.0.0.1:$WEB_PORT"
echo "RECORDER_STATUS=$RECORDER_AFTER"
echo "EVIDENCE_DIR=$run_dir"
