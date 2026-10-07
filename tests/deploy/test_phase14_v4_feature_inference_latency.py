from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "scripts" / "report_v4_feature_inference_latency.py"
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_feature_inference_latency_cloudshell.sh"
)


def test_feature_latency_helper_has_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_feature_latency_helper_is_read_only_and_exact_shadow_bound() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "v4-fresh-book-shadow-20261007T200650Z-d58b2cbedc3f.service",
        "v4-source-time-fresh-book-shadow-d58b2cbedc3f00617b8d4611e538981b2865e369",
        "v4-paper-venv-d58b2cbedc3f00617b8d4611e538981b2865e369",
        "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf",
        "0x1ac81ed6b7ee9b6c336cd537bee971851dacf908c0ded1e6bd5db5cc65549b01",
        "0xeb3058016b76daebf09c032462bd2599104ae61a64d1f08f92d9ac5cb8f2543e",
        "REPORT_READ_ONLY=true",
        "fast_live_source_active",
    ):
        assert marker in source
    for forbidden in (
        "systemctl restart",
        "systemctl stop",
        "systemctl start bp-recorder",
        "LIVE_TRADING_ENABLED=true",
    ):
        assert forbidden not in source


def test_feature_latency_report_times_all_pipeline_stages() -> None:
    source = REPORT.read_text(encoding="utf-8")
    for marker in (
        "probe_core_source_time_v4_readiness",
        "build_source_time_v4_features",
        "predict_frozen_v4_probability",
        "readiness_seconds",
        "feature_build_seconds",
        "inference_seconds",
        "statement_count",
        "p95_seconds",
        "RECORDER_BATCH_SIZE must be 100",
        "default_transaction_read_only=on",
        "PHASE14_V4_FEATURE_INFERENCE_LATENCY_STATUS=PASS",
        "DATABASE_WRITES_PERFORMED=false",
        "SERVICE_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
    ):
        assert marker in source


def test_feature_latency_runtime_checks_use_bp_permissions() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        "sudo -u bp test -x",
        "sudo -u bp test -r",
        "sudo -u bp sha256sum",
    ):
        assert marker in source
