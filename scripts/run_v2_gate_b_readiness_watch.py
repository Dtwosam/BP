#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import create_engine

from bp_engine.config import Settings, TradingMode
from bp_engine.v2_research.config import (
    FROZEN_COVERAGE_INPUT_SHA256,
    FROZEN_FRESHNESS_CANDIDATES_SECONDS,
    FROZEN_INCLUDE_NO_TRADE,
)
from bp_engine.v2_research.models import GateBPlanConfig, GateBResearchConfig
from bp_engine.v2_research.plan import assess_gate_b_readiness

GATE_B_ARTIFACT_NAMES = frozenset(
    {"plan.json", "selection.json", "holdout.json", "summary.json"}
)
EXPECTED_MINIMUM_CONTIGUOUS_EPOCH_SECONDS = 64800.0
EXPECTED_REQUIRED_ORDINARY_FOLDS = 3


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the Phase 14 feature-only Gate B readiness watch without "
            "reading labels or writing Gate B artifacts."
        )
    )
    parser.add_argument("--env-file", required=True)
    parser.add_argument("--safety-env-file", required=True)
    parser.add_argument("--evidence-dir", default="/var/lib/bp/evidence")
    parser.add_argument("--status-file", default=None)
    parser.add_argument("--deployed-root", default="/opt/bp")
    parser.add_argument("--expected-helper-head", required=True)
    parser.add_argument("--expected-deployed-head", required=True)
    return parser


def _read_simple_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def _require_research_zero_money(
    settings: Settings,
    env_file: Path,
    safety_env_file: Path,
) -> None:
    if settings.mode is not TradingMode.RESEARCH:
        raise RuntimeError("mode_not_research")
    if settings.live_trading_enabled is not False:
        raise RuntimeError("live_trading_enabled")
    if float(settings.max_trade_size_usd) != 0.0:
        raise RuntimeError("max_trade_size_nonzero")
    if float(settings.max_daily_loss_usd) != 0.0:
        raise RuntimeError("max_daily_loss_nonzero")

    for path in (env_file, safety_env_file):
        if not path.is_file():
            raise RuntimeError(f"safety_file_missing:{path}")
        values = _read_simple_env(path)
        expected = {
            "MODE": "research",
            "LIVE_TRADING_ENABLED": "false",
            "MAX_TRADE_SIZE_USD": "0",
            "MAX_DAILY_LOSS_USD": "0",
        }
        for key, value in expected.items():
            if values.get(key) != value:
                raise RuntimeError(f"safety_boundary_mismatch:{path}:{key}")


def _gate_b_artifacts(evidence_dir: Path) -> tuple[Path, ...]:
    if not evidence_dir.exists():
        return ()
    return tuple(
        sorted(
            (
                path
                for run_dir in evidence_dir.glob("phase14-v2-gate-b-*")
                if run_dir.is_dir()
                for path in run_dir.iterdir()
                if path.is_file() and path.name in GATE_B_ARTIFACT_NAMES
            ),
            key=lambda path: str(path),
        )
    )


def _require_no_gate_b_artifacts(evidence_dir: Path) -> None:
    artifacts = _gate_b_artifacts(evidence_dir)
    if artifacts:
        raise RuntimeError(f"gate_b_artifact_present:{artifacts[0]}")


def _require_source_truth_boundary() -> None:
    state_path = Path(__file__).resolve().parents[1] / "PROJECT_STATE.json"
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    automatic_promotion: list[object] = []
    gate_b_authorized: list[object] = []

    def walk(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "automatic_promotion":
                    automatic_promotion.append(item)
                elif key == "gate_b_authorized":
                    gate_b_authorized.append(item)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    if not automatic_promotion or any(value is not False for value in automatic_promotion):
        raise RuntimeError("automatic_promotion_boundary_changed")
    if not gate_b_authorized or any(value is not False for value in gate_b_authorized):
        raise RuntimeError("gate_b_authorization_boundary_changed")


REQUIRED_ACTIVE_UNITS = (
    "bp-recorder.service",
    "bp-postgres.service",
    "bp-dashboard-api.service",
    "bp-dashboard-web.service",
    "bp-paper-execution.service",
    "bp-live-predictor.service",
    "bp-prospective-outcomes.service",
    "bp-storage-maintenance.timer",
    "bp-storage-disk-health.timer",
    "bp-v2-forward-coverage.timer",
)


def _command(args: list[str], *, env: dict[str, str] | None = None) -> str:
    result = subprocess.run(
        args,
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or str(result.returncode)
        raise RuntimeError(f"runtime_command_failed:{args[0]}:{detail}")
    return result.stdout.strip()


def _require_runtime_preconditions(
    *,
    settings: Settings,
    env_file: Path,
    deployed_root: Path,
    expected_deployed_head: str,
) -> dict[str, Any]:
    if len(expected_deployed_head) != 40:
        raise RuntimeError("expected_deployed_head_invalid")

    observed_head = _command(
        [
            "git",
            "-c",
            f"safe.directory={deployed_root}",
            "-C",
            str(deployed_root),
            "rev-parse",
            "HEAD",
        ]
    )
    if observed_head != expected_deployed_head:
        raise RuntimeError(
            f"unexpected_deployed_head:{observed_head}:{expected_deployed_head}"
        )

    for unit in REQUIRED_ACTIVE_UNITS:
        result = subprocess.run(
            ["systemctl", "is-active", "--quiet", unit],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"runtime_unit_not_active:{unit}")
    enabled = subprocess.run(
        ["systemctl", "is-enabled", "--quiet", "bp-v2-forward-coverage.timer"],
        check=False,
        capture_output=True,
        text=True,
    )
    if enabled.returncode != 0:
        raise RuntimeError("v2_forward_coverage_timer_not_enabled")

    if int(settings.recorder_writer_workers) != 4:
        raise RuntimeError(
            f"recorder_config_worker_count_not_4:{settings.recorder_writer_workers}"
        )

    child_env = os.environ.copy()
    child_env.pop("PYTHONPATH", None)
    storage_output = _command(
        [
            str(deployed_root / ".venv" / "bin" / "python"),
            str(deployed_root / "scripts" / "storage_maintenance.py"),
            "disk-health",
            "--env-file",
            str(env_file),
        ],
        env=child_env,
    )
    storage = json.loads(storage_output)
    if storage.get("status") != "ok":
        raise RuntimeError(f"storage_health_not_ok:{storage.get('status')}")
    if storage.get("storage_mode") != "partitioned":
        raise RuntimeError(f"storage_mode_not_partitioned:{storage.get('storage_mode')}")
    guards = storage.get("guards") or {}
    for name in ("maintenance_fresh", "current_partition_present", "retention_current"):
        if guards.get(name) is not True:
            raise RuntimeError(f"storage_guard_not_satisfied:{name}")

    return {
        "deployed_head": observed_head,
        "recorder_writer_workers": int(settings.recorder_writer_workers),
        "storage_status": storage["status"],
        "storage_mode": storage["storage_mode"],
        "storage_guards": {
            "maintenance_fresh": True,
            "current_partition_present": True,
            "retention_current": True,
        },
        "storage_free_bytes": int(storage.get("free_bytes", 0)),
    }


def _require_helper_head(expected_helper_head: str) -> None:
    if len(expected_helper_head) != 40:
        raise RuntimeError("expected_helper_head_invalid")
    release_name = Path(__file__).resolve().parents[1].name
    if release_name != expected_helper_head:
        raise RuntimeError(
            f"unexpected_helper_release:{release_name}:{expected_helper_head}"
        )


def _validate_payload(payload: dict[str, Any]) -> None:
    if payload.get("labels_read") is not False:
        raise RuntimeError("readiness_read_labels")
    if payload.get("plan_artifact_written") is not False:
        raise RuntimeError("readiness_wrote_plan_artifact")
    if payload.get("selection_artifact_written") is not False:
        raise RuntimeError("readiness_wrote_selection_artifact")
    if payload.get("holdout_touched") is not False:
        raise RuntimeError("readiness_touched_holdout")
    if (
        payload.get("minimum_contiguous_epoch_seconds")
        != EXPECTED_MINIMUM_CONTIGUOUS_EPOCH_SECONDS
    ):
        raise RuntimeError("minimum_contiguous_epoch_changed")
    if payload.get("required_ordinary_folds") != EXPECTED_REQUIRED_ORDINARY_FOLDS:
        raise RuntimeError("ordinary_fold_requirement_changed")
    if payload.get("coverage_input_sha256") != FROZEN_COVERAGE_INPUT_SHA256:
        raise RuntimeError("coverage_preregistration_hash_changed")
    if payload.get("freshness_candidates_seconds") != list(
        FROZEN_FRESHNESS_CANDIDATES_SECONDS
    ):
        raise RuntimeError("freshness_candidate_grid_changed")
    if payload.get("include_no_trade") is not FROZEN_INCLUDE_NO_TRADE:
        raise RuntimeError("include_no_trade_boundary_changed")


def _compact_status(
    payload: dict[str, Any],
    *,
    expected_helper_head: str,
    runtime: dict[str, Any],
) -> dict[str, Any]:
    rejections = payload.get("candidate_rejections") or []
    last_rejection = rejections[-1] if rejections else None
    return {
        "checked_at": datetime.now(UTC).isoformat(),
        "helper_head": expected_helper_head,
        **runtime,
        "ready": bool(payload["ready"]),
        "market_count": int(payload["market_count"]),
        "market_start_at": payload["market_start_at"],
        "market_end_at": payload["market_end_at"],
        "available_span_seconds": float(payload["available_span_seconds"]),
        "minimum_contiguous_epoch_seconds": float(
            payload["minimum_contiguous_epoch_seconds"]
        ),
        "required_ordinary_folds": int(payload["required_ordinary_folds"]),
        "analysis_start_at": payload.get("analysis_start_at"),
        "eligible_fold_count": int(payload["eligible_fold_count"]),
        "final_holdout_market_count": int(payload["final_holdout_market_count"]),
        "analysis_start_attempt_count": int(payload["analysis_start_attempt_count"]),
        "candidate_rejection_count": len(rejections),
        "last_candidate_rejection": last_rejection,
        "blocking_reason": payload.get("blocking_reason"),
        "would_plan_sha256": payload.get("would_plan_sha256"),
        "labels_read": False,
        "plan_artifact_written": False,
        "selection_artifact_written": False,
        "holdout_touched": False,
        "gate_b_artifact_count": 0,
        "coverage_input_sha256": payload["coverage_input_sha256"],
        "freshness_candidates_seconds": payload["freshness_candidates_seconds"],
        "include_no_trade": payload["include_no_trade"],
    }


def _write_status(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary_path = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        temporary_path.unlink(missing_ok=True)


def run(
    *,
    env_file: Path,
    safety_env_file: Path,
    evidence_dir: Path,
    status_file: Path | None,
    deployed_root: Path,
    expected_helper_head: str,
    expected_deployed_head: str,
) -> dict[str, Any]:
    _require_helper_head(expected_helper_head)
    _require_no_gate_b_artifacts(evidence_dir)
    _require_source_truth_boundary()

    settings = Settings(_env_file=env_file)
    _require_research_zero_money(settings, env_file, safety_env_file)
    _require_runtime_preconditions(
        settings=settings,
        env_file=env_file,
        deployed_root=deployed_root,
        expected_deployed_head=expected_deployed_head,
    )

    engine = create_engine(settings.database_url)
    try:
        with engine.connect() as connection:
            with connection.begin():
                if connection.dialect.name == "postgresql":
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                payload = assess_gate_b_readiness(
                    connection,
                    GateBPlanConfig(),
                    GateBResearchConfig(),
                )
    finally:
        engine.dispose()

    _validate_payload(payload)
    _require_no_gate_b_artifacts(evidence_dir)
    runtime = _require_runtime_preconditions(
        settings=settings,
        env_file=env_file,
        deployed_root=deployed_root,
        expected_deployed_head=expected_deployed_head,
    )

    status = _compact_status(
        payload,
        expected_helper_head=expected_helper_head,
        runtime=runtime,
    )
    if status_file is not None:
        _write_status(status_file, status)
    return status


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        status = run(
            env_file=Path(args.env_file),
            safety_env_file=Path(args.safety_env_file),
            evidence_dir=Path(args.evidence_dir),
            status_file=Path(args.status_file) if args.status_file else None,
            deployed_root=Path(args.deployed_root),
            expected_helper_head=args.expected_helper_head,
            expected_deployed_head=args.expected_deployed_head,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc

    print(json.dumps(status, sort_keys=True, separators=(",", ":")))
    print("PHASE14_V2_GATE_B_READINESS_WATCH=PASS")
    print(f"READY={str(status['ready']).lower()}")
    print("HOLDOUT_TOUCHED=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
