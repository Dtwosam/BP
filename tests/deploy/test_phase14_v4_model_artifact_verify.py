from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
VERIFY = (
    ROOT
    / "scripts"
    / "deploy"
    / "phase14_v4_model_artifact_verify_cloudshell.sh"
)


def test_v4_model_artifact_verifier_is_shell_valid() -> None:
    completed = subprocess.run(
        ["bash", "-n", str(VERIFY)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_v4_model_artifact_verifier_is_read_only_and_hash_bound() -> None:
    text = VERIFY.read_text(encoding="utf-8")

    for marker in (
        'EXPECTED_SHA256="6ae26dcbd189462cc4e594dede8cd3398c7a92960d275bdf43bbada5df2e8ddf"',
        "EXPECTED_SIZE_BYTES=230132",
        "/var/lib/bp/evidence",
        "/var/lib/bp/runtime",
        "/opt/bp",
        "sha256sum",
        "frozen_v4_model_artifact_not_found",
        "frozen_v4_model_artifact_not_unique",
        "MODEL_DESERIALIZED=false",
        "HOLDOUT_LABELS_READ=false",
        "DATABASE_ACCESS=none",
        "DATABASE_WRITES_PERFORMED=false",
        "SERVICE_MUTATION_PERFORMED=false",
        "ORDER_SUBMISSION_PERFORMED=false",
        "PHASE14_V4_MODEL_ARTIFACT_VERIFY=PASS",
    ):
        assert marker in text

    for forbidden in (
        "joblib.load",
        "systemctl start",
        "systemctl restart",
        "systemctl stop",
        "psql",
        "INSERT INTO",
        "UPDATE ",
        "DELETE FROM",
        "post_order(",
        "create_market_order",
    ):
        assert forbidden not in text
