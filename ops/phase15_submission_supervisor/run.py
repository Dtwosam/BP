from __future__ import annotations

import argparse
import base64
import fcntl
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

DEFAULT_PROJECT = "project-4397f2c0-7098-4c1c-abb"
DEFAULT_RECORDER_VM = "bp-recorder"
DEFAULT_RECORDER_ZONE = "us-east1-c"
DEFAULT_EXECUTOR_VM = "bp-v3-canary-exec"
DEFAULT_EXECUTOR_ZONE = "africa-south1-a"
AUTO_APPROVER_LABEL = "com.bp.telegram-auto-approver"
MARKET_END_GRACE_SECONDS = 20
AMBIGUOUS_ATTEMPT_GRACE_SECONDS = 45

BINDINGS = {
    "start_helper_git_blob_sha": "scripts/deploy/phase15_v3_canary_prepare_watch_start_cloudshell.sh",
    "reconcile_helper_git_blob_sha": "scripts/deploy/phase15_v3_controlled_canary_reconcile_unsubmitted_cloudshell.sh",
    "supervisor_git_blob_sha": "ops/phase15_submission_supervisor/run.py",
    "prepare_runner_git_blob_sha": "scripts/run_phase15_v3_canary_prepare_watch.py",
    "prepare_service_unit_git_blob_sha": "deploy/bp-phase15-canary-prepare-watch.service",
    "canary_git_blob_sha": "src/bp_engine/execution/canary.py",
    "live_git_blob_sha": "src/bp_engine/execution/live.py",
    "arm_helper_git_blob_sha": "scripts/deploy/phase15_v3_canary_arm_cloudshell.sh",
    "executor_git_blob_sha": "scripts/deploy/phase15_v3_canary_executor.py",
}


class SupervisorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str


@dataclass(frozen=True)
class Config:
    repo: Path
    state_root: Path
    poll_seconds: float
    project: str
    recorder_vm: str
    recorder_zone: str
    executor_vm: str
    executor_zone: str


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(value: object) -> datetime:
    raw = str(value or "")
    if not raw:
        raise SupervisorError("required timestamp missing")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)


def _emit(event: str, **fields: object) -> None:
    payload = {"event": event, "ts": _utc_now().isoformat(), **fields}
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    encoded = json.dumps(dict(payload), sort_keys=True, indent=2, default=str) + "\n"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        command,
        cwd=cwd,
        env=dict(env) if env is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=timeout,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip().splitlines()
        detail = stderr[-1] if stderr else f"exit_{completed.returncode}"
        raise SupervisorError(f"command_failed:{command[0]}:{detail[:240]}")
    return completed


def _git(repo: Path, *args: str, timeout: int = 120) -> str:
    return _run(["git", *args], cwd=repo, timeout=timeout).stdout.strip()


def sync_repo(repo: Path) -> str:
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        raise SupervisorError("managed repository missing")
    if _git(repo, "status", "--porcelain", "--untracked-files=all"):
        raise SupervisorError("managed repository is dirty")
    _git(repo, "fetch", "origin", "refs/heads/main")
    _git(repo, "switch", "--detach", "FETCH_HEAD")
    local = _git(repo, "rev-parse", "HEAD")
    remote = _git(repo, "ls-remote", "origin", "refs/heads/main").split()[0]
    if local != remote:
        raise SupervisorError("managed repository is not current main")
    verify_authorization(repo)
    return local


def verify_authorization(repo: Path) -> dict[str, Any]:
    state = json.loads((repo / "PROJECT_STATE.json").read_text(encoding="utf-8"))
    gate = state["phase_15_v3_live_canary"]
    second = gate["second_live_canary_authorization"]
    controlled = gate["controlled_auto_approved_canary_authorization"]
    auto = gate["operator_telegram_auto_approver"]
    supervisor = gate["controlled_submission_supervisor"]

    if state.get("live_trading_enabled") is not False or gate.get("live_trading_enabled") is not False:
        raise SupervisorError("global live-trading source truth changed")
    if second.get("status") != "AUTHORIZED_NOT_SUBMITTED":
        raise SupervisorError("second-canary authorization is not available")
    if int(second.get("max_network_submission_attempts", -1)) != 1:
        raise SupervisorError("second-canary network-attempt limit changed")
    if controlled.get("consumed") is not False:
        raise SupervisorError("controlled-canary authorization already consumed")
    if auto.get("status") != "ACTIVE_LIVE_AUTO_APPROVE":
        raise SupervisorError("Telegram auto-approver is not source-truth active")
    if auto.get("mode") != "live-auto-approve":
        raise SupervisorError("Telegram auto-approver mode changed")
    if supervisor.get("authorized") is not True:
        raise SupervisorError("submission supervisor is not authorized")
    if supervisor.get("status") not in {"AUTHORIZED_NOT_DEPLOYED", "ACTIVE_WAITING_FOR_REAL_SUBMISSION"}:
        raise SupervisorError("submission supervisor is terminal or unavailable")
    if supervisor.get("completed") is not False:
        raise SupervisorError("submission supervisor already completed")
    if supervisor.get("max_network_submission_attempts") != 1:
        raise SupervisorError("submission supervisor network-attempt limit changed")
    if supervisor.get("target_notional_usd") != 5:
        raise SupervisorError("submission supervisor target notional changed")

    for field, relative in BINDINGS.items():
        expected = str(supervisor.get(field) or "")
        if len(expected) != 40:
            raise SupervisorError(f"missing supervisor binding:{field}")
        actual = _git(repo, "hash-object", str(repo / relative))
        if actual != expected:
            raise SupervisorError(f"supervisor binding mismatch:{relative}")
    return state


def _gcloud_ssh(
    config: Config,
    *,
    vm: str,
    zone: str,
    command: str,
    timeout: int = 90,
) -> str:
    return _run(
        [
            "gcloud",
            "compute",
            "ssh",
            vm,
            f"--project={config.project}",
            f"--zone={zone}",
            "--quiet",
            f"--command={command}",
        ],
        timeout=timeout,
    ).stdout.strip()


def _remote_python(config: Config, *, vm: str, zone: str, source: str) -> dict[str, Any]:
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    command = (
        "sudo python3 -c "
        + repr(f"import base64;exec(base64.b64decode('{encoded}'))")
    )
    raw = _gcloud_ssh(config, vm=vm, zone=zone, command=command)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SupervisorError("remote JSON response invalid") from exc
    if not isinstance(payload, dict):
        raise SupervisorError("remote JSON response is not an object")
    return payload


def recorder_snapshot(config: Config) -> dict[str, Any]:
    source = r'''
import json
import subprocess
from pathlib import Path

root = Path("/var/lib/bp/phase15-canary-prepare-watch")
current = root / "current-run"
out = {
    "service_active": subprocess.run(
        ["systemctl", "is-active", "--quiet", "bp-phase15-canary-prepare-watch.service"]
    ).returncode == 0,
    "run_dir": None,
    "status": None,
    "prepared": None,
    "approval": None,
    "handoff": None,
}
if current.is_file():
    raw = current.read_text(encoding="utf-8").strip()
    if raw:
        run_dir = Path(raw)
        out["run_dir"] = str(run_dir)
        status_path = run_dir / "status.json"
        if status_path.is_file():
            out["status"] = json.loads(status_path.read_text(encoding="utf-8"))
        prepared_path = run_dir / "prepared.json"
        if prepared_path.is_file():
            prepared = json.loads(prepared_path.read_text(encoding="utf-8"))
            request = prepared.get("request") if isinstance(prepared.get("request"), dict) else {}
            out["prepared"] = {
                "action": prepared.get("action"),
                "intent_id": prepared.get("intent_id"),
                "prediction_id": prepared.get("prediction_id"),
                "paper_order_id": prepared.get("paper_order_id"),
                "request_sha256": prepared.get("request_sha256"),
                "market_end_at": prepared.get("market_end_at"),
                "target_notional_usd": request.get("target_notional_usd"),
            }
            intent = str(prepared.get("intent_id") or "")
            if intent:
                state_dir = Path("/var/lib/bp/phase15-canary-telegram-approval") / intent
                approval_path = state_dir / "approval.json"
                handoff_path = state_dir / "handoff-result.json"
                if approval_path.is_file():
                    approval = json.loads(approval_path.read_text(encoding="utf-8"))
                    out["approval"] = {
                        "status": approval.get("status"),
                        "intent_id": approval.get("intent_id"),
                        "approved_at": approval.get("approved_at"),
                        "expires_at": approval.get("expires_at"),
                    }
                if handoff_path.is_file():
                    handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
                    out["handoff"] = {
                        "status": handoff.get("status"),
                        "intent_id": handoff.get("intent_id"),
                        "completed_at": handoff.get("completed_at"),
                        "retry_allowed": handoff.get("retry_allowed"),
                    }
print(json.dumps(out, sort_keys=True))
'''
    return _remote_python(
        config,
        vm=config.recorder_vm,
        zone=config.recorder_zone,
        source=source,
    )


def executor_snapshot(config: Config, intent_id: str) -> dict[str, Any]:
    intent_literal = json.dumps(intent_id)
    source = f'''
import json
import subprocess
from pathlib import Path

intent = {intent_literal}
root = Path("/var/lib/bp-canary/telegram-live-handoff")
marker_path = root / "second-canary.attempt.json"

def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

marker = load(marker_path) if marker_path.is_file() else None
match_result = None
match_failure = None
for path in sorted(root.glob("*.result.json")):
    payload = load(path)
    if isinstance(payload, dict) and str(payload.get("intent_id") or "") == intent:
        match_result = payload
for path in sorted(root.glob("*.failure.json")):
    payload = load(path)
    if isinstance(payload, dict) and str(payload.get("intent_id") or "") == intent:
        match_failure = payload

health_run = subprocess.run(
    ["/opt/bp-canary/executor.sh"],
    input=b'{{"action":"health"}}',
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    check=False,
)
health = None
if health_run.returncode == 0:
    try:
        raw = json.loads(health_run.stdout.decode("utf-8"))
        health = {{
            "status": raw.get("status"),
            "kill_switch_engaged": raw.get("kill_switch_engaged"),
            "activation_valid": raw.get("activation_valid"),
            "submission_ready": raw.get("submission_ready"),
            "live_order_submitted": raw.get("live_order_submitted"),
            "open_order_count": (raw.get("account") or {{}}).get("open_order_count"),
            "clean_for_canary": (raw.get("account") or {{}}).get("clean_for_canary"),
        }}
    except Exception:
        health = None

def slim(payload):
    if not isinstance(payload, dict):
        return None
    return {{
        "status": payload.get("status"),
        "intent_id": payload.get("intent_id"),
        "request_sha256": payload.get("request_sha256"),
        "started_at": payload.get("started_at"),
        "completed_at": payload.get("completed_at"),
        "accepted": payload.get("accepted"),
        "real_order_submitted": payload.get("real_order_submitted"),
        "network_submission_attempt_consumed": payload.get("network_submission_attempt_consumed"),
        "authorization_slot_consumed": payload.get("authorization_slot_consumed"),
        "retry_allowed": payload.get("retry_allowed"),
        "error": payload.get("error"),
    }}

print(json.dumps({{
    "marker": slim(marker),
    "result": slim(match_result),
    "failure": slim(match_failure),
    "health": health,
}}, sort_keys=True))
'''
    return _remote_python(
        config,
        vm=config.executor_vm,
        zone=config.executor_zone,
        source=source,
    )


def decide(
    recorder: Mapping[str, Any],
    executor: Mapping[str, Any] | None,
    *,
    observed_at: datetime,
) -> Decision:
    prepared = recorder.get("prepared")
    status = recorder.get("status")
    status_name = str(status.get("status") or "") if isinstance(status, Mapping) else ""
    service_active = recorder.get("service_active") is True

    if not isinstance(prepared, Mapping):
        if service_active and status_name in {"running", "prepared_intent_persisted"}:
            return Decision("wait", "watcher_running")
        if status_name in {"failed", "failed_after_intent_persisted"}:
            return Decision("halt", "watcher_failed_closed")
        if status_name == "already_complete":
            return Decision("halt", "canary_reported_already_complete_without_submission_receipt")
        if not service_active:
            return Decision("start", "watcher_inactive_without_prepared_candidate")
        return Decision("wait", "waiting_for_prepared_candidate")

    intent_id = str(prepared.get("intent_id") or "")
    if not intent_id:
        return Decision("halt", "prepared_candidate_missing_intent_id")
    if prepared.get("action") != "submit":
        return Decision("halt", "prepared_candidate_action_invalid")
    try:
        target = float(prepared.get("target_notional_usd"))
    except (TypeError, ValueError):
        return Decision("halt", "prepared_candidate_notional_invalid")
    if abs(target - 5.0) > 1e-9:
        return Decision("halt", "prepared_candidate_notional_not_five")

    if not isinstance(executor, Mapping):
        return Decision("wait", "executor_snapshot_unavailable")
    marker = executor.get("marker")
    result = executor.get("result")
    failure = executor.get("failure")

    if isinstance(marker, Mapping):
        if str(marker.get("intent_id") or "") != intent_id:
            return Decision("halt", "network_attempt_marker_intent_mismatch")
        if isinstance(result, Mapping):
            if str(result.get("intent_id") or "") != intent_id:
                return Decision("halt", "executor_result_intent_mismatch")
            consumed = result.get("network_submission_attempt_consumed") is True
            slot = result.get("authorization_slot_consumed") is True
            submitted = result.get("real_order_submitted") is True
            accepted = result.get("accepted") is True
            if consumed and slot and submitted and accepted:
                return Decision("success", "real_five_dollar_submission_recorded")
            return Decision("halt", "network_attempt_terminal_without_success")
        if isinstance(failure, Mapping):
            return Decision("halt", "network_attempt_failed_closed")
        try:
            started = _iso(marker.get("started_at"))
        except SupervisorError:
            return Decision("halt", "network_attempt_marker_timestamp_invalid")
        if observed_at >= started + timedelta(seconds=AMBIGUOUS_ATTEMPT_GRACE_SECONDS):
            return Decision("halt", "network_attempt_result_ambiguous")
        return Decision("wait", "network_attempt_in_progress")

    if isinstance(result, Mapping) or isinstance(failure, Mapping):
        return Decision("halt", "terminal_executor_receipt_without_global_attempt_marker")

    health = executor.get("health")
    if not isinstance(health, Mapping):
        return Decision("wait", "executor_health_unavailable")
    if (
        health.get("status") != "ok"
        or health.get("kill_switch_engaged") is not True
        or health.get("activation_valid") is not False
        or health.get("submission_ready") is not False
        or health.get("live_order_submitted") is not False
        or int(health.get("open_order_count", -1)) != 0
        or health.get("clean_for_canary") is not True
    ):
        return Decision("halt", "executor_not_safe_idle_without_attempt_marker")

    try:
        market_end = _iso(prepared.get("market_end_at"))
    except SupervisorError:
        return Decision("halt", "prepared_candidate_market_end_invalid")
    if observed_at < market_end + timedelta(seconds=MARKET_END_GRACE_SECONDS):
        return Decision("wait", "candidate_window_not_terminal")
    return Decision("reconcile_restart", "candidate_expired_without_network_attempt")


def _auto_approver_ready() -> bool:
    uid = os.getuid()
    service = subprocess.run(
        ["launchctl", "print", f"gui/{uid}/{AUTO_APPROVER_LABEL}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    env_path = Path.home() / ".config" / "bp" / "telegram-auto-approver.env"
    if service.returncode != 0 or not env_path.is_file():
        return False
    lines = env_path.read_text(encoding="utf-8").splitlines()
    return any(line == "BP_TELEGRAM_AUTO_APPROVE=true" for line in lines)


def _reconcile(config: Config, intent_id: str) -> None:
    env = os.environ.copy()
    env["PHASE15_ACCEPT_CONTROLLED_CANARY_RECONCILIATION"] = "yes"
    env["PHASE15_EXPECT_INTENT_ID"] = intent_id
    completed = _run(
        [
            "bash",
            "scripts/deploy/phase15_v3_controlled_canary_reconcile_unsubmitted_cloudshell.sh",
        ],
        cwd=config.repo,
        env=env,
        timeout=180,
    )
    _emit("CANDIDATE_RECONCILED_WITHOUT_NETWORK_ATTEMPT", intent_id=intent_id, output=completed.stdout[-1200:])


def _start_watcher(config: Config) -> None:
    env = os.environ.copy()
    env["PHASE15_ACCEPT_PERSISTENT_PREPARE_WATCH"] = "yes"
    env.pop("PHASE15_CANARY_MAX_WAIT_SECONDS", None)
    completed = _run(
        ["bash", "scripts/deploy/phase15_v3_canary_prepare_watch_start_cloudshell.sh"],
        cwd=config.repo,
        env=env,
        timeout=180,
    )
    _emit("WATCHER_RESTARTED", output=completed.stdout[-1600:])


def _terminal(config: Config, status: str, reason: str, **fields: object) -> None:
    payload = {
        "schema_version": 1,
        "status": status,
        "reason": reason,
        "at": _utc_now().isoformat(),
        **fields,
    }
    _atomic_json(config.state_root / "terminal.json", payload)
    _emit("SUPERVISOR_TERMINAL", **payload)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(os.environ.get("BP_PHASE15_SUPERVISOR_REPO", "")),
    )
    parser.add_argument(
        "--state-root",
        type=Path,
        default=Path.home() / ".local" / "state" / "bp-phase15-submission-supervisor",
    )
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    if not str(args.repo):
        raise SystemExit("BP_PHASE15_SUPERVISOR_REPO or --repo is required")
    if not 2.0 <= args.poll_seconds <= 30.0:
        raise SystemExit("poll seconds must be within 2..30")

    config = Config(
        repo=args.repo.expanduser().resolve(),
        state_root=args.state_root.expanduser().resolve(),
        poll_seconds=args.poll_seconds,
        project=os.environ.get("PHASE15_CANARY_PROJECT", DEFAULT_PROJECT),
        recorder_vm=os.environ.get("PHASE15_CANARY_US_VM", DEFAULT_RECORDER_VM),
        recorder_zone=os.environ.get("PHASE15_CANARY_US_ZONE", DEFAULT_RECORDER_ZONE),
        executor_vm=os.environ.get("PHASE15_CANARY_EXEC_VM", DEFAULT_EXECUTOR_VM),
        executor_zone=os.environ.get("PHASE15_CANARY_EXEC_ZONE", DEFAULT_EXECUTOR_ZONE),
    )
    config.state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(config.state_root, 0o700)

    terminal_path = config.state_root / "terminal.json"
    if terminal_path.is_file():
        _emit("SUPERVISOR_ALREADY_TERMINAL", terminal=json.loads(terminal_path.read_text(encoding="utf-8")))
        return 0

    lock_path = config.state_root / "supervisor.lock"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    os.chmod(lock_path, 0o600)
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        _emit("SUPERVISOR_ALREADY_RUNNING")
        return 0

    main_sha = sync_repo(config.repo)
    _emit("SUPERVISOR_STARTED", main_sha=main_sha, poll_seconds=config.poll_seconds)

    while True:
        try:
            if not _auto_approver_ready():
                _emit("AUTO_APPROVER_NOT_READY")
                time.sleep(config.poll_seconds)
                continue

            recorder = recorder_snapshot(config)
            prepared = recorder.get("prepared")
            executor = None
            if isinstance(prepared, Mapping):
                intent_id = str(prepared.get("intent_id") or "")
                if intent_id:
                    executor = executor_snapshot(config, intent_id)

            decision = decide(recorder, executor, observed_at=_utc_now())
            _emit(
                "SUPERVISOR_DECISION",
                action=decision.action,
                reason=decision.reason,
                run_dir=recorder.get("run_dir"),
                intent_id=(prepared or {}).get("intent_id") if isinstance(prepared, Mapping) else None,
            )

            if decision.action == "wait":
                time.sleep(config.poll_seconds)
                continue
            if decision.action == "success":
                _terminal(
                    config,
                    "real_submission_succeeded",
                    decision.reason,
                    intent_id=str((prepared or {}).get("intent_id") or ""),
                    executor=executor,
                )
                return 0
            if decision.action == "halt":
                _terminal(
                    config,
                    "halted_fail_closed",
                    decision.reason,
                    intent_id=str((prepared or {}).get("intent_id") or "") if isinstance(prepared, Mapping) else None,
                    recorder=recorder,
                    executor=executor,
                )
                return 0
            if decision.action == "start":
                sync_repo(config.repo)
                _start_watcher(config)
                time.sleep(config.poll_seconds)
                continue
            if decision.action == "reconcile_restart":
                intent_id = str((prepared or {}).get("intent_id") or "")
                if not intent_id:
                    raise SupervisorError("reconcile decision without intent")
                sync_repo(config.repo)
                _reconcile(config, intent_id)
                sync_repo(config.repo)
                _start_watcher(config)
                time.sleep(config.poll_seconds)
                continue
            raise SupervisorError(f"unknown decision:{decision.action}")
        except (OSError, subprocess.TimeoutExpired, SupervisorError, json.JSONDecodeError) as exc:
            _emit("SUPERVISOR_TRANSIENT_ERROR", error_type=type(exc).__name__, error=str(exc)[:500])
            time.sleep(max(config.poll_seconds, 5.0))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        _emit("SUPERVISOR_CRASH", error_type=type(exc).__name__, error=str(exc)[:500])
        raise
