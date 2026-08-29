from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from .models import ActivationManifest


class ActivationManifestError(RuntimeError):
    """Raised when live activation evidence is missing or invalid."""


def _aware_utc(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


def load_activation_manifest(
    path: str,
    *,
    expected_git_sha: str,
    observed_at: datetime,
) -> ActivationManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("activation manifest must be a JSON object")
        if payload.get("authorized") is not True:
            raise ValueError("activation manifest is not authorized")

        manifest = ActivationManifest(
            authorized=True,
            git_sha=payload["git_sha"],
            authorization_id=payload["authorization_id"],
            issued_at=datetime.fromisoformat(payload["issued_at"]),
            expires_at=datetime.fromisoformat(payload["expires_at"]),
        )
        observed = _aware_utc(observed_at, "observed_at")
        if manifest.git_sha != expected_git_sha.lower():
            raise ValueError("activation manifest does not match the deployed git SHA")
        if manifest.issued_at > observed:
            raise ValueError("activation manifest was issued in the future")
        if observed >= manifest.expires_at:
            raise ValueError("activation manifest is expired")
        return manifest
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise ActivationManifestError("activation manifest is missing or invalid") from exc


def kill_switch_engaged(path: str) -> bool:
    try:
        return Path(path).exists()
    except OSError:
        return True
