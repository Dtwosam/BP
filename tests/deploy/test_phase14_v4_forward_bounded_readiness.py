from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_forward_bounded_readiness_cloudshell.sh"
)


def test_preflight_bash_syntax_and_main_binding() -> None:
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    source = SCRIPT.read_text(encoding="utf-8")
    for item in (
        "PHASE14_V4_FORWARD_BOUNDED_DEPLOYED_HEAD",
        "local_main_stale",
        "local_worktree_dirty",
        "V4_FORWARD_MARKETS_PER_CYCLE = 1",
        "V4_FORWARD_STAGE=",
        "BOUNDED_CANDIDATE_MAIN=",
        "DEPLOYED_CHECKOUT_HEAD=",
        "git -c safe.directory=/opt/bp -C /opt/bp rev-parse HEAD",
    ):
        assert item in source


def test_runtime_safety_and_read_only_db_snapshot() -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    for item in (
        "/var/lib/bp/runtime/v4-forward-current",
        "readlink -f",
        "V4_FORWARD_RUNTIME_CODE_SHA256=",
        "bp-v4-forward-coverage.service",
        "bp-v4-forward-coverage.timer",
        "bp-postgres.service",
        "bp-recorder.service",
        "bp-v3-frozen-predictor.service",
        "bp-v3-paper-execution.service",
        "bp-prospective-runtime-safety.env",
        "automatic_promotion",
        "TradingMode.RESEARCH",
        "default_transaction_read_only=on",
        "statement_timeout=2000",
        "POSTGRES_SHARED_BUFFERS=",
        '"128MB"',
        "PHASE14_V4_FORWARD_BOUNDED_READINESS=PASS",
        "PRODUCTION_MUTATION=false",
        "DEPLOYMENT_EXECUTED=false",
        "ROLLOUT_AUTHORIZED=false",
    ):
        assert item in source


def test_readiness_never_changes_services_database_or_checkout() -> None:
    source = SCRIPT.read_text(encoding="utf-8").lower()
    for item in (
        "systemctl start ",
        "systemctl stop ",
        "systemctl restart ",
        "systemctl reset-failed ",
        "systemctl enable ",
        "systemctl disable ",
        "daemon-reload",
        "docker compose up",
        "git checkout",
        "git reset",
        "git switch",
        "git pull",
        "git fetch",
        "create index",
        "alter table",
        "update market_features",
        "delete from market_features",
        "truncate market_features",
    ):
        assert item not in source
