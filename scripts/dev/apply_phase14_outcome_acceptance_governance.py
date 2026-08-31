from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MASTER = ROOT / "docs" / "MASTER-SOURCE-OF-TRUTH.md"
DECISIONS = ROOT / "docs" / "DECISION-LOG.md"
CHANGELOG = ROOT / "docs" / "CHANGELOG.md"
STATE = ROOT / "PROJECT_STATE.json"


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise RuntimeError(f"expected exactly one governance anchor in {path}: {old[:80]!r}")
    path.write_text(text.replace(old, new, 1))


def main() -> None:
    master_anchor = (
        "The runtime is permitted only in `RESEARCH` with `LIVE_TRADING_ENABLED=false`, "
        "`MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`. It exposes no wallet, signing, "
        "real-order, promotion, or live-enable path. Its exact-head host acceptance is deliberately "
        "non-deploying: it runs the candidate from a detached worktree, requires the existing paper "
        "worker and live predictor to be active, may append only canonical official-outcome/evaluation "
        "evidence and derivative paper settlements, verifies `/opt/bp` is unchanged, and performs no "
        "package installation, migration, service stop/restart, or daemon installation."
    )
    master_addition = master_anchor + (
        "\n\nHost acceptance must also prove that this follow-up path was genuinely exercised rather than "
        "passing on an idle no-op. The bounded acceptance cycle therefore requires at least one ended "
        "unevaluated candidate, at least one resolved official market, complete pending/resolved candidate "
        "accounting, one snapshot-store result per resolved candidate, canonical label evidence covering "
        "the resolved candidates, and a newly appended immutable evaluation for every resolved candidate. "
        "These are acceptance-path exercise requirements only; they are not a minimum prospective paper "
        "sample, profitability threshold, calibration threshold, or live-trading promotion criterion."
    )
    replace_once(MASTER, master_anchor, master_addition)

    decision_anchor = (
        "The outcome-sync CLI exposes only bounded one-cycle or repeated research execution, reuses the "
        "existing research/live-disabled/zero-money safety guard, and contains no wallet, signing, "
        "order-submission, promotion, or live-enable path. Host acceptance must run an exact candidate "
        "from a detached worktree, require the existing paper and live-predictor services to be active, "
        "keep `/opt/bp` unchanged, and may append only official outcome/label/evaluation evidence plus "
        "paper settlements derived through the already-existing paper worker. It must not install packages, "
        "migrate production, stop/restart services, or install the new daemon. Permanent rollout remains a "
        "separate explicit step after host acceptance. The Master live gate remains unchanged and Phase 15 "
        "remains blocked."
    )
    decision_addition = decision_anchor + (
        "\n\nA host-acceptance PASS is invalid if the outcome-sync cycle is a no-op. Acceptance must observe at "
        "least one ended unevaluated candidate and at least one resolved candidate, reconcile every candidate "
        "as pending or resolved, reconcile every resolved candidate to a snapshot-store result, confirm "
        "canonical label evidence for the resolved set, and append a new immutable evaluation for every "
        "resolved candidate. This deliberately proves the new production evidence path executes; it does not "
        "define a sufficiently large prospective sample and must never be reused as a profitability, calibration, "
        "or live-readiness threshold."
    )
    replace_once(DECISIONS, decision_anchor, decision_addition)

    changelog_anchor = (
        "TDD preserved explicit RED/GREEN evidence. The initial clean service RED at `233b35a1` failed only "
        "for missing `bp_engine.prospective_outcomes`. A later GREEN cycle exposed the prefixed-snapshot-hash "
        "compatibility defect before the complete service path passed. CLI RED `789e40723d1bec13d7a08f03d9b079695f299b0b` "
        "passed Ruff and 778 existing tests while failing only the three intentionally missing CLI contracts. "
        "Deployment RED on `6793f6b769cdde0e0eb6b2afaa74186337e5f56b` reached pytest and failed only for "
        "the absent systemd unit and Cloud Shell helper. Implementation head `eee3dc56ee6d8271f4ee1c41357f2ecb4efb2374` "
        "then passed the full Python/PostgreSQL and dashboard CI lanes. A final CI-coverage RED at "
        "`3b84c11dd6f52eaf8f3a17746d8e11e12d7e71ce` produced exactly one failure because the new helper was not "
        "yet included in shell-syntax validation; after adding the `bash -n` gate, exact-head CI run `33378572573` "
        "passed completely on implementation checkpoint `22663baa8105ca9c5768bc6defb0f51605eb2130`, including 784 Python "
        "tests, Ruff, deployment validation, health, dashboard tests, TypeScript typecheck, and production build."
    )
    changelog_addition = changelog_anchor + (
        "\n\nA later acceptance-quality review found that the first host helper could technically PASS when no ended "
        "unevaluated prediction existed, which would not prove the new production path had actually executed. "
        "TDD tightened that boundary without changing runtime semantics: RED commit "
        "`685bcbd91fcee51fcc14584d334c4b0311baac1c` passed Ruff and 784 existing tests while failing only the new "
        "no-op-acceptance contract. GREEN commit `bfe1cac144cf3fc9d91d2e057faea9d8117434d6` requires at least one "
        "candidate, at least one resolved market, candidate/snapshot accounting parity, canonical label coverage, "
        "and a newly created immutable evaluation for every resolved candidate; PR-context CI run `33380984760` "
        "then passed all 785 Python tests, Ruff, deployment validation, health, dashboard tests, TypeScript "
        "typecheck, and production build. This is only an acceptance-path exercise requirement, not a prospective "
        "sample-size, profitability, calibration, or live-promotion threshold."
    )
    replace_once(CHANGELOG, changelog_anchor, changelog_addition)

    state_anchor = (
        '    "implementation_test_count": 784,\n'
        '    "host_acceptance_status": "PENDING",'
    )
    state_addition = (
        '    "implementation_test_count": 784,\n'
        '    "acceptance_contract_commit": "bfe1cac144cf3fc9d91d2e057faea9d8117434d6",\n'
        '    "acceptance_contract_ci_run_id": 33380984760,\n'
        '    "acceptance_contract_ci_passed": true,\n'
        '    "acceptance_contract_test_count": 785,\n'
        '    "host_acceptance_requires_exercised_path": true,\n'
        '    "host_acceptance_requires_candidate": true,\n'
        '    "host_acceptance_requires_resolved_market": true,\n'
        '    "host_acceptance_requires_new_evaluation_per_resolved_candidate": true,\n'
        '    "host_acceptance_requirement_is_live_gate_sample_threshold": false,\n'
        '    "host_acceptance_status": "PENDING",'
    )
    replace_once(STATE, state_anchor, state_addition)

    state_text = STATE.read_text()
    timestamp_prefix = '  "updated_at": "'
    start = state_text.index(timestamp_prefix) + len(timestamp_prefix)
    end = state_text.index('"', start)
    timestamp = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    STATE.write_text(state_text[:start] + timestamp + state_text[end:])


if __name__ == "__main__":
    main()
