#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT="${PHASE15_CANARY_PROJECT:-project-4397f2c0-7098-4c1c-abb}"
US_ZONE="${PHASE15_CANARY_US_ZONE:-us-east1-c}"
US_VM="${PHASE15_CANARY_US_VM:-bp-recorder}"

fail() {
  echo "PHASE15_V3_TELEGRAM_APPROVAL_STATUS=FAIL" >&2
  echo "REASON=$1" >&2
  exit 1
}

ROOT=$(git rev-parse --show-toplevel 2>/dev/null || true)
[[ -n "$ROOT" ]] || fail "local_repository_missing"
cd "$ROOT"
[[ -z "$(git status --porcelain --untracked-files=all)" ]] ||
  fail "local_working_tree_dirty"
LOCAL_HEAD=$(git rev-parse HEAD)
REMOTE_MAIN=$(git ls-remote origin refs/heads/main | awk 'NR==1 {print $1}')
[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]] || fail "local_main_not_current"
command -v gcloud >/dev/null 2>&1 || fail "gcloud_missing"
command -v python3 >/dev/null 2>&1 || fail "python3_missing"
gcloud auth list --filter=status:ACTIVE --format='value(account)' | grep -q . ||
  fail "gcloud_auth_missing"

REMOTE=$(
  gcloud compute ssh "$US_VM"     --project="$PROJECT"     --zone="$US_ZONE"     --quiet     --command='sudo /opt/bp/.venv/bin/python -' <<'PY'
import grp
import json
import pwd
import stat
import subprocess
from pathlib import Path

service = "bp-phase15-canary-telegram-approval.service"
current = Path("/opt/bp-phase15-telegram-approval/current")
env_path = Path("/etc/bp/telegram-approval.env")
handoff_env_path = Path("/etc/bp/telegram-approval-handoff.env")
state_root = Path("/var/lib/bp/phase15-canary-telegram-approval")
unit_path = Path("/etc/systemd/system") / service


def systemctl(*args: str) -> str:
    completed = subprocess.run(
        ["systemctl", *args],
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def metadata(path: Path) -> dict[str, object]:
    if not path.exists():
        return {"exists": False}
    info = path.stat()
    return {
        "exists": True,
        "owner": pwd.getpwuid(info.st_uid).pw_name,
        "group": grp.getgrgid(info.st_gid).gr_name,
        "mode": oct(stat.S_IMODE(info.st_mode)),
    }


deployed_head = None
if current.is_symlink():
    resolved = current.resolve()
    deployed_head = resolved.name

env_keys: list[str] = []
token_set = False
user_id_set = False
chat_id_set = False
handoff_configured = False
if env_path.is_file():
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        env_keys.append(key)
        if key == "BP_TELEGRAM_BOT_TOKEN":
            token_set = bool(value)
        elif key == "BP_TELEGRAM_USER_ID":
            user_id_set = value.isdigit() and int(value) > 0
        elif key == "BP_TELEGRAM_CHAT_ID":
            chat_id_set = value.isdigit() and int(value) > 0
        elif key.startswith("BP_TELEGRAM_HANDOFF_"):
            handoff_configured = True

journal = subprocess.run(
    [
        "journalctl",
        "-u",
        service,
        "-p",
        "err",
        "--since",
        "-15 minutes",
        "--no-pager",
        "-q",
    ],
    check=False,
    capture_output=True,
    text=True,
)
journal_error_lines = [line for line in journal.stdout.splitlines() if line.strip()]

payload = {
    "service_active": systemctl("is-active", service) == "active",
    "service_enabled": systemctl("is-enabled", service) == "enabled",
    "deployed_head": deployed_head,
    "unit": metadata(unit_path),
    "env": metadata(env_path),
    "state_root": metadata(state_root),
    "env_keys": sorted(env_keys),
    "bot_token_set": token_set,
    "telegram_user_id_set": user_id_set,
    "telegram_chat_id_set": chat_id_set,
    "handoff_configured": handoff_configured,
    "handoff_env_present": handoff_env_path.exists() or handoff_env_path.is_symlink(),
    "journal_error_line_count_last_15m": len(journal_error_lines),
}
print(json.dumps(payload, separators=(",", ":"), sort_keys=True))
PY
) || fail "remote_status_command_failed"

python3 - "$REMOTE" <<'PY' || fail "remote_status_invalid"
import json
import sys

payload = json.loads(sys.argv[1])
expected_keys = [
    "BP_TELEGRAM_BOT_TOKEN",
    "BP_TELEGRAM_CHAT_ID",
    "BP_TELEGRAM_USER_ID",
]
assert payload["service_active"] is True
assert payload["service_enabled"] is True
assert payload["env_keys"] == expected_keys
assert payload["bot_token_set"] is True
assert payload["telegram_user_id_set"] is True
assert payload["telegram_chat_id_set"] is True
assert payload["handoff_configured"] is False
assert payload["handoff_env_present"] is False
assert payload["unit"]["owner"] == "root"
assert payload["unit"]["group"] == "root"
assert payload["unit"]["mode"] == "0o644"
assert payload["env"]["owner"] == "root"
assert payload["env"]["group"] == "bp"
assert payload["env"]["mode"] == "0o640"
assert payload["state_root"]["owner"] == "bp"
assert payload["state_root"]["group"] == "bp"
assert payload["state_root"]["mode"] == "0o700"
print(json.dumps(payload, indent=2, sort_keys=True))
PY

DEPLOYED_HEAD=$(python3 - "$REMOTE" <<'PY'
import json
import sys
value=json.loads(sys.argv[1]).get("deployed_head")
if not value:
    raise SystemExit("deployed head missing")
print(value)
PY
) || fail "deployed_head_missing"

[[ "$DEPLOYED_HEAD" =~ ^[0-9a-f]{40}$ ]] || fail "deployed_head_invalid"
git cat-file -e "$DEPLOYED_HEAD^{commit}" 2>/dev/null ||
  fail "deployed_head_missing_locally"

BINDING_PATHS=(
  deploy/bp-phase15-canary-telegram-approval.service
  scripts/run_phase15_v3_canary_telegram_approval.py
  src/bp_engine/execution/telegram_approval.py
)
if git diff --quiet "$DEPLOYED_HEAD" "$LOCAL_HEAD" -- "${BINDING_PATHS[@]}"; then
  echo "LISTENER_BINDING_CURRENT=true"
else
  echo "LISTENER_BINDING_CURRENT=false"
  echo "REASON=telegram_listener_binding_changed_after_deploy"
  exit 1
fi

echo "DEPLOYED_HEAD=$DEPLOYED_HEAD"
echo "HANDOFF_CONFIGURED=false"
echo "NO_REAL_ORDER_SUBMITTED=true"
echo "PHASE15_V3_TELEGRAM_APPROVAL_STATUS=PASS"
