from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import reindex_v4_dedupe_primary_indexes as reindex_runner
import report_v4_db_session_owner as session_owner
import report_v4_dedupe_index_health as index_health
import report_v4_dedupe_reindex_blockers as blockers
import report_v4_dedupe_reindex_readiness as readiness
import report_v4_recorder_commit_lag as commit_lag
from sqlalchemy import create_engine, text

from bp_engine.config import Settings

RECORDER_UNIT = "bp-recorder.service"
V3_PREDICTOR_UNIT = "bp-v3-frozen-predictor.service"
V3_EXECUTION_UNIT = "bp-v3-paper-execution.service"
MAINTENANCE_SERVICE = "bp-storage-maintenance.service"
MAINTENANCE_TIMER = "bp-storage-maintenance.timer"
DISK_HEALTH_SERVICE = "bp-storage-disk-health.service"
DISK_HEALTH_TIMER = "bp-storage-disk-health.timer"
V2_SERVICE = "bp-v2-forward-coverage.service"
V2_TIMER = "bp-v2-forward-coverage.timer"
V4_SERVICE = "bp-v4-forward-coverage.service"
V4_TIMER = "bp-v4-forward-coverage.timer"

CORE_UNITS = (RECORDER_UNIT, V3_PREDICTOR_UNIT, V3_EXECUTION_UNIT)
QUIESCED_TIMERS = (MAINTENANCE_TIMER, V2_TIMER, V4_TIMER)
QUIESCED_ONESHOTS = (MAINTENANCE_SERVICE, V2_SERVICE, V4_SERVICE)
REQUIRED_TIMERS = (
    MAINTENANCE_TIMER,
    DISK_HEALTH_TIMER,
    V2_TIMER,
    V4_TIMER,
)
FAILURE_ATTRIBUTION_SAMPLES = 20
FAILURE_ATTRIBUTION_INTERVAL_SECONDS = 0.25
BOUNDED_CLEAN_WINDOW_SAMPLES = 80
BOUNDED_CLEAN_WINDOW_INTERVAL_SECONDS = 0.5


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Guarded production orchestration for V4 dedupe PK reindex"
    )
    parser.add_argument("--repo", default="/opt/bp")
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument(
        "--safety-file",
        default="/etc/bp/bp-prospective-runtime-safety.env",
    )
    parser.add_argument("--expected-deployed-head", required=True)
    parser.add_argument("--helper-head", required=True)
    parser.add_argument("--evidence-root", default="/var/lib/bp/evidence")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def _run(
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        text=True,
        capture_output=True,
    )


def _systemctl(*args: str) -> str:
    return _run("systemctl", *args).stdout.strip()


def _is_active(unit: str) -> bool:
    return _run(
        "systemctl",
        "is-active",
        "--quiet",
        unit,
        check=False,
    ).returncode == 0


def _is_enabled(unit: str) -> bool:
    return _run(
        "systemctl",
        "is-enabled",
        "--quiet",
        unit,
        check=False,
    ).returncode == 0


def _require_active(unit: str) -> None:
    if not _is_active(unit):
        raise RuntimeError(f"required unit is not active: {unit}")


def _require_timer_active_enabled(unit: str) -> None:
    if not _is_enabled(unit):
        raise RuntimeError(f"required timer is not enabled: {unit}")
    if not _is_active(unit):
        raise RuntimeError(f"required timer is not active: {unit}")


def _wait_oneshot_idle_success(unit: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        active_state = _systemctl("show", "-p", "ActiveState", "--value", unit)
        if active_state == "inactive":
            break
        if active_state not in {"active", "activating"}:
            raise RuntimeError(
                f"oneshot entered unexpected state: {unit}:{active_state}"
            )
        if time.monotonic() >= deadline:
            raise RuntimeError(f"oneshot wait timed out: {unit}")
        time.sleep(5)
    result = _systemctl("show", "-p", "Result", "--value", unit)
    if result != "success":
        raise RuntimeError(f"oneshot last result is not success: {unit}:{result}")


def _read_env(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise RuntimeError(f"required safety file is missing: {path}")
    result: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        result[key] = value
    return result


def _require_research_zero_money(
    env_file: Path,
    safety_file: Path,
) -> None:
    for path in (env_file, safety_file):
        values = _read_env(path)
        expected = {
            "MODE": "research",
            "LIVE_TRADING_ENABLED": "false",
            "MAX_TRADE_SIZE_USD": "0",
            "MAX_DAILY_LOSS_USD": "0",
        }
        for key, value in expected.items():
            if values.get(key) != value:
                raise RuntimeError(
                    f"safety setting drifted: {path}:{key}={values.get(key)!r}"
                )


def _require_automatic_promotion_false(project_state: Path) -> None:
    payload = json.loads(project_state.read_text(encoding="utf-8"))
    values: list[Any] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "automatic_promotion":
                    values.append(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    if not values or any(value is not False for value in values):
        raise RuntimeError("automatic_promotion must remain false")


def _require_recorder_binding(settings: Settings) -> None:
    expected: dict[str, object] = {
        "recorder_queue_maxsize": 50000,
        "recorder_batch_size": 500,
        "recorder_writer_workers": 4,
        "recorder_flush_interval_seconds": 0.25,
    }
    for key, value in expected.items():
        actual = getattr(settings, key)
        if isinstance(value, float):
            if abs(float(actual) - value) > 1e-9:
                raise RuntimeError(f"recorder setting drifted: {key}={actual}")
        elif actual != value:
            raise RuntimeError(f"recorder setting drifted: {key}={actual}")


def _unit_snapshot(unit: str) -> dict[str, str]:
    return {
        "main_pid": _systemctl("show", "-p", "MainPID", "--value", unit),
        "n_restarts": _systemctl("show", "-p", "NRestarts", "--value", unit),
    }


def _core_snapshot() -> dict[str, dict[str, str]]:
    return {unit: _unit_snapshot(unit) for unit in CORE_UNITS}


def _require_core_unchanged(
    before: dict[str, dict[str, str]],
) -> None:
    for unit in CORE_UNITS:
        _require_active(unit)
        after = _unit_snapshot(unit)
        if after != before[unit]:
            raise RuntimeError(
                f"core unit changed during reindex: {unit}:"
                f" before={before[unit]} after={after}"
            )


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


@contextmanager
def _readonly_connection(settings: Settings) -> Iterator[Any]:
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=5000 "
                "-c application_name=bp-v4-dedupe-pk-reindex-evidence"
            )
        },
    )
    try:
        with engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            mode = str(
                connection.execute(
                    text("SHOW default_transaction_read_only")
                ).scalar_one()
            ).lower()
            if mode != "on":
                raise RuntimeError("evidence connection is not read-only")
            yield connection
    finally:
        engine.dispose()


def _readiness_report(settings: Settings) -> dict[str, Any]:
    with _readonly_connection(settings) as connection:
        return readiness.build_report(connection, settings)


def _index_health_report(settings: Settings) -> dict[str, Any]:
    with _readonly_connection(settings) as connection:
        return index_health.build_report(connection)


def _commit_lag_report(settings: Settings) -> dict[str, Any]:
    with _readonly_connection(settings) as connection:
        return commit_lag.build_report(
            connection,
            samples=commit_lag.DEFAULT_SAMPLES,
            interval_seconds=commit_lag.DEFAULT_INTERVAL_SECONDS,
            horizon_hours=commit_lag.DEFAULT_HORIZON_HOURS,
        )


def _capture_readiness_failure_attribution(
    settings: Settings,
    evidence_dir: Path,
    *,
    stage: str,
) -> None:
    owner_path = evidence_dir / f"{stage}-session-owner.json"
    blocker_path = evidence_dir / f"{stage}-blockers.json"

    try:
        with _readonly_connection(settings) as connection:
            owner_report = session_owner.build_report(
                connection,
                long_transaction_seconds=(
                    session_owner.DEFAULT_LONG_TRANSACTION_SECONDS
                ),
            )
        _write_json(owner_path, owner_report)
    except Exception as exc:
        _write_json(
            owner_path,
            {
                "report": "v4_db_session_owner_capture_error",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )

    try:
        with _readonly_connection(settings) as connection:
            blocker_report = blockers.build_report(
                connection,
                samples=FAILURE_ATTRIBUTION_SAMPLES,
                interval_seconds=FAILURE_ATTRIBUTION_INTERVAL_SECONDS,
                long_transaction_seconds=(
                    blockers.DEFAULT_LONG_TRANSACTION_SECONDS
                ),
            )
        _write_json(blocker_path, blocker_report)
    except Exception as exc:
        _write_json(
            blocker_path,
            {
                "report": "v4_dedupe_reindex_blocker_capture_error",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )


def _only_long_transactions_block(report: dict[str, Any]) -> bool:
    signals = dict(report["signals"])
    required_true = (
        "all_primary_indexes_healthy",
        "no_invalid_indexes",
        "no_prepared_transactions",
        "transient_headroom_ok",
    )
    return (
        signals.get("reindex_readiness_pass") is False
        and signals.get("no_long_transactions") is False
        and all(signals.get(name) is True for name in required_true)
    )


def _bounded_clean_window_report(settings: Settings) -> dict[str, Any]:
    with _readonly_connection(settings) as connection:
        return blockers.build_report(
            connection,
            samples=BOUNDED_CLEAN_WINDOW_SAMPLES,
            interval_seconds=BOUNDED_CLEAN_WINDOW_INTERVAL_SECONDS,
            long_transaction_seconds=blockers.DEFAULT_LONG_TRANSACTION_SECONDS,
        )


def _bounded_clean_window_ends_clean(report: dict[str, Any]) -> bool:
    pid_summary = dict(report["pid_summary"])
    signals = dict(report["signals"])
    return (
        not list(pid_summary.get("persistent_pids", ()))
        and not list(pid_summary.get("appeared_pids", ()))
        and signals.get("persistent_long_transaction_count") == 0
        and signals.get("writer_quiesce_likely_required_for_bounded_reindex")
        is False
    )


def _require_preflight_readiness(
    report: dict[str, Any],
    *,
    settings: Settings | None = None,
    evidence_dir: Path | None = None,
    stage: str | None = None,
) -> dict[str, Any]:
    signals = dict(report["signals"])
    if signals.get("reindex_readiness_pass") is True:
        return report

    if (
        settings is not None
        and evidence_dir is not None
        and stage is not None
        and _only_long_transactions_block(report)
    ):
        bounded_report = _bounded_clean_window_report(settings)
        _write_json(
            evidence_dir / f"{stage}-bounded-clean-window.json",
            bounded_report,
        )
        if _bounded_clean_window_ends_clean(bounded_report):
            retry_report = _readiness_report(settings)
            _write_json(
                evidence_dir / f"{stage}-after-bounded-clean-window.json",
                retry_report,
            )
            retry_signals = dict(retry_report["signals"])
            if retry_signals.get("reindex_readiness_pass") is True:
                return retry_report
            report = retry_report
            signals = retry_signals

    if settings is not None and evidence_dir is not None and stage is not None:
        _capture_readiness_failure_attribution(
            settings,
            evidence_dir,
            stage=stage,
        )
    raise RuntimeError(f"reindex readiness did not pass: {signals}")


def _require_post_index_integrity(report: dict[str, Any]) -> None:
    signals = dict(report["signals"])
    if signals.get("all_primary_indexes_healthy") is not True:
        raise RuntimeError("post-reindex primary indexes are not healthy")
    if signals.get("no_invalid_indexes") is not True:
        raise RuntimeError("post-reindex invalid dedupe indexes are present")


def _git_head(repo: Path) -> str:
    return _run("git", "-C", str(repo), "rev-parse", "HEAD").stdout.strip()


def run_rollout(args: argparse.Namespace) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise RuntimeError("rollout orchestrator requires root")
    if not args.execute:
        raise RuntimeError("--execute is required")

    repo = Path(args.repo)
    env_file = Path(args.env_file)
    safety_file = Path(args.safety_file)
    if _git_head(repo) != args.expected_deployed_head:
        raise RuntimeError("deployed checkout head changed")

    _require_research_zero_money(env_file, safety_file)
    _require_automatic_promotion_false(repo / "PROJECT_STATE.json")

    settings = Settings(_env_file=str(env_file))
    _require_recorder_binding(settings)

    for unit in CORE_UNITS:
        _require_active(unit)
    for timer in REQUIRED_TIMERS:
        _require_timer_active_enabled(timer)

    _wait_oneshot_idle_success(DISK_HEALTH_SERVICE, 60)

    core_before = _core_snapshot()
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = (
        Path(args.evidence_root)
        / f"phase14-v4-dedupe-pk-reindex-{stamp}-{args.helper_head}"
    )
    evidence_dir.mkdir(parents=True, mode=0o750, exist_ok=False)

    summary: dict[str, Any] = {
        "report": "v4_dedupe_pk_reindex_rollout_v1",
        "started_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
        "status": "running",
        "helper_head": args.helper_head,
        "deployed_head": args.expected_deployed_head,
        "core_before": core_before,
        "quiesced_timers": list(QUIESCED_TIMERS),
        "quiesced_timers_restored": False,
        "effectiveness_requires_review": True,
        "safety": {
            "mode": "research",
            "live_trading_enabled": False,
            "max_trade_size_usd": 0,
            "max_daily_loss_usd": 0,
            "automatic_promotion": False,
        },
    }
    _write_json(evidence_dir / "rollout.json", summary)

    stopped_timers: list[str] = []
    try:
        for timer in QUIESCED_TIMERS:
            _systemctl("stop", timer)
            stopped_timers.append(timer)
            if _is_active(timer):
                raise RuntimeError(f"quiesced timer remained active after stop: {timer}")
            if not _is_enabled(timer):
                raise RuntimeError(f"quiesced timer became disabled: {timer}")

        for service in QUIESCED_ONESHOTS:
            _wait_oneshot_idle_success(service, 3600)
        _wait_oneshot_idle_success(DISK_HEALTH_SERVICE, 60)

        _require_research_zero_money(env_file, safety_file)
        _require_automatic_promotion_false(repo / "PROJECT_STATE.json")
        _require_core_unchanged(core_before)

        pre_readiness = _readiness_report(settings)
        _write_json(evidence_dir / "pre-readiness.json", pre_readiness)
        pre_readiness = _require_preflight_readiness(
            pre_readiness,
            settings=settings,
            evidence_dir=evidence_dir,
            stage="pre-readiness-failure",
        )

        pre_health = _index_health_report(settings)
        _write_json(evidence_dir / "pre-index-health.json", pre_health)

        pre_lag = _commit_lag_report(settings)
        _write_json(evidence_dir / "pre-commit-lag.json", pre_lag)

        _require_core_unchanged(core_before)

        mutation_readiness = _readiness_report(settings)
        _write_json(
            evidence_dir / "mutation-readiness.json",
            mutation_readiness,
        )
        mutation_readiness = _require_preflight_readiness(
            mutation_readiness,
            settings=settings,
            evidence_dir=evidence_dir,
            stage="mutation-readiness-failure",
        )

        reindex_payload = reindex_runner.run(
            settings=settings,
            evidence_path=evidence_dir / "reindex.json",
            statement_timeout_seconds=(
                reindex_runner.DEFAULT_STATEMENT_TIMEOUT_SECONDS
            ),
        )
        summary["reindex"] = {
            "status": reindex_payload["status"],
            "initial_total_primary_key_bytes": (
                reindex_payload["initial_total_primary_key_bytes"]
            ),
            "final_total_primary_key_bytes": (
                reindex_payload["final_total_primary_key_bytes"]
            ),
            "total_bytes_reclaimed": reindex_payload["total_bytes_reclaimed"],
        }

        _require_core_unchanged(core_before)

        post_health = _index_health_report(settings)
        _write_json(evidence_dir / "post-index-health.json", post_health)

        post_lag = _commit_lag_report(settings)
        _write_json(evidence_dir / "post-commit-lag.json", post_lag)

        post_readiness = _readiness_report(settings)
        _write_json(evidence_dir / "post-readiness.json", post_readiness)
        _require_post_index_integrity(post_readiness)

        _require_research_zero_money(env_file, safety_file)
        _require_automatic_promotion_false(repo / "PROJECT_STATE.json")
        _require_core_unchanged(core_before)

        summary["core_after"] = _core_snapshot()
        summary["status"] = "success"
    except Exception as exc:
        summary["status"] = "failure"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        print(f"EVIDENCE_DIR={evidence_dir}", flush=True)
        raise
    finally:
        restore_results: dict[str, dict[str, object]] = {}
        for timer in reversed(stopped_timers):
            restore = _run(
                "systemctl",
                "start",
                timer,
                check=False,
            )
            active = _is_active(timer)
            restore_results[timer] = {
                "returncode": restore.returncode,
                "active": active,
                "stderr": restore.stderr.strip(),
            }
            if restore.returncode != 0 or not active:
                summary["status"] = "failure"
        summary["timer_restore_results"] = restore_results
        summary["quiesced_timers_restored"] = (
            set(restore_results) == set(QUIESCED_TIMERS)
            and all(
                int(result["returncode"]) == 0 and bool(result["active"])
                for result in restore_results.values()
            )
        )
        summary["completed_at"] = datetime.now(UTC).isoformat()
        _write_json(evidence_dir / "rollout.json", summary)

    for timer in REQUIRED_TIMERS:
        _require_timer_active_enabled(timer)
    if summary["status"] != "success":
        raise RuntimeError(f"rollout did not finish successfully: {summary}")
    return {
        **summary,
        "evidence_dir": str(evidence_dir),
    }


def main() -> int:
    args = parse_args()
    payload = run_rollout(args)
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_DEDUPE_PK_REINDEX_GATE=PASS")
    print("EFFECTIVENESS_REQUIRES_REVIEW=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
