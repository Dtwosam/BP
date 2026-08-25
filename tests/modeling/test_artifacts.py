from __future__ import annotations

import hashlib

from bp_engine.modeling.artifacts import write_model_artifact


def test_model_artifact_is_written_with_verified_sha256(tmp_path) -> None:
    artifact = write_model_artifact(
        {"coef": [1.0, -1.0]},
        output_dir=tmp_path,
        name="logistic-fixture",
        family="logistic",
    )

    payload = artifact.path.read_bytes()
    assert artifact.path.exists()
    assert artifact.size_bytes == len(payload)
    assert artifact.sha256 == hashlib.sha256(payload).hexdigest()
    assert artifact.family == "logistic"
    assert artifact.file_name == "logistic-fixture.joblib"
    assert not list(tmp_path.glob("*.tmp"))
