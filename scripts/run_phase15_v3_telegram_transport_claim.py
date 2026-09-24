from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_transport import (
    TransportError,
    claim_transport_envelope,
    load_transport_key_file,
)

MAX_ENVELOPE_BYTES = 256 * 1024


def _load_envelope(path: Path) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise TransportError("transport envelope is not readable") from exc
    if path.is_symlink() or not path.is_file():
        raise TransportError("transport envelope must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > MAX_ENVELOPE_BYTES:
        raise TransportError("transport envelope size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TransportError("transport envelope JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise TransportError("transport envelope must contain a JSON object")
    return dict(payload)


def _write_file(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, encoded)
        os.fsync(fd)
    finally:
        os.close(fd)


def _materialize_claim(
    *,
    verified: Mapping[str, Any],
    envelope: Mapping[str, Any],
    materialize_root: Path,
) -> dict[str, Any]:
    materialize_root.mkdir(parents=True, exist_ok=True)
    os.chmod(materialize_root, 0o700)
    claim_name = Path(str(verified["claim_path"])).stem
    final_dir = materialize_root / claim_name
    if final_dir.exists():
        raise TransportError("transport materialization already exists")

    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=materialize_root))
    os.chmod(staging, 0o700)
    try:
        _write_file(staging / "prepared.json", verified["prepared"])
        _write_file(staging / "approval.json", verified["approval"])
        _write_file(staging / "envelope.json", envelope)
        receipt = {
            "schema_version": 1,
            "status": "claimed_materialized",
            "intent_id": verified["intent_id"],
            "prediction_id": verified["prediction_id"],
            "paper_order_id": verified["paper_order_id"],
            "request_sha256": verified["request_sha256"],
            "prepared_sha256": verified["prepared_sha256"],
            "approval_sha256": verified["approval_sha256"],
            "approval_source_sha256": verified["approval_source_sha256"],
            "transport_nonce": verified["transport_nonce"],
            "transport_created_at": verified["created_at"],
            "transport_expires_at": verified["expires_at"],
            "claim_sha256": verified["claim_sha256"],
            "retry_allowed": False,
            "network_action_performed": False,
            "real_order_submitted": False,
        }
        _write_file(staging / "receipt.json", receipt)
        os.rename(staging, final_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return {
        **receipt,
        "materialized_dir": str(final_dir),
        "prepared_path": str(final_dir / "prepared.json"),
        "approval_path": str(final_dir / "approval.json"),
        "envelope_path": str(final_dir / "envelope.json"),
        "receipt_path": str(final_dir / "receipt.json"),
    }


def claim_transport(
    *,
    envelope_path: Path,
    key_path: Path,
    claim_state_dir: Path,
    materialize_root: Path,
    observed_at: datetime,
) -> dict[str, Any]:
    envelope = _load_envelope(envelope_path)
    key = load_transport_key_file(key_path)
    verified = claim_transport_envelope(
        envelope,
        key=key,
        observed_at=observed_at,
        state_dir=claim_state_dir,
    )
    try:
        return _materialize_claim(
            verified=verified,
            envelope=envelope,
            materialize_root=materialize_root,
        )
    except Exception as exc:
        raise TransportError(
            "transport claim consumed but materialization failed; retry is forbidden"
        ) from exc


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Verify and one-shot claim one Phase 15 Telegram transport envelope "
            "without arming or submitting."
        )
    )
    parser.add_argument("--envelope", type=Path, required=True)
    parser.add_argument("--key-file", type=Path, required=True)
    parser.add_argument("--claim-state-dir", type=Path, required=True)
    parser.add_argument("--materialize-root", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    try:
        result = claim_transport(
            envelope_path=args.envelope,
            key_path=args.key_file,
            claim_state_dir=args.claim_state_dir,
            materialize_root=args.materialize_root,
            observed_at=datetime.now(UTC),
        )
    except TransportError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "retry_allowed": False,
                    "network_action_performed": False,
                    "real_order_submitted": False,
                },
                sort_keys=True,
            )
        )
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
