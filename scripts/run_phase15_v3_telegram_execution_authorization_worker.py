from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_dispatch_ticket import (
    DispatchTicketError,
    claim_dispatch_ticket,
    create_dispatch_ticket,
)
from bp_engine.execution.telegram_execution_ready import (
    ReadyVerificationError,
    verify_ready_bundle,
)
from bp_engine.execution.telegram_origin_attestation import payload_sha256
from bp_engine.execution.telegram_pre_execution import (
    PreExecutionError,
    evaluate_signed_pre_execution_authorization,
)

MAX_JSON_BYTES = 256 * 1024


class ExecutionAuthorizationWorkerError(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ensure_private_directory(path: Path, *, label: str) -> None:
    try:
        path.mkdir(parents=True, mode=0o700)
    except FileExistsError:
        pass
    try:
        info = path.lstat()
    except OSError as exc:
        raise ExecutionAuthorizationWorkerError(
            f"{label} is not accessible"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ExecutionAuthorizationWorkerError(
            f"{label} must be a non-symlink directory"
        )
    os.chmod(path, 0o700)


def _validate_readonly_private_directory(path: Path, *, label: str) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ExecutionAuthorizationWorkerError(
            f"{label} is not accessible"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise ExecutionAuthorizationWorkerError(
            f"{label} must be a non-symlink directory"
        )
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise ExecutionAuthorizationWorkerError(
            f"{label} must not grant group or other access"
        )


def _load_private_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise ExecutionAuthorizationWorkerError(
            f"{label} is not readable"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ExecutionAuthorizationWorkerError(
            f"{label} must be a regular non-symlink file"
        )
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise ExecutionAuthorizationWorkerError(
            f"{label} mode must be 0600"
        )
    if info.st_size <= 0 or info.st_size > MAX_JSON_BYTES:
        raise ExecutionAuthorizationWorkerError(
            f"{label} size invalid"
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExecutionAuthorizationWorkerError(
            f"{label} JSON invalid"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ExecutionAuthorizationWorkerError(
            f"{label} must contain a JSON object"
        )
    return dict(payload)


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise ExecutionAuthorizationWorkerError(
            f"{path.name} already exists"
        ) from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _materialize_handoff(
    *,
    handoff_root: Path,
    prepared: Mapping[str, Any],
    approval: Mapping[str, Any],
    ready_verification: Mapping[str, Any],
    pre_execution_report: Mapping[str, Any],
    dispatch_ticket: Mapping[str, Any],
    dispatch_claim: Mapping[str, Any],
    observed_at: datetime,
) -> Path:
    identity = hashlib.sha256(
        (
            f"{dispatch_claim['intent_id']}\0"
            f"{dispatch_claim['request_sha256']}"
        ).encode()
    ).hexdigest()
    handoff_dir = handoff_root / identity
    temporary_dir = handoff_root / f".{identity}.tmp"
    if handoff_dir.exists() or handoff_dir.is_symlink():
        raise ExecutionAuthorizationWorkerError(
            "authorized handoff already exists"
        )
    if temporary_dir.exists() or temporary_dir.is_symlink():
        raise ExecutionAuthorizationWorkerError(
            "authorized handoff temporary path already exists"
        )

    if payload_sha256(prepared) != str(
        ready_verification["prepared_sha256"]
    ):
        raise ExecutionAuthorizationWorkerError(
            "prepared payload changed after ready verification"
        )
    if payload_sha256(approval) != str(
        ready_verification["approval_sha256"]
    ):
        raise ExecutionAuthorizationWorkerError(
            "approval payload changed after ready verification"
        )

    receipt = {
        "schema_version": 1,
        "status": "execution_authorized_handoff_ready",
        "intent_id": str(dispatch_claim["intent_id"]),
        "prediction_id": str(dispatch_claim["prediction_id"]),
        "paper_order_id": str(dispatch_claim["paper_order_id"]),
        "request_sha256": str(dispatch_claim["request_sha256"]),
        "prepared_sha256": str(dispatch_claim["prepared_sha256"]),
        "approval_sha256": str(dispatch_claim["approval_sha256"]),
        "approval_source_sha256": str(
            dispatch_claim["approval_source_sha256"]
        ),
        "origin_attestation_sha256": str(
            dispatch_claim["origin_attestation_sha256"]
        ),
        "source_truth_sha256": str(dispatch_claim["source_truth_sha256"]),
        "authorization_report_sha256": str(
            dispatch_claim["authorization_report_sha256"]
        ),
        "dispatch_ticket_sha256": str(
            dispatch_claim["dispatch_ticket_sha256"]
        ),
        "dispatch_claim_sha256": str(dispatch_claim["claim_sha256"]),
        "expires_at": str(dispatch_claim["expires_at"]),
        "authorized_at": observed_at.astimezone(UTC).isoformat(),
        "retry_allowed": False,
        "handoff_invoked": False,
        "executor_invoked": False,
        "real_order_submitted": False,
    }

    try:
        temporary_dir.mkdir(mode=0o700)
        for name, payload in (
            ("prepared.json", prepared),
            ("approval.json", approval),
            ("ready-verification.json", ready_verification),
            ("pre-execution.json", pre_execution_report),
            ("dispatch-ticket.json", dispatch_ticket),
            ("dispatch-claim.json", dispatch_claim),
            ("receipt.json", receipt),
        ):
            _write_private_json(temporary_dir / name, payload)

        directory_fd = os.open(
            temporary_dir,
            os.O_RDONLY | os.O_DIRECTORY,
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.rename(temporary_dir, handoff_dir)

        root_fd = os.open(
            handoff_root,
            os.O_RDONLY | os.O_DIRECTORY,
        )
        try:
            os.fsync(root_fd)
        finally:
            os.close(root_fd)
    except Exception:
        try:
            if temporary_dir.is_dir() and not temporary_dir.is_symlink():
                for path in temporary_dir.iterdir():
                    if path.is_file() and not path.is_symlink():
                        path.unlink()
                temporary_dir.rmdir()
        except OSError:
            pass
        raise
    return handoff_dir


def authorize_ready_once(
    *,
    ready_dir: Path,
    origin_key_path: Path,
    expected_origin_key_id: str,
    dispatch_claim_dir: Path,
    handoff_root: Path,
    processed_dir: Path,
    failure_dir: Path,
    observed_at: datetime,
) -> dict[str, Any]:
    identity = ready_dir.name
    processed_path = processed_dir / f"{identity}.json"
    failure_path = failure_dir / f"{identity}.json"
    if processed_path.exists() or failure_path.exists():
        return {
            "status": "already_terminal",
            "ready_name": identity,
            "retry_allowed": False,
            "handoff_invoked": False,
            "executor_invoked": False,
            "real_order_submitted": False,
        }

    dispatch_claim_consumed = False
    try:
        ready = verify_ready_bundle(
            ready_dir=ready_dir,
            origin_key_path=origin_key_path,
            expected_origin_key_id=expected_origin_key_id,
            observed_at=observed_at,
        )
        if ready.get("status") != "execution_ready_source_truth_verified":
            raise ExecutionAuthorizationWorkerError(
                "ready bundle lacks signed source truth authorization"
            )
        pre_execution = evaluate_signed_pre_execution_authorization(
            ready_verification=ready,
        )
        ticket = create_dispatch_ticket(
            pre_execution,
            created_at=observed_at,
        )
        claim = claim_dispatch_ticket(
            ticket,
            pre_execution_report=pre_execution,
            ready_verification=ready,
            project_state=None,
            observed_at=observed_at,
            state_dir=dispatch_claim_dir,
        )
        dispatch_claim_consumed = True

        prepared = _load_private_json(
            ready_dir / "prepared.json",
            label="ready prepared payload",
        )
        approval = _load_private_json(
            ready_dir / "approval.json",
            label="ready approval payload",
        )
        handoff_dir = _materialize_handoff(
            handoff_root=handoff_root,
            prepared=prepared,
            approval=approval,
            ready_verification=ready,
            pre_execution_report=pre_execution,
            dispatch_ticket=ticket,
            dispatch_claim=claim,
            observed_at=observed_at,
        )
        result = {
            "schema_version": 1,
            "status": "execution_authorized_handoff_ready",
            "ready_name": identity,
            "intent_id": str(claim["intent_id"]),
            "request_sha256": str(claim["request_sha256"]),
            "dispatch_ticket_sha256": str(
                claim["dispatch_ticket_sha256"]
            ),
            "dispatch_claim_sha256": str(claim["claim_sha256"]),
            "expires_at": str(claim["expires_at"]),
            "handoff_dir": str(handoff_dir),
            "retry_allowed": False,
            "handoff_invoked": False,
            "executor_invoked": False,
            "real_order_submitted": False,
        }
        _write_private_json(processed_path, result)
        return result
    except (
        ReadyVerificationError,
        PreExecutionError,
        DispatchTicketError,
        ExecutionAuthorizationWorkerError,
    ) as exc:
        failure = {
            "schema_version": 1,
            "status": "execution_authorization_failed_closed",
            "ready_name": identity,
            "error": str(exc),
            "dispatch_claim_consumed": dispatch_claim_consumed,
            "retry_allowed": False,
            "handoff_invoked": False,
            "executor_invoked": False,
            "real_order_submitted": False,
        }
        _write_private_json(failure_path, failure)
        return {
            **failure,
            "failure_path": str(failure_path),
        }


def authorize_pending_once(
    *,
    ready_root: Path,
    origin_key_path: Path,
    expected_origin_key_id: str,
    dispatch_claim_dir: Path,
    handoff_root: Path,
    processed_dir: Path,
    failure_dir: Path,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    _validate_readonly_private_directory(
        ready_root,
        label="execution-ready root",
    )
    for path, label in (
        (dispatch_claim_dir, "dispatch claim directory"),
        (handoff_root, "authorized handoff directory"),
        (processed_dir, "execution authorization processed directory"),
        (failure_dir, "execution authorization failure directory"),
    ):
        _ensure_private_directory(path, label=label)

    results: list[dict[str, Any]] = []
    for ready_dir in sorted(ready_root.iterdir()):
        if ready_dir.is_symlink() or not ready_dir.is_dir():
            continue
        results.append(
            authorize_ready_once(
                ready_dir=ready_dir,
                origin_key_path=origin_key_path,
                expected_origin_key_id=expected_origin_key_id,
                dispatch_claim_dir=dispatch_claim_dir,
                handoff_root=handoff_root,
                processed_dir=processed_dir,
                failure_dir=failure_dir,
                observed_at=observed_at,
            )
        )
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Persistently consume source-truth-authorized Telegram ready bundles "
            "into one-shot local handoff packages. This worker never arms or submits."
        )
    )
    parser.add_argument(
        "--ready-root",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-transport-ready"),
    )
    parser.add_argument(
        "--dispatch-claim-dir",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-dispatch-claims"),
    )
    parser.add_argument(
        "--handoff-root",
        type=Path,
        default=Path("/var/lib/bp-canary/telegram-execution-authorized"),
    )
    parser.add_argument(
        "--processed-dir",
        type=Path,
        default=Path(
            "/var/lib/bp-canary/telegram-execution-auth-processed"
        ),
    )
    parser.add_argument(
        "--failure-dir",
        type=Path,
        default=Path(
            "/var/lib/bp-canary/telegram-execution-auth-failures"
        ),
    )
    parser.add_argument("--poll-seconds", type=float, default=0.05)
    return parser.parse_args()


def _require_safe_runtime() -> tuple[Path, str]:
    if (
        os.environ.get(
            "BP_TELEGRAM_EXECUTION_AUTH_WORKER_ENABLED",
            "no",
        )
        != "yes"
    ):
        raise SystemExit(
            "Telegram execution authorization worker is not enabled"
        )
    if os.environ.get("MODE") != "research":
        raise SystemExit("MODE must be research")
    if os.environ.get("LIVE_TRADING_ENABLED") != "false":
        raise SystemExit("LIVE_TRADING_ENABLED must be false")
    if os.environ.get("MAX_TRADE_SIZE_USD") != "0":
        raise SystemExit("MAX_TRADE_SIZE_USD must be 0")
    if os.environ.get("MAX_DAILY_LOSS_USD") != "0":
        raise SystemExit("MAX_DAILY_LOSS_USD must be 0")
    for forbidden in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        if os.environ.get(forbidden):
            raise SystemExit(
                f"{forbidden} must not be present in execution auth worker"
            )

    origin_key_raw = os.environ.get(
        "BP_TELEGRAM_ORIGIN_KEY_FILE",
        "",
    ).strip()
    origin_key_id = os.environ.get(
        "BP_TELEGRAM_ORIGIN_KEY_ID",
        "",
    ).strip()
    if not origin_key_raw or not origin_key_id:
        raise SystemExit(
            "Telegram execution authorization worker configuration incomplete"
        )
    return Path(origin_key_raw), origin_key_id


def main() -> int:
    args = _parse_args()
    if not 0.02 <= args.poll_seconds <= 5:
        raise SystemExit("poll seconds must be within 0.02..5")
    origin_key_path, origin_key_id = _require_safe_runtime()

    while True:
        results = authorize_pending_once(
            ready_root=args.ready_root,
            origin_key_path=origin_key_path,
            expected_origin_key_id=origin_key_id,
            dispatch_claim_dir=args.dispatch_claim_dir,
            handoff_root=args.handoff_root,
            processed_dir=args.processed_dir,
            failure_dir=args.failure_dir,
            observed_at=_utc_now(),
        )
        for result in results:
            if result.get("status") != "already_terminal":
                print(json.dumps(result, sort_keys=True), flush=True)
        time.sleep(args.poll_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
