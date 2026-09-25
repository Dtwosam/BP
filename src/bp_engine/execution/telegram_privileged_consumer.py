from __future__ import annotations

import json
import os
import secrets
import stat
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_privileged_handoff import (
    PrivilegedHandoffContractError,
    verify_privileged_handoff_contract,
)

MAX_JSON_BYTES = 256 * 1024
ACTIVATION_MAX_SECONDS = 45
MARKET_END_SAFETY_SECONDS = 10
SECOND_CANARY_ATTEMPT_BASENAME = "second-canary.attempt.json"


class PrivilegedConsumerError(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PrivilegedConsumerError(f"{label} is not readable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise PrivilegedConsumerError(f"{label} must be a regular non-symlink file")
    if info.st_size <= 0 or info.st_size > MAX_JSON_BYTES:
        raise PrivilegedConsumerError(f"{label} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PrivilegedConsumerError(f"{label} JSON invalid") from exc
    if not isinstance(payload, Mapping):
        raise PrivilegedConsumerError(f"{label} must contain a JSON object")
    return dict(payload)


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise PrivilegedConsumerError("consumer state root must be a directory")
    os.chmod(path, 0o700)


def _write_exclusive_json(path: Path, payload: Mapping[str, Any]) -> None:
    data = _json_bytes(payload)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise PrivilegedConsumerError(f"{path.name} already exists") from exc
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_json(path: Path, payload: Mapping[str, Any]) -> None:
    data = _json_bytes(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _engage_kill_switch(path: Path, reason: str) -> None:
    _replace_json(
        path,
        {
            "status": "engaged",
            "reason": reason,
            "at": datetime.now(UTC).isoformat(),
        },
    )


def _release_commit_sha(path: Path) -> str:
    payload = _load_json(path, label="transport release manifest")
    value = str(payload.get("commit_sha") or "")
    if len(value) != 40 or any(ch not in "0123456789abcdef" for ch in value):
        raise PrivilegedConsumerError("transport release commit SHA invalid")
    return value


def _run_executor(
    wrapper: Path,
    payload: Mapping[str, Any],
    *,
    timeout_seconds: float,
    runner: Callable[..., subprocess.CompletedProcess[bytes]],
) -> dict[str, Any]:
    try:
        completed = runner(
            [str(wrapper)],
            input=_json_bytes(payload),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise PrivilegedConsumerError("executor invocation timed out") from exc
    if completed.returncode != 0:
        raise PrivilegedConsumerError("executor returned nonzero status")
    try:
        result = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PrivilegedConsumerError("executor result is not valid JSON") from exc
    if not isinstance(result, Mapping):
        raise PrivilegedConsumerError("executor result must be an object")
    return dict(result)


def _require_safe_health(payload: Mapping[str, Any], *, armed: bool) -> None:
    if payload.get("status") != "ok":
        raise PrivilegedConsumerError("executor health is not ok")
    geo = payload.get("geoblock")
    account = payload.get("account")
    if not isinstance(geo, Mapping) or geo.get("blocked") is not False:
        raise PrivilegedConsumerError("executor geography is blocked or unknown")
    if not isinstance(account, Mapping) or account.get("clean_for_canary") is not True:
        raise PrivilegedConsumerError("executor account is not clean for canary")
    if int(account.get("open_order_count", -1)) != 0:
        raise PrivilegedConsumerError("executor account has open orders")
    try:
        collateral = Decimal(str(account.get("collateral_balance_usd")))
    except Exception as exc:
        raise PrivilegedConsumerError("executor collateral is invalid") from exc
    if collateral < Decimal("5"):
        raise PrivilegedConsumerError("executor collateral is below canary minimum")
    if armed:
        if payload.get("activation_valid") is not True:
            raise PrivilegedConsumerError("executor activation is not valid")
        if payload.get("kill_switch_engaged") is not False:
            raise PrivilegedConsumerError("executor kill switch did not disengage")
        if payload.get("submission_ready") is not True:
            raise PrivilegedConsumerError("executor is not submission ready")
    else:
        if payload.get("kill_switch_engaged") is not True:
            raise PrivilegedConsumerError("executor is not safely idle")
        if payload.get("activation_valid") is not False:
            raise PrivilegedConsumerError("stale executor activation is still valid")
        if payload.get("submission_ready") is not False:
            raise PrivilegedConsumerError("executor unexpectedly submission ready")


def _validate_result(
    result: Mapping[str, Any],
    *,
    prepared: Mapping[str, Any],
    request_sha256: str,
    authorization_id: str,
    executor_sha256: str,
) -> None:
    for name in ("intent_id", "prediction_id", "paper_order_id"):
        if str(result.get(name) or "") != str(prepared.get(name) or ""):
            raise PrivilegedConsumerError(f"executor result {name} mismatch")
    if str(result.get("authorization_id") or "") != authorization_id:
        raise PrivilegedConsumerError("executor result authorization mismatch")
    if str(result.get("request_sha256") or "") != request_sha256:
        raise PrivilegedConsumerError("executor result request hash mismatch")
    if str(result.get("executor_sha256") or "") != executor_sha256:
        raise PrivilegedConsumerError("executor result executable hash mismatch")
    for name in ("accepted", "status", "code", "geoblock", "account_preflight"):
        if name not in result:
            raise PrivilegedConsumerError(f"executor result missing {name}")


def execute_authorized_package_once(
    *,
    package_dir: Path,
    processed_receipt_path: Path,
    executor_path: Path,
    executor_wrapper_path: Path,
    release_manifest_path: Path,
    activation_path: Path,
    kill_switch_path: Path,
    state_root: Path,
    expected_executor_sha256: str,
    observed_at: datetime,
    expected_owner_uid: int = 0,
    runner: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    now_fn: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    now_fn = now_fn or (lambda: datetime.now(UTC))
    observed = _utc(observed_at)
    _ensure_private_dir(state_root)
    identity = package_dir.name
    attempt_path = state_root / f"{identity}.attempt.json"
    result_path = state_root / f"{identity}.result.json"
    failure_path = state_root / f"{identity}.failure.json"
    payload_path = state_root / f"{identity}.submit.json"
    authorization_slot_path = state_root / SECOND_CANARY_ATTEMPT_BASENAME

    if authorization_slot_path.exists():
        return {
            "status": "already_terminal",
            "terminal_reason": "second_canary_authorization_consumed",
            "package_identity": identity,
            "authorization_slot_consumed": True,
            "retry_allowed": False,
            "executor_invoked": False,
            "real_order_submitted": False,
        }
    if result_path.exists() or failure_path.exists() or attempt_path.exists():
        return {
            "status": "already_terminal",
            "terminal_reason": "package_already_terminal",
            "package_identity": identity,
            "authorization_slot_consumed": False,
            "retry_allowed": False,
            "executor_invoked": attempt_path.exists(),
            "real_order_submitted": False,
        }

    armed = False
    attempt_started = False
    authorization_slot_consumed = False
    authorization_id = ""
    contract: dict[str, Any] | None = None
    try:
        contract = verify_privileged_handoff_contract(
            package_dir=package_dir,
            processed_receipt_path=processed_receipt_path,
            executor_path=executor_path,
            expected_executor_sha256=expected_executor_sha256,
            observed_at=observed,
            expected_owner_uid=expected_owner_uid,
        )
        prepared = _load_json(package_dir / "prepared.json", label="prepared payload")
        if prepared.get("action") != "submit":
            raise PrivilegedConsumerError("prepared payload action is not submit")
        request = prepared.get("request")
        if not isinstance(request, Mapping):
            raise PrivilegedConsumerError("prepared request missing")

        if not kill_switch_path.exists():
            raise PrivilegedConsumerError("kill switch must be engaged before handoff")

        preflight = _run_executor(
            executor_wrapper_path,
            {"action": "health"},
            timeout_seconds=10,
            runner=runner,
        )
        _require_safe_health(preflight, armed=False)
        if str(preflight.get("executor_sha256") or "") != expected_executor_sha256:
            raise PrivilegedConsumerError("preflight executor SHA-256 mismatch")

        authorization_id = "phase15-v3-canary-" + secrets.token_hex(12)
        now = _utc(now_fn())
        contract_expiry = datetime.fromisoformat(
            str(contract["expires_at"])
        ).astimezone(UTC)
        market_end = datetime.fromisoformat(
            str(prepared["market_end_at"])
        ).astimezone(UTC)
        expires = min(
            now + timedelta(seconds=ACTIVATION_MAX_SECONDS),
            contract_expiry,
            market_end - timedelta(seconds=MARKET_END_SAFETY_SECONDS),
        )
        if expires <= now:
            raise PrivilegedConsumerError("authorization expired before activation")

        activation = {
            "authorized": True,
            "git_sha": _release_commit_sha(release_manifest_path),
            "executor_sha256": expected_executor_sha256,
            "authorization_id": authorization_id,
            "issued_at": now.isoformat(),
            "expires_at": expires.isoformat(),
            "intent_id": str(contract["intent_id"]),
            "prediction_id": str(contract["prediction_id"]),
            "paper_order_id": str(contract["paper_order_id"]),
            "request_sha256": str(contract["request_sha256"]),
            "source_prediction_version": "v3-frozen-paper-v1",
            "source_execution_version": "paper-execution-v3-frozen-v1",
            "max_trade_size_usd": "10",
            "max_total_exposure_usd": "10",
            "max_daily_loss_usd": "10",
            "max_submission_attempts": 1,
            "source_truth_sha256": str(contract["source_truth_sha256"]),
            "package_manifest_sha256": str(contract["package_manifest_sha256"]),
            "dispatch_claim_sha256": str(contract["dispatch_claim_sha256"]),
        }
        submit_payload = dict(prepared)
        submit_payload["authorization_id"] = authorization_id
        _write_exclusive_json(payload_path, submit_payload)
        _replace_json(activation_path, activation)
        kill_switch_path.unlink()
        armed = True

        armed_health = _run_executor(
            executor_wrapper_path,
            {"action": "health"},
            timeout_seconds=10,
            runner=runner,
        )
        _require_safe_health(armed_health, armed=True)
        if str(armed_health.get("executor_sha256") or "") != expected_executor_sha256:
            raise PrivilegedConsumerError("armed executor SHA-256 mismatch")

        fresh_contract = verify_privileged_handoff_contract(
            package_dir=package_dir,
            processed_receipt_path=processed_receipt_path,
            executor_path=executor_path,
            expected_executor_sha256=expected_executor_sha256,
            observed_at=_utc(now_fn()),
            expected_owner_uid=expected_owner_uid,
        )
        for name in (
            "intent_id",
            "prediction_id",
            "paper_order_id",
            "request_sha256",
            "source_truth_sha256",
            "dispatch_claim_sha256",
            "package_manifest_sha256",
            "expires_at",
            "executor_sha256",
        ):
            if str(fresh_contract.get(name) or "") != str(contract.get(name) or ""):
                raise PrivilegedConsumerError(f"fresh handoff contract {name} mismatch")

        started_at = _utc(now_fn()).isoformat()
        authorization_slot = {
            "schema_version": 1,
            "status": "second_canary_network_attempt_starting",
            "package_identity": identity,
            "intent_id": str(contract["intent_id"]),
            "request_sha256": str(contract["request_sha256"]),
            "authorization_id": authorization_id,
            "executor_sha256": expected_executor_sha256,
            "started_at": started_at,
            "retry_allowed": False,
        }
        _write_exclusive_json(authorization_slot_path, authorization_slot)
        authorization_slot_consumed = True

        attempt = {
            **authorization_slot,
            "status": "executor_invocation_starting",
        }
        _write_exclusive_json(attempt_path, attempt)
        attempt_started = True

        result = _run_executor(
            executor_wrapper_path,
            submit_payload,
            timeout_seconds=20,
            runner=runner,
        )
        _validate_result(
            result,
            prepared=prepared,
            request_sha256=str(contract["request_sha256"]),
            authorization_id=authorization_id,
            executor_sha256=expected_executor_sha256,
        )
        receipt = {
            "schema_version": 1,
            "status": "executor_result_recorded",
            "package_identity": identity,
            "intent_id": str(contract["intent_id"]),
            "prediction_id": str(contract["prediction_id"]),
            "paper_order_id": str(contract["paper_order_id"]),
            "request_sha256": str(contract["request_sha256"]),
            "authorization_id": authorization_id,
            "executor_sha256": expected_executor_sha256,
            "package_manifest_sha256": str(contract["package_manifest_sha256"]),
            "dispatch_claim_sha256": str(contract["dispatch_claim_sha256"]),
            "accepted": bool(result["accepted"]),
            "executor_status": str(result["status"]),
            "executor_code": str(result["code"]),
            "external_order_id": result.get("external_order_id"),
            "cancellation": result.get("cancellation"),
            "completed_at": _utc(now_fn()).isoformat(),
            "network_submission_attempt_consumed": True,
            "authorization_slot_consumed": True,
            "retry_allowed": False,
            "executor_invoked": True,
            "official_reconciliation_required": True,
            "real_order_submitted": bool(result["accepted"]),
        }
        _write_exclusive_json(result_path, receipt)
        return {**receipt, "result_path": str(result_path)}
    except (
        OSError,
        ValueError,
        KeyError,
        PrivilegedHandoffContractError,
        PrivilegedConsumerError,
    ) as exc:
        authorization_slot_consumed = (
            authorization_slot_consumed or authorization_slot_path.exists()
        )
        failure = {
            "schema_version": 1,
            "status": "privileged_handoff_failed_closed",
            "package_identity": identity,
            "intent_id": str((contract or {}).get("intent_id") or ""),
            "request_sha256": str((contract or {}).get("request_sha256") or ""),
            "authorization_id": authorization_id,
            "error": str(exc),
            "attempt_started": attempt_started,
            "authorization_slot_consumed": authorization_slot_consumed,
            "network_submission_attempt_consumed": authorization_slot_consumed,
            "retry_allowed": False,
            "executor_invoked": attempt_started,
            "official_reconciliation_required": authorization_slot_consumed,
            "real_order_submitted": False,
        }
        try:
            _write_exclusive_json(failure_path, failure)
        except PrivilegedConsumerError:
            pass
        return {**failure, "failure_path": str(failure_path)}
    finally:
        if armed or attempt_started:
            try:
                _engage_kill_switch(kill_switch_path, "telegram-privileged-handoff-safe-stop")
            except OSError:
                pass
