from __future__ import annotations

import argparse
import json
import os
import sys
import time
import types
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

import bp_engine.execution as execution_package
from bp_engine.config import Settings


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    os.chmod(tmp, 0o600)
    tmp.replace(path)


def _normalized(payload: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(payload, default=str))


def _load_current_modules(live_path: Path, canary_path: Path):
    live_source = live_path.read_text(encoding="utf-8")
    live_module = types.ModuleType("bp_engine.execution.live")
    live_module.__package__ = "bp_engine.execution"
    sys.modules[live_module.__name__] = live_module
    execution_package.live = live_module
    exec(compile(live_source, "<phase15_live_sidecar>", "exec"), live_module.__dict__)

    canary_source = canary_path.read_text(encoding="utf-8")
    canary_module = types.ModuleType("phase15_canary_sidecar")
    sys.modules[canary_module.__name__] = canary_module
    exec(
        compile(canary_source, "<phase15_canary_sidecar>", "exec"),
        canary_module.__dict__,
    )
    return live_module, canary_module


def _status_base(
    *,
    run_id: str,
    helper_head: str,
    activated_at: datetime,
    max_wait_seconds: int,
) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "helper_head": helper_head,
        "activated_at": activated_at.isoformat(),
        "max_wait_seconds": max_wait_seconds,
        "updated_at": _utc_now().isoformat(),
        "live_trading_enabled": False,
        "real_order_submitted": False,
        "arm_attempted": False,
        "submission_attempt_consumed": False,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--helper-head", required=True)
    parser.add_argument("--activated-at", required=True)
    parser.add_argument("--official-open-order-count", type=int, required=True)
    parser.add_argument("--collateral-balance-usd", required=True)
    parser.add_argument("--max-wait-seconds", type=int, default=7200)
    parser.add_argument("--poll-seconds", type=float, default=0.5)
    parser.add_argument("--authorized-submission-attempt-limit", type=int, default=1)
    parser.add_argument("--authorized-accepted-order-limit", type=int, default=1)
    parser.add_argument("--live-source", required=True)
    parser.add_argument("--canary-source", required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if os.environ.get("MODE") != "research":
        raise SystemExit("MODE must be research")
    if os.environ.get("LIVE_TRADING_ENABLED") != "false":
        raise SystemExit("LIVE_TRADING_ENABLED must be false")
    if os.environ.get("MAX_TRADE_SIZE_USD") != "0":
        raise SystemExit("MAX_TRADE_SIZE_USD must be 0")
    if os.environ.get("MAX_DAILY_LOSS_USD") != "0":
        raise SystemExit("MAX_DAILY_LOSS_USD must be 0")
    if "POLYMARKET_PRIVATE_KEY" in os.environ:
        raise SystemExit("wallet material must not be present on prepare watcher")
    if not 1 <= args.max_wait_seconds <= 7200:
        raise SystemExit("max wait must be within 1..7200 seconds")
    if not 0.5 <= args.poll_seconds <= 10:
        raise SystemExit("poll seconds must be within 0.5..10")
    if args.official_open_order_count != 0:
        raise SystemExit("official open order count must be zero")
    if args.authorized_submission_attempt_limit not in (1, 2):
        raise SystemExit("authorized submission attempt limit must be 1 or 2")
    if args.authorized_accepted_order_limit not in (1, 2):
        raise SystemExit("authorized accepted order limit must be 1 or 2")

    collateral = Decimal(args.collateral_balance_usd)
    if collateral < Decimal("5"):
        raise SystemExit("official collateral must be at least 5")

    state_dir = Path(args.state_dir)
    status_path = state_dir / "status.json"
    prepared_path = state_dir / "prepared.json"
    live_path = Path(args.live_source)
    canary_path = Path(args.canary_source)
    activated_at = datetime.fromisoformat(args.activated_at).astimezone(UTC)
    helper_head = args.helper_head
    if len(helper_head) != 40 or any(ch not in "0123456789abcdef" for ch in helper_head):
        raise SystemExit("helper head must be a 40-character lowercase SHA")

    live_module, canary_module = _load_current_modules(live_path, canary_path)
    interlock = live_module.InterlockDecision(eligible=True, reasons=())

    settings = Settings(_env_file=args.env_file)
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    deadline = time.monotonic() + args.max_wait_seconds
    base = _status_base(
        run_id=args.run_id,
        helper_head=helper_head,
        activated_at=activated_at,
        max_wait_seconds=args.max_wait_seconds,
    )
    _atomic_json(
        status_path,
        {
            **base,
            "status": "running",
            "reason": "waiting_for_new_frozen_v3_trade_order",
        },
    )

    persisted_intent: dict[str, Any] | None = None
    try:
        while time.monotonic() < deadline:
            report = canary_module.prepare_next_canary(
                engine=engine,
                activated_at=activated_at,
                observed_at=_utc_now(),
                interlock=interlock,
                api_healthy=True,
                official_open_order_count=args.official_open_order_count,
                collateral_balance_usd=collateral,
                authorized_submission_attempt_limit=args.authorized_submission_attempt_limit,
                authorized_accepted_order_limit=args.authorized_accepted_order_limit,
            )
            normalized = _normalized(report)
            status = str(normalized.get("status") or "")

            if status == "waiting":
                _atomic_json(
                    status_path,
                    {
                        **base,
                        "status": "running",
                        "reason": normalized.get("reason"),
                        "updated_at": _utc_now().isoformat(),
                    },
                )
                time.sleep(args.poll_seconds)
                continue

            if status == "skipped":
                _atomic_json(
                    status_path,
                    {
                        **base,
                        "status": "running",
                        "reason": "candidate_skipped",
                        "last_report": normalized,
                        "updated_at": _utc_now().isoformat(),
                    },
                )
                print(json.dumps(normalized, sort_keys=True), flush=True)
                time.sleep(args.poll_seconds)
                continue

            if status == "stopped":
                terminal = {
                    **base,
                    "status": "already_complete",
                    "last_report": normalized,
                    "updated_at": _utc_now().isoformat(),
                }
                _atomic_json(status_path, terminal)
                print(json.dumps(terminal, sort_keys=True), flush=True)
                print("PHASE15_V3_CANARY_PERSISTENT_PREPARE=ALREADY_COMPLETE", flush=True)
                return 0

            if status != "prepared":
                terminal = {
                    **base,
                    "status": "failed",
                    "reason": "canary_prepare_blocked",
                    "last_report": normalized,
                    "updated_at": _utc_now().isoformat(),
                }
                _atomic_json(status_path, terminal)
                print(json.dumps(terminal, sort_keys=True), flush=True)
                return 1

            persisted_intent = normalized
            _atomic_json(
                status_path,
                {
                    **base,
                    "status": "prepared_intent_persisted",
                    "intent_id": normalized["intent_id"],
                    "prediction_id": normalized["prediction_id"],
                    "paper_order_id": normalized["paper_order_id"],
                    "market_end_at": normalized["market_end_at"],
                    "timing": normalized.get("timing"),
                    "updated_at": _utc_now().isoformat(),
                },
            )

            prepared_payload = dict(normalized)
            prepared_payload["action"] = "submit"
            _atomic_json(prepared_path, prepared_payload)
            terminal = {
                **base,
                "status": "prepared",
                "intent_id": normalized["intent_id"],
                "prediction_id": normalized["prediction_id"],
                "paper_order_id": normalized["paper_order_id"],
                "market_end_at": normalized["market_end_at"],
                "selected_side": normalized["request"]["selected_side"],
                "target_notional_usd": normalized["request"]["target_notional_usd"],
                "limit_price": normalized["request"]["limit_price"],
                "requested_shares": normalized["request"]["requested_shares"],
                "timing": normalized.get("timing"),
                "prepared_file": str(prepared_path),
                "updated_at": _utc_now().isoformat(),
            }
            _atomic_json(status_path, terminal)
            print(json.dumps(terminal, sort_keys=True), flush=True)
            print("NO_REAL_ORDER_SUBMITTED=true", flush=True)
            print("PHASE15_V3_CANARY_PERSISTENT_PREPARE=PASS", flush=True)
            return 0

        terminal = {
            **base,
            "status": "expired",
            "reason": "no_eligible_v3_trade_within_wait_window",
            "updated_at": _utc_now().isoformat(),
        }
        _atomic_json(status_path, terminal)
        print(json.dumps(terminal, sort_keys=True), flush=True)
        print("PHASE15_V3_CANARY_PERSISTENT_PREPARE=EXPIRED", flush=True)
        return 0
    except Exception as exc:
        terminal = {
            **base,
            "status": (
                "failed_after_intent_persisted"
                if persisted_intent is not None
                else "failed"
            ),
            "reason": str(exc),
            "error_type": type(exc).__name__,
            "updated_at": _utc_now().isoformat(),
        }
        if persisted_intent is not None:
            terminal.update(
                {
                    "intent_id": persisted_intent.get("intent_id"),
                    "prediction_id": persisted_intent.get("prediction_id"),
                    "paper_order_id": persisted_intent.get("paper_order_id"),
                    "market_end_at": persisted_intent.get("market_end_at"),
                    "requires_reconciliation": True,
                }
            )
        _atomic_json(status_path, terminal)
        print(json.dumps(terminal, sort_keys=True), flush=True)
        return 1
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
