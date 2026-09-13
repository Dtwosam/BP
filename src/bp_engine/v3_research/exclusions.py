from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from bp_engine.features.hashing import canonical_hash

EXCLUSION_MANIFEST_VERSION = "v3-gate-b-exclusion-v1"
ExclusionKind = Literal["diagnosis", "consumed_v2_final_holdout"]
_ALLOWED_KINDS = frozenset({"diagnosis", "consumed_v2_final_holdout"})


class ExclusionManifestError(ValueError):
    """Raised when an exclusion manifest violates the preregistered contract."""


@dataclass(frozen=True)
class ExclusionManifest:
    version: str
    kind: ExclusionKind
    condition_ids: tuple[str, ...]
    sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "kind": self.kind,
            "condition_ids": list(self.condition_ids),
            "sha256": self.sha256,
        }


def _validate_kind(kind: str) -> ExclusionKind:
    if kind not in _ALLOWED_KINDS:
        raise ExclusionManifestError(f"unsupported exclusion manifest kind: {kind}")
    if kind == "diagnosis":
        return "diagnosis"
    return "consumed_v2_final_holdout"


def _canonical_condition_ids(condition_ids: Iterable[str]) -> tuple[str, ...]:
    values = tuple(condition_ids)
    if any(not isinstance(condition_id, str) or not condition_id for condition_id in values):
        raise ExclusionManifestError("condition_ids must contain non-empty strings")
    return tuple(sorted(set(values)))


def _hash_payload(
    *,
    version: str,
    kind: ExclusionKind,
    condition_ids: tuple[str, ...],
) -> dict[str, object]:
    return {
        "version": version,
        "kind": kind,
        "condition_ids": list(condition_ids),
    }


def build_exclusion_manifest(
    *,
    kind: str,
    condition_ids: Iterable[str],
) -> ExclusionManifest:
    validated_kind = _validate_kind(kind)
    canonical_ids = _canonical_condition_ids(condition_ids)
    payload = _hash_payload(
        version=EXCLUSION_MANIFEST_VERSION,
        kind=validated_kind,
        condition_ids=canonical_ids,
    )
    return ExclusionManifest(
        version=EXCLUSION_MANIFEST_VERSION,
        kind=validated_kind,
        condition_ids=canonical_ids,
        sha256=canonical_hash(payload),
    )


def load_exclusion_manifest(
    path: str | Path,
    *,
    expected_kind: str | None = None,
) -> ExclusionManifest:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExclusionManifestError("unable to load exclusion manifest") from exc

    if not isinstance(payload, dict):
        raise ExclusionManifestError("exclusion manifest must be a JSON object")
    if payload.get("version") != EXCLUSION_MANIFEST_VERSION:
        raise ExclusionManifestError("unexpected exclusion manifest version")

    kind_value = payload.get("kind")
    if not isinstance(kind_value, str):
        raise ExclusionManifestError("exclusion manifest kind must be a string")
    kind = _validate_kind(kind_value)

    raw_condition_ids = payload.get("condition_ids")
    if not isinstance(raw_condition_ids, list) or any(
        not isinstance(condition_id, str) or not condition_id
        for condition_id in raw_condition_ids
    ):
        raise ExclusionManifestError("condition_ids must be a list of non-empty strings")
    condition_ids = tuple(raw_condition_ids)
    canonical_ids = _canonical_condition_ids(condition_ids)
    if condition_ids != canonical_ids:
        raise ExclusionManifestError("condition_ids must be sorted and unique")

    raw_sha256 = payload.get("sha256")
    if not isinstance(raw_sha256, str):
        raise ExclusionManifestError("exclusion manifest hash must be a string")
    expected_sha256 = canonical_hash(
        _hash_payload(
            version=EXCLUSION_MANIFEST_VERSION,
            kind=kind,
            condition_ids=condition_ids,
        )
    )
    if raw_sha256 != expected_sha256:
        raise ExclusionManifestError("exclusion manifest hash mismatch")

    if expected_kind is not None:
        validated_expected_kind = _validate_kind(expected_kind)
        if kind != validated_expected_kind:
            raise ExclusionManifestError(
                f"expected kind {validated_expected_kind}, got {kind}"
            )

    return ExclusionManifest(
        version=EXCLUSION_MANIFEST_VERSION,
        kind=kind,
        condition_ids=condition_ids,
        sha256=raw_sha256,
    )
