from __future__ import annotations

import argparse
import json
import os
import stat
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bp_engine.execution.telegram_origin_attestation import (
    OriginAttestationError,
    load_origin_key_file,
)
from bp_engine.execution.telegram_source_truth_authorization import (
    SourceTruthAuthorizationError,
    create_source_truth_authorization,
)

MAX_JSON_BYTES = 2 * 1024 * 1024


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _load_json(
    path: Path,
    *,
    label: str,
    modes: tuple[int, ...],
) -> dict[str, Any]:
    try:
        info = path.lstat()
    except OSError as exc:
        raise SourceTruthAuthorizationError(
            f"{label} is not readable"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise SourceTruthAuthorizationError(
            f"{label} must be a regular non-symlink file"
        )
    if stat.S_IMODE(info.st_mode) not in modes:
        allowed = ", ".join(f"{mode:04o}" for mode in modes)
        raise SourceTruthAuthorizationError(
            f"{label} mode must be one of {allowed}"
        )
    if info.st_size <= 0 or info.st_size > MAX_JSON_BYTES:
        raise SourceTruthAuthorizationError(f"{label} size invalid")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceTruthAuthorizationError(
            f"{label} JSON invalid"
        ) from exc
    if not isinstance(payload, Mapping):
        raise SourceTruthAuthorizationError(
            f"{label} must contain a JSON object"
        )
    return dict(payload)


def _ensure_private_parent(path: Path) -> None:
    try:
        info = path.parent.lstat()
    except OSError as exc:
        raise SourceTruthAuthorizationError(
            "source truth output directory missing"
        ) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SourceTruthAuthorizationError(
            "source truth output directory must be a non-symlink directory"
        )
    if stat.S_IMODE(info.st_mode) != 0o700:
        raise SourceTruthAuthorizationError(
            "source truth output directory mode must be 0700"
        )


def _write_once(path: Path, payload: Mapping[str, Any]) -> None:
    _ensure_private_parent(path)
    encoded = (
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        + "\n"
    )
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise SourceTruthAuthorizationError(
            "source truth output already exists"
        ) from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create a short-lived exact-order BP Telegram source-truth "
            "authorization proof. This command performs no network or execution action."
        )
    )
    parser.add_argument("--project-state", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--approval", type=Path, required=True)
    parser.add_argument("--origin-attestation", type=Path, required=True)
    parser.add_argument("--origin-key-file", type=Path, required=True)
    parser.add_argument("--origin-key-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _require_safe_runtime() -> None:
    for forbidden in (
        "POLYMARKET_PRIVATE_KEY",
        "POLYMARKET_WALLET_ADDRESS",
        "BP_TELEGRAM_BOT_TOKEN",
        "GOOGLE_APPLICATION_CREDENTIALS",
    ):
        if os.environ.get(forbidden):
            raise SystemExit(
                f"{forbidden} must not be present in source-truth attester"
            )


def main() -> int:
    args = _parse_args()
    _require_safe_runtime()
    try:
        state = _load_json(
            args.project_state,
            label="project state",
            modes=(0o600, 0o640, 0o644),
        )
        prepared = _load_json(
            args.prepared,
            label="prepared payload",
            modes=(0o600, 0o640),
        )
        approval = _load_json(
            args.approval,
            label="approval payload",
            modes=(0o600, 0o640),
        )
        origin = _load_json(
            args.origin_attestation,
            label="origin attestation",
            modes=(0o600, 0o640),
        )
        try:
            key = load_origin_key_file(args.origin_key_file)
        except OriginAttestationError as exc:
            raise SourceTruthAuthorizationError(str(exc)) from exc
        attestation = create_source_truth_authorization(
            state,
            prepared=prepared,
            approval=approval,
            origin_attestation=origin,
            key=key,
            key_id=args.origin_key_id,
            attested_at=_utc_now(),
        )
        _write_once(args.output, attestation)
    except SourceTruthAuthorizationError as exc:
        print(
            json.dumps(
                {
                    "status": "failed_closed",
                    "error": str(exc),
                    "mutation_performed": False,
                    "network_action_performed": False,
                    "executor_invoked": False,
                    "real_order_submitted": False,
                },
                sort_keys=True,
            )
        )
        return 1

    print(
        json.dumps(
            {
                "status": "source_truth_authorization_written",
                "authorized": attestation["authorized"],
                "blockers": attestation["blockers"],
                "project_state_sha256": attestation["project_state_sha256"],
                "authorization_snapshot_sha256": attestation[
                    "authorization_snapshot_sha256"
                ],
                "intent_id": attestation["intent_id"],
                "request_sha256": attestation["request_sha256"],
                "attested_at": attestation["attested_at"],
                "expires_at": attestation["expires_at"],
                "output_path": str(args.output),
                "mutation_performed": False,
                "network_action_performed": False,
                "executor_invoked": False,
                "real_order_submitted": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
