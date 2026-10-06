from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "scripts" / "deploy" / "phase14_v4_gate_b_final_holdout_cloudshell.sh"


def test_v4_final_holdout_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_final_holdout_helper_is_exactly_hash_bound_and_one_shot() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        'EXPECTED_PLAN_FILE_SHA256="564e0c299b360062c5d1e37ceb10050e5b600f4fc29f35451aef8c08a5884dad"',
        'EXPECTED_PLAN_SHA256="9c017b1d968925a8cddab18324628227ed8b8b381e43c653f72c0f26366ee557"',
        'EXPECTED_SELECTION_FILE_SHA256="cd7e42eb07fe675ebf4687bfc8162908428e4583e763efd9762b6ec7ec120a53"',
        'EXPECTED_SELECTION_SHA256="895cb70ae0cdbc22f4e3585c77db3ad20f8186d1ee1992a58025d89bb1e2bb1a"',
        'EXPECTED_MODEL_SHA256="6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"',
        "EXPECTED_MODEL_SIZE_BYTES=230132",
        "EXPECTED_HOLDOUT_MARKETS=288",
        "I_APPROVE_PHASE14_V4_GATE_B_FINAL_HOLDOUT:",
        "PHASE14_V4_GATE_B_FINAL_HOLDOUT_PREFLIGHT=PASS",
        "PRODUCTION_HOST_CONTACTED=false",
        "DATABASE_ACCESSED=false",
        "PRODUCTION_MUTATION_PERFORMED=false",
        "v4-gate-b-final-holdout-v2-attempt.json",
        "HOLDOUT_ACCESS_COMMITTED_BEFORE_LABEL_READ",
        "os.O_EXCL",
        "os.fsync",
        "MODEL_FILE_NAME",
        "selection model filename invalid",
        "evaluate-holdout",
        "--plan",
        "--selection",
        "--model",
        "--output",
        "holdout.json",
        "MODE=research",
        "LIVE_TRADING_ENABLED=false",
        "MAX_TRADE_SIZE_USD=0",
        "MAX_DAILY_LOSS_USD=0",
        "model_refit_performed",
        "threshold_tuning_performed",
        "policy_reselection_performed",
        "paper_activation_authorized",
        "paper_activation_performed",
        "DATABASE_WRITES_PERFORMED=false",
        "PAPER_ACTIVATION_PERFORMED=false",
        "REAL_MONEY_USD=0",
    ):
        assert marker in text

    assert text.index("PHASE14_V4_GATE_B_FINAL_HOLDOUT_PREFLIGHT=PASS") < text.index(
        "gcloud auth list"
    )
    assert text.index("HOLDOUT_ACCESS_COMMITTED_BEFORE_LABEL_READ") < text.index(
        "evaluate-holdout"
    )


def test_v4_final_holdout_preflight_is_local_only_and_prints_exact_token(
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
if [[ "$1" == "rev-parse" && ( "$2" == "HEAD" || "$2" == "origin/main" ) ]]; then
  printf '%s\\n' "$FAKE_MAIN_SHA"
  exit 0
fi
case "$1" in
  fetch|switch|pull) exit 0 ;;
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

    main_sha = "a" * 40
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_REPO_ROOT"] = str(tmp_path)
    env["FAKE_MAIN_SHA"] = main_sha
    env["FAKE_GCLOUD_SENTINEL"] = str(gcloud_sentinel)
    env["PHASE14_V4_GATE_B_FINAL_HOLDOUT_PREFLIGHT_ONLY"] = "true"

    completed = subprocess.run(
        ["bash", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    assert not gcloud_sentinel.exists()
    assert "PHASE14_V4_GATE_B_FINAL_HOLDOUT_PREFLIGHT=PASS" in completed.stdout
    assert f"CANDIDATE_MAIN={main_sha}" in completed.stdout
    assert "HOLDOUT_TOUCHED=false" in completed.stdout
    assert "PRODUCTION_HOST_CONTACTED=false" in completed.stdout
    assert "DATABASE_ACCESSED=false" in completed.stdout
    assert "PRODUCTION_MUTATION_PERFORMED=false" in completed.stdout
    assert (
        "EXPECTED_APPROVAL="
        f"I_APPROVE_PHASE14_V4_GATE_B_FINAL_HOLDOUT:{main_sha}:"
        "564e0c299b360062c5d1e37ceb10050e5b600f4fc29f35451aef8c08a5884dad:"
        "cd7e42eb07fe675ebf4687bfc8162908428e4583e763efd9762b6ec7ec120a53:"
        "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"
        in completed.stdout
    )


def test_v4_final_holdout_helper_does_not_activate_or_submit() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for forbidden in (
        "systemctl start",
        "systemctl restart",
        "systemctl enable",
        "post_order(",
        "create_market_order",
        "POLYMARKET_PRIVATE_KEY=",
        "POLYMARKET_WALLET_ADDRESS=",
        "LIVE_TRADING_ENABLED=true",
        "MAX_TRADE_SIZE_USD=5",
        "MAX_DAILY_LOSS_USD=5",
    ):
        assert forbidden not in text
