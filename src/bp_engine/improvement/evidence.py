from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from bp_engine.improvement.models import (
    EvidenceItem,
    EvidenceRole,
    ImprovementExperimentSpec,
)

_CONFIRMATION_ROLES = frozenset(
    {
        EvidenceRole.FRESH_HOLDOUT,
        EvidenceRole.PROSPECTIVE_PAPER,
    }
)
_SELECTION_ROLES = frozenset(
    {
        EvidenceRole.DEVELOPMENT_TRAIN,
        EvidenceRole.DEVELOPMENT_VALIDATION,
    }
)


class EvidenceIntegrityError(ValueError):
    """Raised when evidence violates the frozen experiment boundary."""


def _policy_roles(
    policy: Mapping[str, Any],
    *,
    policy_name: str,
) -> frozenset[EvidenceRole]:
    raw_roles = policy.get("allowed_roles", ())
    if not isinstance(raw_roles, (list, tuple, set, frozenset)):
        raise EvidenceIntegrityError(f"{policy_name} allowed_roles must be a collection")

    roles: set[EvidenceRole] = set()
    for raw_role in raw_roles:
        try:
            roles.add(EvidenceRole(str(raw_role)))
        except ValueError as exc:
            raise EvidenceIntegrityError(
                f"{policy_name} contains unknown evidence role {raw_role!r}"
            ) from exc
    return frozenset(roles)


def validate_evidence_manifest(
    *,
    experiment: ImprovementExperimentSpec,
    evidence: Iterable[EvidenceItem],
    prior_confirmation_identifiers: set[str] | frozenset[str],
) -> None:
    """Fail closed if evidence crosses selection, freshness, or temporal boundaries."""

    selection_roles = _policy_roles(
        experiment.selection_policy,
        policy_name="selection policy",
    )
    confirmation_roles = _policy_roles(
        experiment.confirmation_policy,
        policy_name="confirmation policy",
    )

    forbidden_selection_roles = selection_roles & _CONFIRMATION_ROLES
    if forbidden_selection_roles:
        names = ", ".join(sorted(role.value for role in forbidden_selection_roles))
        raise EvidenceIntegrityError(
            f"selection policy cannot include independent confirmation roles: {names}"
        )

    unknown_selection_roles = selection_roles - _SELECTION_ROLES
    if unknown_selection_roles:
        names = ", ".join(sorted(role.value for role in unknown_selection_roles))
        raise EvidenceIntegrityError(f"selection policy contains non-development roles: {names}")

    forbidden_confirmation_roles = confirmation_roles - _CONFIRMATION_ROLES
    if forbidden_confirmation_roles:
        names = ", ".join(sorted(role.value for role in forbidden_confirmation_roles))
        raise EvidenceIntegrityError(
            f"confirmation policy contains non-confirmation roles: {names}"
        )

    legacy_identifiers = set(experiment.legacy_confirmation_identifiers)
    consumed_identifiers = set(prior_confirmation_identifiers)

    for item in evidence:
        if item.observed_at.tzinfo is None or item.observed_at.utcoffset() is None:
            raise EvidenceIntegrityError(
                f"evidence {item.identifier} observed_at must be timezone-aware"
            )

        if item.role in _SELECTION_ROLES and item.role not in selection_roles:
            raise EvidenceIntegrityError(
                f"evidence role {item.role.value} is not allowed by selection policy"
            )

        if item.role not in _CONFIRMATION_ROLES:
            continue

        if item.role not in confirmation_roles:
            raise EvidenceIntegrityError(
                f"evidence role {item.role.value} is not allowed by confirmation policy"
            )

        if item.identifier in consumed_identifiers:
            raise EvidenceIntegrityError(
                f"confirmation evidence {item.identifier} was already consumed"
            )

        if item.role is EvidenceRole.FRESH_HOLDOUT and (
            item.identifier in legacy_identifiers or item.condition_id in legacy_identifiers
        ):
            raise EvidenceIntegrityError(
                f"evidence {item.identifier} is a known legacy final holdout"
            )

        if item.role is EvidenceRole.PROSPECTIVE_PAPER:
            if not item.prediction_id:
                raise EvidenceIntegrityError(
                    f"prospective paper evidence {item.identifier} requires prediction_id"
                )
            if item.observed_at <= experiment.created_at:
                raise EvidenceIntegrityError(
                    f"prospective paper evidence {item.identifier} must post-date challenger freeze"
                )
