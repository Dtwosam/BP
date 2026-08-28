from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "deploy" / "phase11_install.sh"


def test_phase11_install_is_fail_closed_and_keeps_recorder_untouched() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "NODE_VERSION=24.20.0" in script
    assert "SHASUMS256.txt" in script
    assert "sha256sum -c -" in script
    assert '[[ "$MODE" != "research"' in script
    assert '[[ "$LIVE_TRADING_ENABLED" != "false"' in script
    assert '[[ "$MAX_TRADE_SIZE_USD" != "0"' in script
    assert '[[ "$MAX_DAILY_LOSS_USD" != "0"' in script
    assert "npm\" --prefix \"$DASHBOARD_DIR\" install --ignore-scripts" in script
    assert "npm\" --prefix \"$DASHBOARD_DIR\" test" in script
    assert "npm\" --prefix \"$DASHBOARD_DIR\" run typecheck" in script
    assert "npm\" --prefix \"$DASHBOARD_DIR\" run build" in script
    assert 'install -d -o bp -g bp "$DASHBOARD_DIR/.next/cache"' in script
    assert "bp-dashboard-api.service" in script
    assert "bp-dashboard-web.service" in script
    assert "http://127.0.0.1:8787/health" in script
    assert "http://127.0.0.1:3000/" in script
    assert "rollback_dashboard" in script
    assert "RECORDER_BEFORE" in script
    assert "RECORDER_AFTER" in script
    assert "systemctl stop bp-recorder" not in script
    assert "systemctl restart bp-recorder" not in script


def test_phase11_install_never_enables_execution() -> None:
    script = SCRIPT.read_text(encoding="utf-8").lower()

    assert "private_key" not in script
    assert "wallet" not in script
    assert "place_order" not in script
    assert "live_trading_enabled=true" not in script


def test_phase11_install_cleans_snapshot_on_any_exit() -> None:
    script = SCRIPT.read_text(encoding="utf-8")

    assert "trap 'rm -f \"$snapshot_file\"' RETURN" not in script
    assert 'SNAPSHOT_FILE=""' in script
    assert '[[ -n "$SNAPSHOT_FILE" ]] && rm -f "$SNAPSHOT_FILE"' in script
