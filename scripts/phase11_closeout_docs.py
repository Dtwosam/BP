from __future__ import annotations

import json
from collections import OrderedDict
from pathlib import Path

ACCEPTED = "126959eaef973b061c3c7ea619b6d6313f3f4e4e"
UPDATED_AT = "2026-08-28T15:38:49Z"


def update_project_state() -> None:
    path = Path("PROJECT_STATE.json")
    state = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)
    state["source_of_truth_version"] = "0.11.0"
    state["updated_at"] = UPDATED_AT
    state["current_phase"] = 12
    state["current_phase_name"] = "paper execution"
    state["status"] = "PHASE_11_COMPLETE_PHASE_12_READY"
    state["trading_mode"] = "RESEARCH"
    state["live_trading_enabled"] = False

    checkpoint = OrderedDict(
        [
            ("host_accepted_operational_commit", ACCEPTED),
            ("ci_run", 1223),
            ("ci_passed", True),
            ("host_acceptance_verdict", "PASS"),
            ("permanent_install_verdict", "PASS"),
            ("dashboard_api_listener", "127.0.0.1:8787"),
            ("dashboard_web_listener", "127.0.0.1:3000"),
            ("host_acceptance_api_listener", "127.0.0.1:18787"),
            ("host_acceptance_web_listener", "127.0.0.1:13000"),
            ("node_version", "v24.20.0"),
            ("recorder_active_after_install", True),
            ("postgres_active_after_install", True),
            ("dashboard_api_active_after_install", True),
            ("dashboard_web_active_after_install", True),
            ("mutation_post_status", 405),
            ("active_markets_at_host_acceptance", 4),
            ("feed_rows_at_host_acceptance", 4),
            ("performance_rows_at_host_acceptance", 2),
            ("prediction_history_rows_at_host_acceptance", 26),
            ("evaluated_predictions_at_host_acceptance", 0),
            ("paper_pnl_status", "UNAVAILABLE_UNTIL_PHASE_12"),
            ("execution_available", False),
            ("paper_execution_available", False),
            ("live_trading_enabled", False),
            ("money_execution_path_added", False),
            ("profitability_claim", False),
            (
                "host_evidence_dir",
                "/var/lib/bp/evidence/phase11-dashboard/20260828T151015Z",
            ),
            ("sanitized_evidence", "docs/evidence/phase-11-closeout-20260828.json"),
        ]
    )

    rebuilt = OrderedDict()
    for key, value in state.items():
        if key == "completed":
            rebuilt["phase_11_checkpoint"] = checkpoint
        rebuilt[key] = value
    state = rebuilt

    additions = [
        "Phase 11 read-only Python dashboard snapshot API implemented with fail-closed RESEARCH mode and no mutation/execution path",
        "Phase 11 Next.js operator dashboard implemented for active markets, model/market state, feed health, immutable prediction history, evaluation-backed performance, and current mode",
        "Phase 11 localhost-only hardened systemd deployment and rollback-capable installer implemented",
        "Phase 11 production host acceptance passed on the exact operational commit with 4 active markets, 4 feed rows, 2 performance rows, and 26 prediction-history rows",
        "Phase 11 permanent production install passed with recorder/PostgreSQL continuity, loopback-only dashboard listeners, and POST mutation rejection",
        "Phase 11 closeout evidence recorded",
    ]
    completed = list(state.get("completed", []))
    for item in additions:
        if item not in completed:
            completed.append(item)
    state["completed"] = completed
    state["not_completed"] = ["Paper trading", "Live trading"]
    state["next_actions"] = [
        "Begin Phase 12 Paper Execution",
        "Implement simulated orders through the same execution interface intended for later live trading, without wallet/signing/order placement",
        "Model executable bid/ask, depth, partial fills, latency, slippage, cancellations, expiry, and fees",
        "Reconcile every paper order/fill deterministically to immutable Phase 10 live prediction signals and preserve no-trade decisions",
        "Expose paper positions, fills, realized/unrealized paper P&L, execution diagnostics, and reconciliation status through the Phase 11 dashboard",
        "Keep LIVE_TRADING_ENABLED=false and zero real-money trade-size/daily-loss limits; Phase 14 live-readiness and explicit authorization remain mandatory before real trading",
    ]
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def update_changelog() -> None:
    path = Path("docs/CHANGELOG.md")
    text = path.read_text(encoding="utf-8")
    if "## 0.11.0 — 28 August 2026" in text:
        return
    section = """## 0.11.0 — 28 August 2026

Phase 11 — Dashboard V1 — closed after exact-head CI, isolated production-host acceptance, and permanent host installation on operational candidate `126959eaef973b061c3c7ea619b6d6313f3f4e4e`. CI run #1223 passed 511 Python tests plus lint, deployment validation, health, dashboard tests, strict TypeScript typecheck, and the Next.js production build.

Dashboard V1 adds a read-only Python snapshot API and a localhost-only Next.js operator surface for active markets, model probabilities, observed market prices, edge/action, four-feed health, immutable prediction history, evaluation-backed performance/calibration, and current safety mode. The API rejects mutation requests, exposes no wallet/signing/order path, and keeps paper P&L explicitly `UNAVAILABLE_UNTIL_PHASE_12` with `execution_available=false` and `paper_execution_available=false`.

Production acceptance uncovered and corrected three host-only deployment/read-model defects without weakening safety: the temporary Node npm probe needed the downloaded Node directory in `PATH`; feed health had to derive from the recorder's authoritative compact `market_state_1s` evidence when the unused `feed_status` table was empty; and the initial compact-state fallback had to use bounded latest-row lookups rather than a full-table aggregate scan. Regression tests cover those cases. The final isolated host acceptance returned `PHASE11_HOST_ACCEPTANCE=PASS` with 4 active markets, 4 feed rows, 2 performance rows, 26 prediction-history rows, zero evaluated predictions, localhost-only candidate listeners, and the recorder active.

Permanent installation on the same SHA returned `PHASE11_INSTALL=PASS`. Node `v24.20.0` was checksum-verified; `bp-recorder`, PostgreSQL, dashboard API, and dashboard web were active after installation; listeners remained only `127.0.0.1:8787` and `127.0.0.1:3000`; API health reported `RESEARCH` with live trading disabled; and POST mutation requests returned HTTP 405. Zero evaluated predictions remains valid append-only evidence and is not converted into a performance or profitability claim.

Sanitized closeout evidence is stored in `docs/evidence/phase-11-closeout-20260828.json`. Phase 12 — Paper Execution — is now the next permitted build-order phase. It must simulate bid/ask, depth, partial fills, latency, slippage, cancellations, expiry, and fees through the same interface intended for later live trading, reconcile paper trades to immutable signals, and surface paper execution/P&L diagnostics through the dashboard. Live trading remains disabled and still requires the later live-readiness gate plus explicit user authorization.

"""
    marker = "# Changelog\n\n"
    if marker not in text:
        raise RuntimeError("changelog header not found")
    path.write_text(text.replace(marker, marker + section, 1), encoding="utf-8")


if __name__ == "__main__":
    update_project_state()
    update_changelog()
