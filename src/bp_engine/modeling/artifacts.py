from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

import joblib


@dataclass(frozen=True)
class ModelArtifact:
    family: str
    file_name: str
    path: Path
    size_bytes: int
    sha256: str
    library_version: str


def _library_version(family: str) -> str:
    if family == "xgboost":
        return version("xgboost")
    if family == "logistic":
        return version("scikit-learn")
    return version("joblib")


def write_model_artifact(
    model: object,
    *,
    output_dir: Path,
    name: str,
    family: str,
) -> ModelArtifact:
    if not name or "/" in name or "\\" in name:
        raise ValueError("artifact name must be a simple file stem")
    output_dir.mkdir(parents=True, exist_ok=True)
    final_path = output_dir / f"{name}.joblib"
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{name}.",
        suffix=".tmp",
        dir=output_dir,
    )
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        joblib.dump(model, temp_path)
        os.replace(temp_path, final_path)
    finally:
        if temp_path.exists():
            temp_path.unlink()
    payload = final_path.read_bytes()
    return ModelArtifact(
        family=family,
        file_name=final_path.name,
        path=final_path,
        size_bytes=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        library_version=_library_version(family),
    )
