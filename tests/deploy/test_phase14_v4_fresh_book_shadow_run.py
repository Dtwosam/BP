from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_fresh_book_shadow_run_cloudshell.sh"
)
RUNTIME_REQUIREMENTS = ROOT / "deploy" / "phase14-v4-paper-runtime-requirements.txt"


def test_v4_fresh_book_shadow_run_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_paper_runtime_requirements_are_exactly_pinned() -> None:
    lines = [
        line.strip()
        for line in RUNTIME_REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert lines == [
        "scikit-learn==1.9.1",
        "xgboost-cpu==3.4.1",
        "joblib==1.5.3",
    ]


def test_v4_fresh_book_shadow_preflight_is_local_only_and_prints_approval(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "$1" == "rev-parse" && "$2" == "--show-toplevel" ]]; then
  printf '%s\\n' "$FAKE_REPO_ROOT"
  exit 0
fi
if [[ "$1" == "status" && "$2" == "--porcelain" ]]; then
  exit 0
fi
if [[ "$1" == "branch" && "$2" == "--show-current" ]]; then
  printf 'main\n'
  exit 0
fi
if [[ "$1" == "rev-parse" && ( "$2" == "HEAD" || "$2" == "origin/main" ) ]]; then
  printf '%s\\n' "$FAKE_MAIN_SHA"
  exit 0
fi
case "$1" in
  fetch) exit 0 ;;
esac
exit 97
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    gcloud_sentinel = tmp_path / "gcloud-called"
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        """#!/usr/bin/env bash
printf 'called\\n' > "$FAKE_GCLOUD_SENTINEL"
exit 99
""",
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)

    main_sha = "b" * 40
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_REPO_ROOT"] = str(tmp_path)
    env["FAKE_MAIN_SHA"] = main_sha
    env["FAKE_GCLOUD_SENTINEL"] = str(gcloud_sentinel)
    env["PHASE14_V4_FRESH_BOOK_SHADOW_PREFLIGHT_ONLY"] = "true"

    completed = subprocess.run(
        ["bash", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    assert not gcloud_sentinel.exists()
    assert "PHASE14_V4_FRESH_BOOK_SHADOW_PREFLIGHT=PASS" in completed.stdout
    assert f"CANDIDATE_MAIN={main_sha}" in completed.stdout
    assert "SCIKIT_LEARN_VERSION=1.9.1" in completed.stdout
    assert "XGBOOST_VERSION=3.4.1" in completed.stdout
    assert "JOBLIB_VERSION=1.5.3" in completed.stdout
    assert "PRODUCTION_HOST_CONTACTED=false" in completed.stdout
    assert "PRODUCTION_MUTATION_PERFORMED=false" in completed.stdout
    assert "PAPER_ACTIVATION_PERFORMED=false" in completed.stdout
    assert "LIVE_TRADING_ENABLED=false" in completed.stdout
    assert "REAL_MONEY_USD=0" in completed.stdout
    assert (
        "EXPECTED_APPROVAL="
        f"I_APPROVE_PHASE14_V4_ZERO_MONEY_PAPER_SHADOW:{main_sha}:"
        "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf:"
        "1.9.1:3.4.1:1.5.3"
        in completed.stdout
    )


def test_v4_fresh_book_shadow_refuses_stale_local_main_before_gcloud(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()

    fake_git = fake_bin / "git"
    fake_git.write_text(
        """#!/usr/bin/env bash
set -eu
if [[ "$1" == "rev-parse" && "$2" == "--show-toplevel" ]]; then
  printf '%s\\n' "$FAKE_REPO_ROOT"
  exit 0
fi
if [[ "$1" == "status" && "$2" == "--porcelain" ]]; then
  exit 0
fi
if [[ "$1" == "branch" && "$2" == "--show-current" ]]; then
  printf 'main\\n'
  exit 0
fi
if [[ "$1" == "fetch" ]]; then
  exit 0
fi
if [[ "$1" == "rev-parse" && "$2" == "HEAD" ]]; then
  printf '%s\\n' "$FAKE_LOCAL_SHA"
  exit 0
fi
if [[ "$1" == "rev-parse" && "$2" == "origin/main" ]]; then
  printf '%s\\n' "$FAKE_REMOTE_SHA"
  exit 0
fi
exit 97
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    gcloud_sentinel = tmp_path / "gcloud-called"
    fake_gcloud = fake_bin / "gcloud"
    fake_gcloud.write_text(
        """#!/usr/bin/env bash
printf 'called\\n' > "$FAKE_GCLOUD_SENTINEL"
exit 99
""",
        encoding="utf-8",
    )
    fake_gcloud.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_REPO_ROOT"] = str(tmp_path)
    env["FAKE_LOCAL_SHA"] = "a" * 40
    env["FAKE_REMOTE_SHA"] = "b" * 40
    env["FAKE_GCLOUD_SENTINEL"] = str(gcloud_sentinel)

    completed = subprocess.run(
        ["bash", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode != 0
    assert not gcloud_sentinel.exists()
    assert "PHASE14_V4_FRESH_BOOK_SHADOW_RUN=FAIL:local_main_stale_update_before_run" in (
        completed.stderr
    )
    assert f"LOCAL_HEAD={'a' * 40}" in completed.stderr
    assert f"REMOTE_MAIN={'b' * 40}" in completed.stderr


def test_v4_fresh_book_shadow_run_helper_is_hash_bound_and_money_disabled() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        'EXPECTED_MODEL_SHA256="6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"',
        "EXPECTED_MODEL_SIZE_BYTES=230132",
        'EXPECTED_SKLEARN_VERSION="1.9.1"',
        'EXPECTED_XGBOOST_VERSION="3.4.1"',
        'EXPECTED_JOBLIB_VERSION="1.5.3"',
        "I_APPROVE_PHASE14_V4_ZERO_MONEY_PAPER_SHADOW:",
        "local_branch_not_main",
        "local_main_stale_update_before_run",
        "PHASE14_V4_FRESH_BOOK_SHADOW_PREFLIGHT=PASS",
        "explicit_zero_money_paper_shadow_approval_missing_or_mismatched",
        "PRODUCTION_HOST_CONTACTED=false",
        "PRODUCTION_MUTATION_PERFORMED=false",
        "PAPER_ACTIVATION_PERFORMED=false",
        "v4-paper-venv-$head",
        "phase14-v4-paper-runtime-requirements.txt",
        "paper_runtime_sklearn_pin_missing",
        "paper_runtime_xgboost_pin_missing",
        "paper_runtime_joblib_pin_missing",
        "paper_runtime_venv_create_failed",
        "paper_runtime_pip_check_failed",
        "existing_paper_runtime_invalid",
        "existing_paper_runtime_not_ready",
        "existing_paper_runtime_head_mismatch",
        "existing_paper_runtime_sklearn_mismatch",
        "existing_paper_runtime_xgboost_mismatch",
        "existing_paper_runtime_joblib_mismatch",
        "--constraint",
        "PYTHONNOUSERSITE=1",
        'version("scikit-learn")',
        "paper runtime metadata version mismatch",
        "paper runtime module version mismatch",
        '.ready',
        '.release-head',
        '.sklearn-version',
        '.xgboost-version',
        '.joblib-version',
        "SCIKIT_LEARN_VERSION",
        "XGBOOST_VERSION",
        "JOBLIB_VERSION",
        "RUN_SECONDS >= 300 && RUN_SECONDS <= 86400",
        "git archive --format=tar.gz",
        '"database_read_only":true',
        "MODE=research",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "fast_live_source_active",
        "fast_live_source_enabled",
        "v3_fresh_book_shadow_already_running",
        "v4_fresh_book_shadow_already_running",
        "frozen_v4_model_artifact_not_found",
        "sha256sum",
        "frozen-v4-model.joblib",
        "load_frozen_v4_bundle",
        'V4_SOURCE_TIME_FEATURE_VERSION == "v4-source-time-features-v2"',
        "V4_CORE_SOURCE_REQUIRED_FLAGS",
        '"core_source_policy":"require_market_start_and_current_all_venues"',
        "SOURCE_FEATURE_VERSION=v4-source-time-features-v2",
        "CORE_SOURCE_POLICY=require_market_start_and_current_all_venues",
        "--quote-fresh-seconds 0.25",
        "--max-decision-lag-seconds 2.0",
        'PHASE14_V4_FRESH_BOOK_SHADOW_RUN_SECONDS:-86400',
        "RUN_SECONDS >= 300 && RUN_SECONDS <= 86400",
        "run_seconds >= 300 && run_seconds <= 86400",
        "runtime_max_seconds=$((run_seconds + 60))",
        "RuntimeMaxSec=${runtime_max_seconds}s",
        "RUNTIME_MAX_SECONDS",
        "--collect",
        "--uid=bp",
        "--gid=bp",
        '"event":"v4_fresh_book_shadow_started"',
        '"database_read_only":true',
        '"order_submission_enabled":false',
        '"holdout_labels_read":false',
        '"model_refit_performed":false',
        '"threshold_tuning_performed":false',
        "PHASE14_V4_FRESH_BOOK_SHADOW_RUN=PASS",
        "DATABASE_WRITES_PERFORMED=false",
        "HOLDOUT_LABELS_READ=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "WALLET_MATERIAL_LOADED=false",
    ):
        assert marker in text

    assert text.index("PHASE14_V4_FRESH_BOOK_SHADOW_PREFLIGHT=PASS") < text.index(
        "gcloud auth list"
    )
    assert "/opt/bp/.venv/bin/python" not in text
    assert "git switch main" not in text
    assert "git pull --ff-only origin main" not in text
    assert 'mv "$venv_tmp" "$venv"' not in text
    assert '"$venv/bin/python" -m pip install' in text
    assert '--constraint "$runtime_requirements" "$release"' in text
    assert text.index('paper runtime metadata version mismatch') < text.index(
        'load_frozen_v4_bundle'
    )

    for forbidden in (
        "systemctl enable",
        "systemctl restart",
        "post_order(",
        "create_market_order",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
    ):
        assert forbidden not in text
