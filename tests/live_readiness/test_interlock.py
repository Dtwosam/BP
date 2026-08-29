import json
from datetime import UTC, datetime, timedelta

import pytest

from bp_engine.live_readiness.interlock import (
    ActivationManifestError,
    kill_switch_engaged,
    load_activation_manifest,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)
SHA = "a" * 64


def _write_manifest(path, **overrides: object) -> None:
    payload: dict[str, object] = {
        "authorized": True,
        "git_sha": SHA,
        "authorization_id": "operator-auth-1",
        "issued_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=30)).isoformat(),
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_load_activation_manifest_requires_exact_head_and_authorization(tmp_path) -> None:
    path = tmp_path / "activation.json"
    _write_manifest(path)

    manifest = load_activation_manifest(str(path), expected_git_sha=SHA, observed_at=NOW)

    assert manifest.authorized is True
    assert manifest.git_sha == SHA
    assert manifest.authorization_id == "operator-auth-1"


@pytest.mark.parametrize(
    "overrides",
    [
        {"authorized": False},
        {"git_sha": "b" * 64},
        {"expires_at": (NOW - timedelta(seconds=1)).isoformat()},
        {"issued_at": (NOW + timedelta(seconds=1)).isoformat()},
        {"authorization_id": ""},
    ],
)
def test_activation_manifest_fails_closed_for_invalid_authorization(tmp_path, overrides) -> None:
    path = tmp_path / "activation.json"
    _write_manifest(path, **overrides)

    with pytest.raises(ActivationManifestError):
        load_activation_manifest(str(path), expected_git_sha=SHA, observed_at=NOW)


def test_activation_manifest_fails_closed_when_missing_or_malformed(tmp_path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ActivationManifestError):
        load_activation_manifest(str(missing), expected_git_sha=SHA, observed_at=NOW)

    malformed = tmp_path / "activation.json"
    malformed.write_text("{", encoding="utf-8")
    with pytest.raises(ActivationManifestError):
        load_activation_manifest(str(malformed), expected_git_sha=SHA, observed_at=NOW)


def test_kill_switch_is_engaged_by_presence_and_unreadable_state(tmp_path) -> None:
    switch = tmp_path / "KILL"
    assert kill_switch_engaged(str(switch)) is False

    switch.write_text("STOP\n", encoding="utf-8")
    assert kill_switch_engaged(str(switch)) is True

    switch.unlink()
    switch.mkdir()
    assert kill_switch_engaged(str(switch)) is True
