from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = (
    ROOT / "scripts" / "deploy"
    / "phase14_v4_cache_batch100_readiness_cloudshell.sh"
)


def test_readiness_shell_syntax_is_valid() -> None:
    subprocess.run(["bash", "-n", str(HELPER)], check=True)


def test_readiness_checks_exact_runtime_without_mutation() -> None:
    source = HELPER.read_text(encoding="utf-8")
    for marker in (
        '"$(git branch --show-current)" == "main"',
        "local_main_not_current",
        "DEPLOYED_CHECKOUT_HEAD=",
        "git -C /opt/bp rev-parse HEAD",
        "bp-recorder.service",
        "bp-postgres.service",
        "bp-v3-frozen-predictor.service",
        "bp-v3-paper-execution.service",
        "bp-v4-forward-coverage.timer",
        "HOST_MEMORY_KIB",
        "HOST_IO_PRESSURE",
        "memory_limit_bytes",
        "memory.current",
        "MemAvailable",
        "MemTotal",
        "recorder_batch_size == 100",
        "recorder_priority_batch_size == 20",
        "recorder_priority_queue_maxsize == 5_000",
        "recorder_writer_workers == 4",
        "max_total_exposure_usd == 0",
        "default_transaction_read_only=on",
        "SHOW shared_buffers",
        'shared == "128MB"',
        "PHASE14_V4_CACHE_READINESS_STATUS=PASS",
        "PRODUCTION_MUTATION=false",
        "EXPERIMENT_EXECUTED=false",
    ):
        assert marker in source


def test_readiness_never_runs_or_authorizes_experiment() -> None:
    source = HELPER.read_text(encoding="utf-8")
    forbidden = (
        "run_v4_postgres_cache2g_batch100_ab.py",
        "docker compose up",
        "docker-compose up",
        "systemctl stop",
        "systemctl start",
        "systemctl restart",
        "REINDEX INDEX",
        "ALTER SYSTEM",
        "CREATE INDEX",
        "DROP INDEX",
        "VACUUM ",
        "pg_terminate_backend",
        "git checkout",
        "git switch",
        "git pull",
        "gcloud config set",
        "--execute",
        "I_APPROVE_PHASE14",
    )
    for marker in forbidden:
        assert marker not in source


def test_readiness_requires_clean_up_to_date_main() -> None:
    source = HELPER.read_text(encoding="utf-8")
    assert '[[ -z "$(git status --porcelain --untracked-files=all)" ]]' in source
    assert '[[ "$LOCAL_HEAD" == "$REMOTE_MAIN" ]]' in source
    assert 'gcloud compute ssh "$VM"' in source
    assert "--command=\"$REMOTE\"" in source
    assert "sudo -n -u bp env" in source
    assert "s.database_url" in source
    assert "print(s.database_url)" not in source
    assert "REDACTED_RECORDER_CONFIG" in source
