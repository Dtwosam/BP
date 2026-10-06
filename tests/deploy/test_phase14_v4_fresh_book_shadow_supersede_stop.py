from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_fresh_book_shadow_supersede_stop_cloudshell.sh"
)
RUN_ID = "v4-fresh-book-shadow-20261006T140323Z-f5c76576619c"
MODEL_SHA = "6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"


def test_v4_supersede_stop_helper_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_supersede_stop_preflight_is_local_only(tmp_path: Path) -> None:
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
if [[ "$1" == "rev-parse" && ( "$2" == "HEAD" || "$2" == "origin/main" ) ]]; then
  printf '%s\\n' "$FAKE_MAIN_SHA"
  exit 0
fi
exit 97
""",
        encoding="utf-8",
    )
    fake_git.chmod(0o755)

    runner = tmp_path / "scripts" / "run_v4_fresh_book_shadow.py"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text(
        "\n".join(
            (
                "probe_core_source_time_v4_readiness",
                '"source_retry_probe": "core_six_anchor_only"',
            )
        ),
        encoding="utf-8",
    )
    source_features = (
        tmp_path / "src" / "bp_engine" / "v4_paper" / "source_time_features.py"
    )
    source_features.parent.mkdir(parents=True, exist_ok=True)
    source_features.write_text(
        "raw_market_events.c.received_at <= requested\n",
        encoding="utf-8",
    )

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

    main_sha = "c" * 40
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["FAKE_REPO_ROOT"] = str(tmp_path)
    env["FAKE_MAIN_SHA"] = main_sha
    env["FAKE_GCLOUD_SENTINEL"] = str(gcloud_sentinel)
    env["PHASE14_V4_SUPERSEDE_STOP_PREFLIGHT_ONLY"] = "true"

    completed = subprocess.run(
        ["bash", str(HELPER)],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )

    assert completed.returncode == 0, completed.stderr
    assert not gcloud_sentinel.exists()
    assert "PHASE14_V4_FRESH_BOOK_SHADOW_SUPERSEDE_STOP_PREFLIGHT=PASS" in completed.stdout
    assert f"CANDIDATE_MAIN={main_sha}" in completed.stdout
    assert f"EXPECTED_OLD_RUN_ID={RUN_ID}" in completed.stdout
    assert (
        "EXPECTED_APPROVAL="
        f"I_APPROVE_PHASE14_V4_ZERO_MONEY_PAPER_SHADOW_SUPERSEDE_STOP:"
        f"{RUN_ID}:{main_sha}:{MODEL_SHA}"
        in completed.stdout
    )
    assert "PRODUCTION_HOST_CONTACTED=false" in completed.stdout
    assert "PRODUCTION_MUTATION_PERFORMED=false" in completed.stdout
    assert "SERVICE_STOP_PERFORMED=false" in completed.stdout
    assert "EVIDENCE_DELETE_PERFORMED=false" in completed.stdout
    assert "REAL_MONEY_USD=0" in completed.stdout


def test_v4_supersede_stop_is_exact_scoped_and_preserves_evidence() -> None:
    text = HELPER.read_text(encoding="utf-8")

    for marker in (
        f'EXPECTED_RUN_ID="{RUN_ID}"',
        'EXPECTED_RUN_MAIN="f5c76576619c35e65fc8a317d47c8a31dd263950"',
        f'EXPECTED_MODEL_SHA256="{MODEL_SHA}"',
        "I_APPROVE_PHASE14_V4_ZERO_MONEY_PAPER_SHADOW_SUPERSEDE_STOP:",
        'systemctl stop "$expected_unit"',
        "old_shadow_not_active",
        "old_evidence_missing_or_invalid",
        "old_evidence_not_preserved",
        "source_ineligible <= 0",
        "EVIDENCE_DELETE_PERFORMED=false",
        "DATABASE_WRITES_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "WALLET_MATERIAL_LOADED=false",
        "SERVICE_STOP_PERFORMED=true",
        "PRODUCTION_MUTATION_PERFORMED=true",
        "FAST_LIVE_SOURCE_ACTIVE=false",
        "LIVE_TRADING_ENABLED=false",
        "REAL_MONEY_USD=0",
        "probe_core_source_time_v4_readiness",
        "core_six_anchor_only",
        "raw_market_events.c.received_at <= requested",
    ):
        assert marker in text

    assert "systemctl restart" not in text
    assert "systemctl enable" not in text
    assert "systemctl disable" not in text
    assert "rm -f /var/lib/bp/evidence" not in text
    assert "create_market_order" not in text
    assert "post_order(" not in text
    assert "POLYMARKET_PRIVATE_KEY=" not in text
    assert "POLYMARKET_WALLET_ADDRESS=" not in text
