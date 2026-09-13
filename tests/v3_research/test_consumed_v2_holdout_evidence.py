from __future__ import annotations

from pathlib import Path

from bp_engine.v3_research.exclusions import load_exclusion_manifest


EVIDENCE_PATH = Path(
    "docs/evidence/phase-14-v3-consumed-v2-final-holdout-exclusions-20260913.json"
)
EXPECTED_MANIFEST_SHA256 = (
    "28f46a8feff52bc02780fd67b1e42bbcd462ff562b33a0b5d8df903b6d54ef54"
)


def test_consumed_v2_final_holdout_evidence_is_frozen_and_loadable() -> None:
    manifest = load_exclusion_manifest(
        EVIDENCE_PATH,
        expected_kind="consumed_v2_final_holdout",
    )

    assert manifest.sha256 == EXPECTED_MANIFEST_SHA256
    assert len(manifest.condition_ids) == 24
    assert len(set(manifest.condition_ids)) == 24
