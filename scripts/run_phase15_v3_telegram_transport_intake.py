from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_transport import (
    TransportError,
    claim_transport_envelope,
    parse_transport_key,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and claim a BP Telegram transport envelope")
    parser.add_argument("envelope_path", type=Path)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-claims"),
    )
    return parser.parse_args()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise TransportError("transport envelope must contain a JSON object")
    return payload


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            os.chmod(temporary, 0o600)
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def main() -> int:
    args = _parse_args()
    if os.environ.get("BP_TELEGRAM_TRANSPORT_INTAKE_ENABLED", "no") != "yes":
        raise SystemExit("Telegram transport intake is not enabled")
    for forbidden in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
    ):
        if os.environ.get(forbidden):
            raise SystemExit(f"{forbidden} must not be present in transport intake")

    key = parse_transport_key(os.environ.get("BP_TELEGRAM_TRANSPORT_HMAC_KEY", ""))
    envelope = _load_json(args.envelope_path)
    claimed = claim_transport_envelope(
        envelope,
        key=key,
        observed_at=_utc_now(),
        state_dir=args.state_dir,
    )

    receipt_dir = args.state_dir / "receipts" / str(claimed["claim_id"])
    receipt_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(receipt_dir, 0o700)
    prepared_path = receipt_dir / "prepared.json"
    approval_path = receipt_dir / "approval.json"
    if prepared_path.exists() or approval_path.exists():
        raise TransportError("transport receipt payload already exists")
    _atomic_json(prepared_path, claimed["prepared"])
    _atomic_json(approval_path, claimed["approval"])

    print(
        json.dumps(
            {
                "status": "transport_envelope_claimed",
                "intent_id": claimed["intent_id"],
                "request_sha256": claimed["request_sha256"],
                "prepared_sha256": claimed["prepared_sha256"],
                "approval_source_sha256": claimed["approval_source_sha256"],
                "claim_sha256": claimed["claim_sha256"],
                "prepared_path": str(prepared_path),
                "approval_path": str(approval_path),
                "executor_invoked": False,
                "real_order_submitted": False,
            },
            sort_keys=True,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
