#!/usr/bin/env bash
set -uo pipefail

EXPECTED_HEAD="${1:-}"
EVID="/var/lib/bp/evidence"
RESULT="$EVID/phase3-retention-semantics-manual.json"
DISK_JSON="$EVID/phase3-retention-semantics-disk-health.json"
SUMMARY="$EVID/phase3-retention-semantics-final-summary.txt"
FORENSIC="$EVID/phase3-data-integrity-incident/raw-20260822T200000Z-20260822T210000Z.jsonl.gz"
EXPECTED_FORENSIC_SHA="423f22c58ed356a207684b794f401537ba60e009f08aa89fe54fc7f58efbe9ef"

fail=0

bad() {
    printf 'FAIL | %s\n' "$1" >&2
    fail=$((fail + 1))
}

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo bash $0 <expected-commit-sha>" >&2
    exit 2
fi

if [[ -z "$EXPECTED_HEAD" ]]; then
    echo "Missing expected commit SHA" >&2
    exit 2
fi

mkdir -p "$EVID"
STARTED_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
TARGET_HOT_CUTOFF=$(date -u -d '24 hours ago' '+%Y-%m-%dT%H:00:00Z')

# Fail closed: the maintenance timer remains disabled unless every gate passes.
systemctl disable --now bp-storage-maintenance.timer >/dev/null 2>&1 || true

for _ in $(seq 1 660); do
    state=$(systemctl is-active bp-storage-maintenance.service 2>/dev/null || true)
    case "$state" in
        activating|active|deactivating|reloading)
            sleep 5
            ;;
        *)
            break
            ;;
    esac
done

state=$(systemctl is-active bp-storage-maintenance.service 2>/dev/null || true)
case "$state" in
    activating|active|deactivating|reloading)
        bad "maintenance service still running"
        ;;
esac

head=$(git -C /opt/bp rev-parse HEAD 2>/dev/null || true)
[[ "$head" == "$EXPECTED_HEAD" ]] || bad "HEAD=$head expected=$EXPECTED_HEAD"

tracked_dirty=$(git -C /opt/bp status --porcelain --untracked-files=no 2>/dev/null || true)
[[ -z "$tracked_dirty" ]] || bad "tracked repository is dirty"

if [[ "$fail" -eq 0 ]]; then
    sudo -u bp env PYTHONDONTWRITEBYTECODE=1 /opt/bp/.venv/bin/python - <<'PY'
import ast
from pathlib import Path

for path in (
    Path("/opt/bp/scripts/storage_maintenance.py"),
    Path("/opt/bp/src/bp_engine/storage/maintenance.py"),
):
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
PY
    syntax_rc=$?
    [[ "$syntax_rc" -eq 0 ]] || bad "syntax validation exit=$syntax_rc"
fi

before_archives=$(find /var/lib/bp/archive/raw -maxdepth 1 -type f \
    -name '*.jsonl.gz.manifest.json' | wc -l)

maintenance_rc=99
if [[ "$fail" -eq 0 ]]; then
    sudo -u bp env PYTHONDONTWRITEBYTECODE=1 \
        /opt/bp/.venv/bin/python /opt/bp/scripts/storage_maintenance.py run \
        --env-file /etc/bp/bp.env >"$RESULT"
    maintenance_rc=$?
    [[ "$maintenance_rc" -eq 0 ]] || bad "manual maintenance exit=$maintenance_rc"
fi

status="missing"
removed_count="?"
interval_count="?"
parity="0"

if [[ -s "$RESULT" ]]; then
    mapfile -t parsed < <(python3 - "$RESULT" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    payload = json.load(handle)

print(payload["status"])
print(len(payload["removed_archives"]))
print(len(payload["raw_intervals"]))
print(int(all(
    row["archived_rows"] == row["deleted_rows"]
    for row in payload["raw_intervals"]
)))
PY
)
    status="${parsed[0]:-missing}"
    removed_count="${parsed[1]:-?}"
    interval_count="${parsed[2]:-?}"
    parity="${parsed[3]:-0}"

    [[ "$status" == "ok" ]] || bad "maintenance status=$status"
    [[ "$removed_count" == "0" ]] || bad "premature archive pruning removed=$removed_count"
    [[ "$parity" == "1" ]] || bad "archive/delete row parity failed"
else
    bad "maintenance result JSON missing"
fi

sudo -u bp env PYTHONDONTWRITEBYTECODE=1 /opt/bp/.venv/bin/python - <<'PY'
from pathlib import Path
from bp_engine.storage.maintenance import verify_archive

root = Path("/var/lib/bp/archive/raw")
for manifest in sorted(root.glob("*.jsonl.gz.manifest.json")):
    archive = manifest.with_name(manifest.name.removesuffix(".manifest.json"))
    verify_archive(archive, manifest)
PY
archive_verify_rc=$?
[[ "$archive_verify_rc" -eq 0 ]] || bad "archive verification exit=$archive_verify_rc"

after_archives=$(find /var/lib/bp/archive/raw -maxdepth 1 -type f \
    -name '*.jsonl.gz.manifest.json' | wc -l)

temp_count=$(find /var/lib/bp/archive/raw -maxdepth 1 -type f \
    -name '.raw-*.jsonl.gz.*' | wc -l)
[[ "$temp_count" -eq 0 ]] || bad "stale archive temp files=$temp_count"

old_rows=$(docker compose --env-file /etc/bp/bp.env \
    -f /opt/bp/docker-compose.prod.yml exec -T postgres \
    psql -U bp -d bp -At -c \
    "SELECT count(*) FROM raw_market_events WHERE received_at < TIMESTAMPTZ '$TARGET_HOT_CUTOFF';" \
    2>/dev/null || echo QUERY_FAILED)
[[ "$old_rows" == "0" ]] || bad "raw rows before hot cutoff=$old_rows"

earliest_raw=$(docker compose --env-file /etc/bp/bp.env \
    -f /opt/bp/docker-compose.prod.yml exec -T postgres \
    psql -U bp -d bp -At -c \
    "SELECT min(received_at) FROM raw_market_events;" 2>/dev/null || true)

recorder=$(systemctl is-active bp-recorder 2>/dev/null || true)
[[ "$recorder" == "active" ]] || bad "recorder=$recorder"

feed_count=$(docker compose --env-file /etc/bp/bp.env \
    -f /opt/bp/docker-compose.prod.yml exec -T postgres \
    psql -U bp -d bp -At -c \
    "SELECT count(*) FROM (SELECT source, stream FROM market_state_1s GROUP BY source, stream) feeds;" \
    2>/dev/null || echo QUERY_FAILED)
[[ "$feed_count" == "4" ]] || bad "compact feed count=$feed_count"

sudo -u bp env PYTHONDONTWRITEBYTECODE=1 \
    /opt/bp/.venv/bin/python /opt/bp/scripts/storage_maintenance.py disk-health \
    --env-file /etc/bp/bp.env >"$DISK_JSON"
disk_rc=$?

disk_status=$(python3 - "$DISK_JSON" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    print(json.load(handle)["status"])
PY
)
[[ "$disk_rc" -eq 0 ]] || bad "disk health exit=$disk_rc"
[[ "$disk_status" == "ok" ]] || bad "disk status=$disk_status"

forensic_sha=$(sha256sum "$FORENSIC" 2>/dev/null | awk '{print $1}')
[[ "$forensic_sha" == "$EXPECTED_FORENSIC_SHA" ]] || bad "forensic SHA mismatch=$forensic_sha"

warning_lines=$(journalctl -u bp-recorder --since "$STARTED_AT" -p warning --no-pager 2>/dev/null \
    | grep -vc '^-- No entries --$' || true)
[[ "$warning_lines" -eq 0 ]] || bad "recorder warning/error lines=$warning_lines"

service_result="not-run"
service_exit="not-run"
if [[ "$fail" -eq 0 ]]; then
    systemctl start bp-storage-maintenance.service
    service_result=$(systemctl show bp-storage-maintenance.service -p Result --value)
    service_exit=$(systemctl show bp-storage-maintenance.service -p ExecMainStatus --value)
    [[ "$service_result" == "success" ]] || bad "systemd Result=$service_result"
    [[ "$service_exit" == "0" ]] || bad "systemd exit=$service_exit"
fi

systemctl enable --now bp-storage-disk-health.timer >/dev/null
if [[ "$fail" -eq 0 ]]; then
    systemctl enable --now bp-storage-maintenance.timer >/dev/null
else
    systemctl disable --now bp-storage-maintenance.timer >/dev/null 2>&1 || true
fi

maint_timer=$(systemctl is-enabled bp-storage-maintenance.timer 2>/dev/null || true)
disk_timer=$(systemctl is-enabled bp-storage-disk-health.timer 2>/dev/null || true)

if [[ "$fail" -eq 0 ]]; then
    [[ "$maint_timer" == "enabled" ]] || bad "maintenance timer=$maint_timer"
fi
[[ "$disk_timer" == "enabled" ]] || bad "disk-health timer=$disk_timer"

if [[ "$fail" -eq 0 ]]; then
    verdict="PHASE3_RETENTION_SEMANTICS_HOST_REVALIDATION_PASS"
else
    systemctl disable --now bp-storage-maintenance.timer >/dev/null 2>&1 || true
    maint_timer=$(systemctl is-enabled bp-storage-maintenance.timer 2>/dev/null || true)
    verdict="PHASE3_RETENTION_SEMANTICS_HOST_REVALIDATION_BLOCKED"
fi

cat >"$SUMMARY" <<EOF
FINAL SUMMARY
FAIL_COUNT=$fail
HEAD=$head
MANUAL_STATUS=$status
REMOVED_ARCHIVES=$removed_count
PROCESSED_INTERVALS=$interval_count
ARCHIVE_DELETE_PARITY=$parity
ARCHIVES_BEFORE=$before_archives
ARCHIVES_AFTER=$after_archives
ARCHIVE_VERIFY_EXIT=$archive_verify_rc
STALE_TEMP_FILES=$temp_count
TARGET_HOT_CUTOFF=$TARGET_HOT_CUTOFF
EARLIEST_RAW=$earliest_raw
RECORDER=$recorder
COMPACT_FEEDS=$feed_count
DISK_STATUS=$disk_status
FORENSIC_SHA=$forensic_sha
RECORDER_WARNING_LINES=$warning_lines
SYSTEMD_RESULT=$service_result
SYSTEMD_EXIT=$service_exit
MAINT_TIMER=$maint_timer
DISK_TIMER=$disk_timer
VERDICT=$verdict
EOF

cat "$SUMMARY"

if [[ "$fail" -eq 0 ]]; then
    exit 0
fi
exit 1
