from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import report_v4_cache_bulk_lane_evidence as bulk_lane
import report_v4_recorder_commit_lag as commit_lag
from sqlalchemy import create_engine, text

from bp_engine.config import Settings

POSTGRES_UNIT = "bp-postgres.service"
RECORDER_UNIT = "bp-recorder.service"
V3_PREDICTOR_UNIT = "bp-v3-frozen-predictor.service"
V3_EXECUTION_UNIT = "bp-v3-paper-execution.service"
CORE_UNITS = (RECORDER_UNIT, V3_PREDICTOR_UNIT, V3_EXECUTION_UNIT)
AUX_UNITS = (
    "bp-dashboard-api.service",
    "bp-dashboard-web.service",
    "bp-paper-execution.service",
    "bp-live-predictor.service",
    "bp-prospective-outcomes.service",
)

MAINTENANCE_SERVICE = "bp-storage-maintenance.service"
MAINTENANCE_TIMER = "bp-storage-maintenance.timer"
V2_SERVICE = "bp-v2-forward-coverage.service"
V2_TIMER = "bp-v2-forward-coverage.timer"
V4_SERVICE = "bp-v4-forward-coverage.service"
V4_TIMER = "bp-v4-forward-coverage.timer"
DISK_HEALTH_SERVICE = "bp-storage-disk-health.service"
DISK_HEALTH_TIMER = "bp-storage-disk-health.timer"

CYCLE_TIMERS = (MAINTENANCE_TIMER, V2_TIMER, V4_TIMER)
CYCLE_ONESHOTS = (MAINTENANCE_SERVICE, V2_SERVICE, V4_SERVICE)
REQUIRED_TIMERS = (*CYCLE_TIMERS, DISK_HEALTH_TIMER)

EXPECTED_BATCH_SIZE = 100
EXPECTED_QUEUE_MAXSIZE = 50_000
EXPECTED_WRITER_WORKERS = 4
EXPECTED_FLUSH_INTERVAL_SECONDS = 0.25
BASELINE_SHARED_BUFFERS = "128MB"
CANDIDATE_SHARED_BUFFERS = "2GB"
MIN_HOST_TOTAL_BYTES = 7 * 1024**3
MIN_BASELINE_AVAILABLE_BYTES = 4 * 1024**3
MIN_CANDIDATE_AVAILABLE_BYTES = 2 * 1024**3
WARMUP_SECONDS = 300


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Guarded A/B experiment for PostgreSQL 2GB shared_buffers at "
            "recorder batch size 100; always restores the baseline config"
        )
    )
    parser.add_argument("--repo", default="/opt/bp")
    parser.add_argument("--env-file", default="/etc/bp/bp.env")
    parser.add_argument(
        "--safety-file",
        default="/etc/bp/bp-prospective-runtime-safety.env",
    )
    parser.add_argument("--expected-deployed-head", required=True)
    parser.add_argument("--helper-head", required=True)
    parser.add_argument("--candidate-compose", required=True)
    parser.add_argument("--evidence-root", default="/var/lib/bp/evidence")
    parser.add_argument("--execute", action="store_true")
    return parser.parse_args()


def _run(
    *args: str,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        text=True,
        capture_output=True,
        env=env,
    )


def _systemctl(*args: str, check: bool = True) -> str:
    return _run("systemctl", *args, check=check).stdout.strip()


def _is_active(unit: str) -> bool:
    return (
        _run(
            "systemctl",
            "is-active",
            "--quiet",
            unit,
            check=False,
        ).returncode
        == 0
    )


def _is_enabled(unit: str) -> bool:
    return (
        _run(
            "systemctl",
            "is-enabled",
            "--quiet",
            unit,
            check=False,
        ).returncode
        == 0
    )


def _require_active(unit: str) -> None:
    if not _is_active(unit):
        raise RuntimeError(f"required unit is not active: {unit}")


def _require_timer_active_enabled(unit: str) -> None:
    if not _is_enabled(unit):
        raise RuntimeError(f"required timer is not enabled: {unit}")
    if not _is_active(unit):
        raise RuntimeError(f"required timer is not active: {unit}")


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key] = value
    return values


def _require_research_zero_money(env_file: Path, safety_file: Path) -> None:
    expected = {
        "MODE": "research",
        "LIVE_TRADING_ENABLED": "false",
        "MAX_TRADE_SIZE_USD": "0",
        "MAX_DAILY_LOSS_USD": "0",
    }
    for path in (env_file, safety_file):
        values = _read_env(path)
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
        "recorder_queue_maxsize": EXPECTED_QUEUE_MAXSIZE,
        "recorder_batch_size": EXPECTED_BATCH_SIZE,
        "recorder_priority_batch_size": 20,
        "recorder_priority_queue_maxsize": 5_000,
        "recorder_writer_workers": EXPECTED_WRITER_WORKERS,
        "recorder_flush_interval_seconds": EXPECTED_FLUSH_INTERVAL_SECONDS,
    }
    for key, value in expected.items():
        actual = getattr(settings, key)
        if isinstance(value, float):
            if abs(float(actual) - value) > 1e-9:
                raise RuntimeError(f"recorder setting drifted: {key}={actual}")
        elif actual != value:
            raise RuntimeError(f"recorder setting drifted: {key}={actual}")


def _memory_snapshot() -> dict[str, int]:
    values: dict[str, int] = {}
    for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
        name, raw = line.split(":", 1)
        if name in {"MemTotal", "MemAvailable", "SwapTotal"}:
            values[name] = int(raw.strip().split()[0]) * 1024
    return {
        "total_bytes": values["MemTotal"],
        "available_bytes": values["MemAvailable"],
        "swap_total_bytes": values["SwapTotal"],
    }


def _require_memory(min_available_bytes: int) -> dict[str, int]:
    snapshot = _memory_snapshot()
    if snapshot["total_bytes"] < MIN_HOST_TOTAL_BYTES:
        raise RuntimeError(f"host memory too small: {snapshot}")
    if snapshot["available_bytes"] < min_available_bytes:
        raise RuntimeError(f"host available memory too low: {snapshot}")
    return snapshot


def _postgres_shared_buffers(settings: Settings) -> str:
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            return str(connection.execute(text("SHOW shared_buffers")).scalar_one())
    finally:
        engine.dispose()


def _require_postgres_shared_buffers(settings: Settings, expected: str) -> None:
    actual = _postgres_shared_buffers(settings)
    if actual != expected:
        raise RuntimeError(
            f"postgres shared_buffers mismatch: expected={expected} actual={actual}"
        )


def _guarded_candidate_warmup(seconds: int) -> None:
    """Fail back to rollback if candidate memory or critical services deteriorate."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        time.sleep(min(5.0, max(0.0, deadline - time.monotonic())))
        _require_memory(MIN_CANDIDATE_AVAILABLE_BYTES)
        for unit in (POSTGRES_UNIT, *CORE_UNITS):
            if not _is_active(unit):
                raise RuntimeError(f"service failed during cache warmup: {unit}")


def _wait_oneshot_idle_success(unit: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    while True:
        state = _systemctl("show", "-p", "ActiveState", "--value", unit)
        if state == "inactive":
            break
        if state not in {"active", "activating"}:
            raise RuntimeError(f"oneshot entered unexpected state: {unit}:{state}")
        if time.monotonic() >= deadline:
            raise RuntimeError(f"oneshot wait timed out: {unit}")
        time.sleep(5)
    result = _systemctl("show", "-p", "Result", "--value", unit)
    if result != "success":
        raise RuntimeError(f"oneshot last result is not success: {unit}:{result}")


def _quiesce_cycle_timers() -> None:
    for timer in CYCLE_TIMERS:
        _systemctl("stop", timer)
        if _is_active(timer):
            raise RuntimeError(f"timer remained active after stop: {timer}")
        if not _is_enabled(timer):
            raise RuntimeError(f"timer became disabled: {timer}")
    for service in CYCLE_ONESHOTS:
        _wait_oneshot_idle_success(service, 3600)
    _wait_oneshot_idle_success(DISK_HEALTH_SERVICE, 60)


def _restore_cycle_timers() -> None:
    for timer in reversed(CYCLE_TIMERS):
        _systemctl("start", timer)
    for timer in REQUIRED_TIMERS:
        _require_timer_active_enabled(timer)


def _stop_core_chain() -> None:
    for unit in (V3_EXECUTION_UNIT, V3_PREDICTOR_UNIT, RECORDER_UNIT):
        _systemctl("stop", unit)
        if _is_active(unit):
            raise RuntimeError(f"core unit remained active after stop: {unit}")


def _start_core_chain() -> None:
    for unit in CORE_UNITS:
        _systemctl("reset-failed", unit, check=False)
        _systemctl("start", unit)
        deadline = time.monotonic() + 45
        while not _is_active(unit):
            if time.monotonic() >= deadline:
                raise RuntimeError(f"core unit did not start: {unit}")
            time.sleep(1)


def _candidate_compose_valid(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    required = "shared_buffers=${POSTGRES_SHARED_BUFFERS:-128MB}"
    if required not in content:
        raise RuntimeError("candidate compose lacks shared_buffers override")


def _deployed_compose(repo: Path) -> Path:
    path = repo / "docker-compose.prod.yml"
    if not path.is_file():
        raise RuntimeError("deployed compose file missing")
    return path


def _postgres_identity_from_container(container_id: str) -> dict[str, str]:
    payload = json.loads(_run("docker", "inspect", container_id).stdout)
    if len(payload) != 1:
        raise RuntimeError("postgres container inspect result invalid")
    container = payload[0]
    labels = (container.get("Config") or {}).get("Labels") or {}
    project = str(labels.get("com.docker.compose.project") or "")
    service = str(labels.get("com.docker.compose.service") or "")
    if not project or service != "postgres":
        raise RuntimeError("postgres compose identity missing")

    mounts = [
        mount
        for mount in container.get("Mounts") or []
        if mount.get("Destination") == "/var/lib/postgresql/data"
    ]
    if len(mounts) != 1:
        raise RuntimeError("postgres data mount identity invalid")
    mount = mounts[0]
    source = str(mount.get("Source") or "")
    if not source:
        raise RuntimeError("postgres data mount source missing")
    return {
        "container_id": container_id,
        "compose_project": project,
        "data_mount_source": source,
        "data_mount_type": str(mount.get("Type") or ""),
        "data_mount_name": str(mount.get("Name") or ""),
    }


def _discover_postgres_identity(repo: Path, env_file: Path) -> dict[str, str]:
    compose = _deployed_compose(repo)
    container_id = _run(
        "docker",
        "compose",
        "--project-directory",
        str(repo),
        "--env-file",
        str(env_file),
        "-f",
        str(compose),
        "ps",
        "-q",
        "postgres",
    ).stdout.strip()
    if not container_id:
        raise RuntimeError("postgres container id missing")
    return _postgres_identity_from_container(container_id)


def _current_project_postgres_identity(project: str) -> dict[str, str]:
    output = _run(
        "docker",
        "ps",
        "--filter",
        f"label=com.docker.compose.project={project}",
        "--filter",
        "label=com.docker.compose.service=postgres",
        "--format",
        "{{.ID}}",
    ).stdout.splitlines()
    ids = [item.strip() for item in output if item.strip()]
    if len(ids) != 1:
        raise RuntimeError(
            f"expected exactly one running postgres container, got {len(ids)}"
        )
    return _postgres_identity_from_container(ids[0])


def _wait_postgres_ready(settings: Settings, timeout_seconds: int = 60) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _postgres_shared_buffers(settings)
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"postgres did not become queryable: {last_error}")


def _recreate_postgres(
    *,
    repo: Path,
    env_file: Path,
    compose_path: Path,
    compose_project: str,
    shared_buffers: str,
    expected_data_mount_source: str,
    settings: Settings,
) -> dict[str, str]:
    environment = dict(os.environ)
    environment["POSTGRES_SHARED_BUFFERS"] = shared_buffers
    _run(
        "docker",
        "compose",
        "-p",
        compose_project,
        "--project-directory",
        str(repo),
        "--env-file",
        str(env_file),
        "-f",
        str(compose_path),
        "up",
        "-d",
        "--force-recreate",
        "postgres",
        env=environment,
    )
    _wait_postgres_ready(settings)
    identity = _current_project_postgres_identity(compose_project)
    if identity["data_mount_source"] != expected_data_mount_source:
        raise RuntimeError(
            "postgres data mount changed during candidate recreation: "
            f"expected={expected_data_mount_source} "
            f"actual={identity['data_mount_source']}"
        )
    _require_active(POSTGRES_UNIT)
    return identity


def _core_snapshot() -> dict[str, dict[str, str]]:
    return {
        unit: {
            "active_state": _systemctl(
                "show", "-p", "ActiveState", "--value", unit
            ),
            "main_pid": _systemctl("show", "-p", "MainPID", "--value", unit),
            "n_restarts": _systemctl(
                "show", "-p", "NRestarts", "--value", unit
            ),
        }
        for unit in CORE_UNITS
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _capture_commit_lag(settings: Settings, path: Path) -> dict[str, Any]:
    engine = create_engine(
        settings.database_url,
        pool_pre_ping=True,
        connect_args={
            "options": (
                "-c default_transaction_read_only=on "
                "-c statement_timeout=5000 "
                "-c application_name=bp-v4-cache2g-batch100-ab"
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
                raise RuntimeError("commit-lag evidence connection is not read-only")
            report = commit_lag.build_report(
                connection,
                samples=commit_lag.DEFAULT_SAMPLES,
                interval_seconds=commit_lag.DEFAULT_INTERVAL_SECONDS,
                horizon_hours=commit_lag.DEFAULT_HORIZON_HOURS,
            )
    finally:
        engine.dispose()
    _write_json(path, report)
    return report


def _report_comparison(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    venues: dict[str, Any] = {}
    for venue in ("coinbase", "bybit_spot", "bybit_linear"):
        before = baseline["venues"][venue]
        after = candidate["venues"][venue]
        venues[venue] = {
            "baseline": {
                "commit_advancement_ratio": before["commit_advancement_ratio"],
                "lag_delta_seconds": before["lag_delta_seconds"],
                "commit_lag_seconds": before["commit_lag_seconds"],
            },
            "candidate": {
                "commit_advancement_ratio": after["commit_advancement_ratio"],
                "lag_delta_seconds": after["lag_delta_seconds"],
                "commit_lag_seconds": after["commit_lag_seconds"],
            },
        }

    def dedupe_phase(report: dict[str, Any]) -> dict[str, Any]:
        phase = report["writer_activity"]["phases"]["dedupe_insert"]
        return {
            "observation_count": phase["observation_count"],
            "query_age_seconds": phase["query_age_seconds"],
            "xact_age_seconds": phase["xact_age_seconds"],
            "wait_counts": phase["wait_counts"],
        }

    return {
        "venues": venues,
        "dedupe_insert": {
            "baseline": dedupe_phase(baseline),
            "candidate": dedupe_phase(candidate),
        },
        "postgresql_write_cost": {
            "baseline": baseline["postgresql_write_cost"]["derived"],
            "candidate": candidate["postgresql_write_cost"]["derived"],
        },
    }


def _capture_bulk_cache(settings: Settings, path: Path) -> dict[str, Any]:
    """Use the same two-snapshot read-only probe before and after the cache test."""
    payload = bulk_lane.capture_from_settings(settings)
    _write_json(path, payload)
    return payload


def _bulk_cache_comparison(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
) -> dict[str, Any]:
    """Evidence only. Never automatically declare the candidate acceptable."""
    kinds = sorted(set(baseline["event_progress"]) | set(candidate["event_progress"]))
    return {
        "baseline_digest_index_delta": baseline["digest_index_delta"],
        "candidate_digest_index_delta": candidate["digest_index_delta"],
        "event_progress": {
            kind: {
                "baseline": baseline["event_progress"].get(kind),
                "candidate": candidate["event_progress"].get(kind),
            }
            for kind in kinds
        },
        "candidate_evidence_complete": bool(candidate["complete"]),
        "baseline_evidence_complete": bool(baseline["complete"]),
        "manual_review_required": True,
        "automatic_candidate_promotion": False,
    }


def _validate_steady_state(
    *,
    settings: Settings,
    env_file: Path,
    safety_file: Path,
    expected_shared_buffers: str,
    min_available_bytes: int,
) -> dict[str, Any]:
    _require_research_zero_money(env_file, safety_file)
    _require_automatic_promotion_false(Path("/opt/bp/PROJECT_STATE.json"))
    _require_recorder_binding(settings)
    _require_active(POSTGRES_UNIT)
    for unit in (*CORE_UNITS, *AUX_UNITS):
        _require_active(unit)
    for timer in REQUIRED_TIMERS:
        _require_timer_active_enabled(timer)
    _require_postgres_shared_buffers(settings, expected_shared_buffers)
    return {
        "memory": _require_memory(min_available_bytes),
        "core": _core_snapshot(),
        "shared_buffers": _postgres_shared_buffers(settings),
    }


def run_experiment(args: argparse.Namespace) -> dict[str, Any]:
    # SIGTERM/SIGINT/SIGHUP should enter the rollback path instead of
    # silently terminating during candidate or baseline recreation.
    def _abort(signum, _frame):
        raise RuntimeError(f"interrupted during cache experiment: signal={signum}")

    signal.signal(signal.SIGTERM, _abort)
    signal.signal(signal.SIGINT, _abort)
    signal.signal(signal.SIGHUP, _abort)
    if os.geteuid() != 0:
        raise RuntimeError("experiment orchestrator requires root")
    if not args.execute:
        raise RuntimeError("--execute is required")

    repo = Path(args.repo)
    # The deployed checkout may not be root-owned. Trust only the exact
    # expected path for the single read-only rev-parse invocation.
    if repo != Path("/opt/bp"):
        raise RuntimeError("experiment repository must be /opt/bp")
    env_file = Path(args.env_file)
    safety_file = Path(args.safety_file)
    project_state = repo / "PROJECT_STATE.json"
    candidate_compose = Path(args.candidate_compose)
    _candidate_compose_valid(candidate_compose)

    deployed_head = _run(
        "git", "-c", "safe.directory=/opt/bp",
        "-C", str(repo), "rev-parse", "HEAD"
    ).stdout.strip()
    if deployed_head != args.expected_deployed_head:
        raise RuntimeError(
            f"deployed checkout head changed: {deployed_head}"
        )

    settings = Settings(_env_file=str(env_file))

    _require_research_zero_money(env_file, safety_file)
    _require_automatic_promotion_false(project_state)
    _require_recorder_binding(settings)
    _require_active(POSTGRES_UNIT)
    for unit in (*CORE_UNITS, *AUX_UNITS):
        _require_active(unit)
    for timer in REQUIRED_TIMERS:
        _require_timer_active_enabled(timer)
    _wait_oneshot_idle_success(DISK_HEALTH_SERVICE, 60)
    # Reject failed/running maintenance and coverage services *before* the
    # first production mutation, not after timers have already been stopped.
    for unit in CYCLE_ONESHOTS:
        _wait_oneshot_idle_success(unit, 3600)
    _require_postgres_shared_buffers(settings, BASELINE_SHARED_BUFFERS)
    baseline_memory = _require_memory(MIN_BASELINE_AVAILABLE_BYTES)
    baseline_postgres_identity = _discover_postgres_identity(repo, env_file)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = (
        Path(args.evidence_root)
        / f"phase14-v4-cache2g-batch100-ab-{stamp}-{args.helper_head}"
    )
    evidence_dir.mkdir(parents=True, mode=0o750, exist_ok=False)

    summary: dict[str, Any] = {
        "report": "v4_postgres_cache2g_batch100_ab_v1",
        "status": "running",
        "started_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
        "helper_head": args.helper_head,
        "deployed_head": args.expected_deployed_head,
        "batch_size": EXPECTED_BATCH_SIZE,
        "baseline_shared_buffers": BASELINE_SHARED_BUFFERS,
        "candidate_shared_buffers": CANDIDATE_SHARED_BUFFERS,
        "warmup_seconds": WARMUP_SECONDS,
        "always_restore_baseline": True,
        "memory_zero_swap_warning": True,
        "manual_review_required": True,
        "candidate_auto_promotion": False,
        "restart_confounds_queue_residence": True,
        "restored_baseline": False,
        "baseline_memory": baseline_memory,
        "baseline_postgres_identity": baseline_postgres_identity,
        "environment_file_mutated": False,
        "core_before": _core_snapshot(),
        "safety": {
            "mode": "research",
            "live_trading_enabled": False,
            "max_trade_size_usd": 0,
            "max_daily_loss_usd": 0,
            "automatic_promotion": False,
        },
    }
    _write_json(evidence_dir / "summary.json", summary)

    print(f"EVIDENCE_DIR={evidence_dir}", flush=True)
    print("PHASE=baseline_commit_lag", flush=True)
    baseline = _capture_commit_lag(
        settings,
        evidence_dir / "baseline-commit-lag.json",
    )
    print("PHASE=baseline_bulk_and_cache", flush=True)
    baseline_bulk_cache = _capture_bulk_cache(
        settings,
        evidence_dir / "baseline-bulk-and-cache.json",
    )

    mutation_started = False
    core_stop_started = False
    postgres_recreate_started = False
    restored = False
    try:
        print("PHASE=candidate_quiesce", flush=True)
        mutation_started = True
        _quiesce_cycle_timers()

        print("PHASE=candidate_stop_core", flush=True)
        core_stop_started = True
        _stop_core_chain()

        print("PHASE=candidate_recreate_postgres_2GB", flush=True)
        candidate_settings = Settings(_env_file=str(env_file))
        postgres_recreate_started = True
        candidate_postgres_identity = _recreate_postgres(
            repo=repo,
            env_file=env_file,
            compose_path=candidate_compose,
            compose_project=baseline_postgres_identity["compose_project"],
            shared_buffers=CANDIDATE_SHARED_BUFFERS,
            expected_data_mount_source=baseline_postgres_identity[
                "data_mount_source"
            ],
            settings=candidate_settings,
        )
        _require_postgres_shared_buffers(
            candidate_settings,
            CANDIDATE_SHARED_BUFFERS,
        )
        candidate_memory_after_restart = _require_memory(
            MIN_CANDIDATE_AVAILABLE_BYTES
        )

        print("PHASE=candidate_start_core", flush=True)
        _start_core_chain()
        _restore_cycle_timers()

        print(f"PHASE=candidate_warmup seconds={WARMUP_SECONDS}", flush=True)
        _guarded_candidate_warmup(WARMUP_SECONDS)

        candidate_state = _validate_steady_state(
            settings=candidate_settings,
            env_file=env_file,
            safety_file=safety_file,
            expected_shared_buffers=CANDIDATE_SHARED_BUFFERS,
            min_available_bytes=MIN_CANDIDATE_AVAILABLE_BYTES,
        )

        print("PHASE=candidate_commit_lag", flush=True)
        candidate = _capture_commit_lag(
            candidate_settings,
            evidence_dir / "candidate-commit-lag.json",
        )
        print("PHASE=candidate_bulk_and_cache", flush=True)
        candidate_bulk_cache = _capture_bulk_cache(
            candidate_settings,
            evidence_dir / "candidate-bulk-and-cache.json",
        )
        summary["bulk_cache_comparison"] = _bulk_cache_comparison(
            baseline_bulk_cache,
            candidate_bulk_cache,
        )
        summary["candidate_memory_after_restart"] = (
            candidate_memory_after_restart
        )
        summary["candidate_postgres_identity"] = candidate_postgres_identity
        summary["candidate_state"] = candidate_state
        summary["comparison"] = _report_comparison(baseline, candidate)
        _write_json(evidence_dir / "summary.json", summary)

        print("PHASE=restore_quiesce", flush=True)
        _quiesce_cycle_timers()
        _stop_core_chain()

        print("PHASE=restore_deployed_postgres_128MB", flush=True)
        restored_settings = Settings(_env_file=str(env_file))
        restored_postgres_identity = _recreate_postgres(
            repo=repo,
            env_file=env_file,
            compose_path=_deployed_compose(repo),
            compose_project=baseline_postgres_identity["compose_project"],
            shared_buffers=BASELINE_SHARED_BUFFERS,
            expected_data_mount_source=baseline_postgres_identity[
                "data_mount_source"
            ],
            settings=restored_settings,
        )
        _require_postgres_shared_buffers(
            restored_settings,
            BASELINE_SHARED_BUFFERS,
        )

        print("PHASE=restore_core_and_timers", flush=True)
        _start_core_chain()
        _restore_cycle_timers()
        restored_state = _validate_steady_state(
            settings=restored_settings,
            env_file=env_file,
            safety_file=safety_file,
            expected_shared_buffers=BASELINE_SHARED_BUFFERS,
            min_available_bytes=MIN_CANDIDATE_AVAILABLE_BYTES,
        )
        restored = True

        summary["restored_baseline"] = True
        summary["restored_postgres_identity"] = restored_postgres_identity
        summary["restored_state"] = restored_state
        summary["core_after"] = _core_snapshot()
        summary["status"] = "success"
        summary["completed_at"] = datetime.now(UTC).isoformat()
        _write_json(evidence_dir / "summary.json", summary)
        return {**summary, "evidence_dir": str(evidence_dir)}
    except BaseException as exc:
        summary["status"] = "failure"
        summary["error"] = f"{type(exc).__name__}: {exc}"
        print(
            f"EXPERIMENT_ERROR={type(exc).__name__}: {exc}",
            flush=True,
        )
        if mutation_started and not restored:
            print("PHASE=emergency_restore", flush=True)
            cleanup_errors: list[str] = []
            try:
                for timer in CYCLE_TIMERS:
                    _systemctl("stop", timer, check=False)
            except Exception as cleanup_exc:
                cleanup_errors.append(
                    f"timer_stop:{type(cleanup_exc).__name__}:{cleanup_exc}"
                )
            # A failure while quiescing timers must not restart the recorder
            # chain or recreate an untouched PostgreSQL container. These
            # booleans are set *before* each mutation can partially start.
            if core_stop_started:
                try:
                    for unit in (
                        V3_EXECUTION_UNIT,
                        V3_PREDICTOR_UNIT,
                        RECORDER_UNIT,
                    ):
                        _systemctl("stop", unit, check=False)
                except Exception as cleanup_exc:
                    cleanup_errors.append(
                        f"core_stop:{type(cleanup_exc).__name__}:{cleanup_exc}"
                    )
            if postgres_recreate_started:
                try:
                    cleanup_settings = Settings(_env_file=str(env_file))
                    _recreate_postgres(
                        repo=repo,
                        env_file=env_file,
                        compose_path=_deployed_compose(repo),
                        compose_project=baseline_postgres_identity[
                            "compose_project"
                        ],
                        shared_buffers=BASELINE_SHARED_BUFFERS,
                        expected_data_mount_source=baseline_postgres_identity[
                            "data_mount_source"
                        ],
                        settings=cleanup_settings,
                    )
                except Exception as cleanup_exc:
                    cleanup_errors.append(
                        f"postgres_restore:{type(cleanup_exc).__name__}:{cleanup_exc}"
                    )
            if core_stop_started:
                try:
                    _start_core_chain()
                except Exception as cleanup_exc:
                    cleanup_errors.append(
                        f"core_restore:{type(cleanup_exc).__name__}:{cleanup_exc}"
                    )
            try:
                _restore_cycle_timers()
            except Exception as cleanup_exc:
                cleanup_errors.append(
                    f"timer_restore:{type(cleanup_exc).__name__}:{cleanup_exc}"
                )
            try:
                restored_settings = Settings(_env_file=str(env_file))
                _require_postgres_shared_buffers(
                    restored_settings,
                    BASELINE_SHARED_BUFFERS,
                )
                restored_identity = _current_project_postgres_identity(
                    baseline_postgres_identity["compose_project"]
                )
                if (
                    restored_identity["data_mount_source"]
                    != baseline_postgres_identity["data_mount_source"]
                ):
                    raise RuntimeError("restored postgres data mount mismatch")
                _require_active(POSTGRES_UNIT)
                for unit in CORE_UNITS:
                    _require_active(unit)
                for timer in REQUIRED_TIMERS:
                    _require_timer_active_enabled(timer)
                if not cleanup_errors:
                    restored = True
            except Exception as cleanup_exc:
                cleanup_errors.append(
                    f"baseline_verify:{type(cleanup_exc).__name__}:{cleanup_exc}"
                )
            summary["restored_baseline"] = restored
            summary["cleanup_errors"] = cleanup_errors
        summary["completed_at"] = datetime.now(UTC).isoformat()
        _write_json(evidence_dir / "summary.json", summary)
        raise


def main() -> int:
    args = parse_args()
    try:
        payload = run_experiment(args)
    except Exception:
        print("PHASE14_V4_PG_CACHE2G_BATCH100_AB_GATE=FAIL", flush=True)
        raise
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))
    print("PHASE14_V4_PG_CACHE2G_BATCH100_AB_GATE=PASS")
    print("PRODUCTION_FINAL_SHARED_BUFFERS=128MB")
    print("EXPERIMENT_PERSISTED=false")
    print("PRODUCTION_MUTATION=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
