#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PYTHON=/opt/bp-telegram-transport/.venv/bin/python
SCRIPT=/opt/bp-telegram-transport/current/scripts/run_phase15_v3_telegram_approved_outbox.py
PYTHONPATH_ROOT=/opt/bp-telegram-transport/current/src

fail() {
  echo "PHASE15_V3_TELEGRAM_APPROVED_OUTBOX_HANDOFF=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

[[ "$#" -eq 2 ]] || fail "expected_prepared_and_approval_paths"
[[ "${BP_TELEGRAM_HANDOFF_ENABLED:-no}" == "yes" ]] ||
  fail "telegram_handoff_not_enabled"
[[ "${BP_TELEGRAM_APPROVED_OUTBOX_ENABLED:-no}" == "yes" ]] ||
  fail "telegram_approved_outbox_not_enabled"
[[ -x "$PYTHON" ]] || fail "transport_python_missing"
[[ -f "$SCRIPT" && ! -L "$SCRIPT" ]] || fail "approved_outbox_script_missing"
[[ -d "$PYTHONPATH_ROOT" && ! -L "$PYTHONPATH_ROOT" ]] ||
  fail "transport_pythonpath_missing"

for required in   BP_TELEGRAM_ORIGIN_KEY_FILE   BP_TELEGRAM_ORIGIN_KEY_ID   BP_TELEGRAM_TRANSPORT_KEY_FILE   BP_TELEGRAM_TRANSPORT_KEY_ID   BP_APPROVED_INTENT_ID   BP_APPROVED_REQUEST_SHA256
do
  [[ -n "${!required:-}" ]] || fail "required_environment_missing:$required"
done

for forbidden in   POLYMARKET_PRIVATE_KEY   POLYMARKET_WALLET_ADDRESS   BP_TELEGRAM_BOT_TOKEN   GOOGLE_APPLICATION_CREDENTIALS
do
  [[ -z "${!forbidden:-}" ]] || fail "forbidden_environment_present:$forbidden"
done

export PYTHONPATH="$PYTHONPATH_ROOT"
exec "$PYTHON" "$SCRIPT" "$1" "$2"
