from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file_path = Path(path)
    text = file_path.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(
            f"{path}: expected exactly one match, found {count}: {old[:100]!r}"
        )
    file_path.write_text(text.replace(old, new, 1), encoding="utf-8")


state = "PROJECT_STATE.json"
replace_once(
    state,
    '"source_of_truth_version": "0.14.4"',
    '"source_of_truth_version": "0.14.5"',
)
replace_once(
    state,
    '"updated_at": "2026-09-02T10:20:00Z"',
    '"updated_at": "2026-09-02T14:25:00Z"',
)
replace_once(
    state,
    '    "Phase 14 Gate A market-price V2 code slices implemented and CI-verified under review: dedicated last-trade provenance, exact-token timestamp-coherent 5m core-v2-last-trade features, and outcome-blind read-only coverage reporting; not deployed and no V2 policy selected"',
    '    "Phase 14 Gate A timestamp-coherent market-price V2 production rollout passed on exact deployed head d077e45f24704e6038c947169c84527e954de975 with a forward epoch of 2026-09-02T12:18:02Z, one completed post-epoch market, four immutable core-v2-last-trade rows, zero future-cutoff violations, and no V2 policy selection",\n    "Phase 14 V2 forward coverage collector implementation completed and CI-verified with outcome-blind restart-safe collection, hardened systemd oneshot/timer packaging, and rollback-capable rollout tooling; collector production activation remains separately gated and not performed"',
)
replace_once(
    state,
    '  "not_completed": [\n    "Phase 15 controlled live launch",',
    '  "not_completed": [\n    "Production activation of bp-v2-forward-coverage.timer",\n    "Phase 15 controlled live launch",',
)
replace_once(
    state,
    '    "Complete Gate A review packaging on phase14-market-price-v2-design: require fresh final branch-head CI, inspect the full diff against main, open a draft PR, and stop before any production deployment",',
    '    "Review the completed V2 forward coverage collector package as a draft PR; require docs-complete exact-head CI and PR-triggered smoke gates, inspect the full diff against deployed head d077e45f24704e6038c947169c84527e954de975, and stop before collector production activation",',
)
replace_once(
    state,
    '    "Treat any recorder/V2 production rollout as a separate explicit authorization step with bounded acceptance and rollback; until authorized, Gate A remains undeployed and no forward production V2 coverage epoch exists",',
    '    "Treat activation of bp-v2-forward-coverage.timer and the continuous forward collector rollout as a separate explicit authorization step with bounded acceptance and rollback; Gate A itself is already production-accepted, but the continuous collector remains undeployed",',
)
replace_once(
    state,
    '    "gate_a_implementation_status": "IMPLEMENTATION_UNDER_REVIEW_NOT_DEPLOYED",',
    '    "gate_a_implementation_status": "DEPLOYED_PRODUCTION_ACCEPTED_RESEARCH_ONLY",',
)
replace_once(
    state,
    '    "gate_a_policy_selected": false,\n    "gate_a_production_rollout_performed": false,\n    "gate_a_v1_feature_service_byte_identical_to_main": true,',
    '    "gate_a_policy_selected": false,\n    "gate_a_production_rollout_performed": true,\n    "gate_a_production_rollout_commit": "d077e45f24704e6038c947169c84527e954de975",\n    "gate_a_forward_epoch": "2026-09-02T12:18:02Z",\n    "gate_a_host_acceptance_verdict": "PASS",\n    "gate_a_forward_validation_market_count": 1,\n    "gate_a_forward_validation_row_count": 4,\n    "gate_a_future_cutoff_violation_count": 0,\n    "gate_a_coverage_input_sha256": "44592883ca47d18337b4c4385f3e34badc00bc30e5a124a02c0c4c99cccf6891",\n    "gate_a_sanitized_evidence": "docs/evidence/phase-14-v2-gate-a-rollout-20260902.json",\n    "gate_a_v1_feature_service_byte_identical_to_main": true,',
)
replace_once(
    state,
    '    "gate_a_next_step": "fresh_final_branch_head_ci_and_draft_pr_review_then_separate_explicit_rollout_authorization",\n    "automatic_promotion": false,',
    '    "gate_a_next_step": "review_forward_coverage_collector_then_separate_explicit_collector_rollout_authorization",\n    "forward_coverage_collector_branch": "phase14-v2-gate-a-rollout-evidence",\n    "forward_coverage_collector_implementation_checkpoint": "af8b2e4f741851df1b055b57c3fc44e39c7b6b06",\n    "forward_coverage_collector_implementation_status": "IMPLEMENTATION_COMPLETE_REVIEW_NOT_DEPLOYED",\n    "forward_coverage_collector_prepackaging_ci_run": 1978,\n    "forward_coverage_collector_prepackaging_ci_run_id": 33639062997,\n    "forward_coverage_collector_prepackaging_test_count": 860,\n    "forward_coverage_collector_service": "bp-v2-forward-coverage.service",\n    "forward_coverage_collector_timer": "bp-v2-forward-coverage.timer",\n    "forward_coverage_collector_rollout_helper": "scripts/deploy/phase14_v2_forward_coverage_rollout_cloudshell.sh",\n    "forward_coverage_collector_production_rollout_performed": false,\n    "forward_coverage_collector_policy_selected": false,\n    "automatic_promotion": false,',
)

build = Path("docs/BUILD-ORDER.md")
build_text = build.read_text(encoding="utf-8")
marker = "## Immediate next action\n"
if build_text.count(marker) != 1:
    raise SystemExit("BUILD-ORDER immediate-action marker mismatch")
build_head = build_text.split(marker, 1)[0]
build_tail = """## Immediate next action

**Review the completed continuous V2 forward-coverage collector as a draft PR; do not activate it on production, do not select a V2 policy, and do not start Phase 15.**

Gate A itself is now production-accepted. The guarded rollout advanced `/opt/bp` from `be1f82f65d15b2e172495e6ae934ec9a78648c32` to `d077e45f24704e6038c947169c84527e954de975` with canonical forward epoch `2026-09-02T12:18:02Z`. Host acceptance proved real dedicated Polymarket last-trade provenance, preserved its timestamp across unrelated market activity, generated exactly four immutable `core-v2-last-trade` rows at offsets 60/120/180/240 for one completed post-epoch 5m market, found zero future-source-cutoff violations, and kept `policy_selected=false` and `automatic_promotion=false`. Sanitized evidence is `docs/evidence/phase-14-v2-gate-a-rollout-20260902.json`.

The next implementation package on `phase14-v2-gate-a-rollout-evidence` adds only continuous **outcome-blind** collection. It discovers completed post-epoch 5m markets with missing approved V2 natural keys, materializes only missing immutable rows, reports descriptive V2 coverage, and uses the database as the restart-safe checkpoint. It adds a hardened `bp-v2-forward-coverage.service` oneshot plus persistent one-minute `bp-v2-forward-coverage.timer`, and an exact-head rollback-capable rollout helper. Rollback may restore checkout/unit state but must never delete or rewrite `market_features`.

Pre-packaging exact-head CI #1978 (`33639062997`) passed all 860 Python tests, Ruff, deployment-asset validation including Python compile and Bash syntax gates, health checks, dashboard tests, TypeScript typecheck, and dashboard production build. Full diff review against deployed head `d077e45f24704e6038c947169c84527e954de975` shows only the collector/orchestration, systemd/rollout packaging, tests, CI validation, and documentation/evidence files. Frozen V1 feature service, `live_prediction`, `calibration`, and `execution` paths are unchanged; no migration, wallet/secret change, risk-limit increase, geoblock bypass, live activation, or Phase 15 implementation is present.

The continuous collector has **not** been deployed or enabled. A draft-PR review and fresh docs-complete CI/PR smoke gates come first. Any later activation of `bp-v2-forward-coverage.timer` is a separate explicit production-rollout authorization with bounded acceptance and rollback. Passing that operational rollout would establish continuing forward coverage only; it would not authorize Gate B, labels/outcomes, timing/freshness/model/calibration/edge selection, paper execution, promotion, or live trading.

Existing V1 predictions, evaluations, paper orders/fills/settlements, and P&L remain a separate immutable evidence epoch. Do not rewrite or blend them into V2 profitability evidence, and do not use the V1 failures or the single Gate A acceptance market to choose V2 freshness, timing, calibration, model, or edge parameters. Selected-book freshness remains exactly 10 seconds.

`MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0` remain mandatory. `automatic_promotion=false`; the Master live gate remains `fail`; geographic/compliance restrictions must not be bypassed; Phase 15 remains blocked; and explicit real-money authorization remains separately required.
"""
build.write_text(build_head + build_tail, encoding="utf-8")

master = "docs/MASTER-SOURCE-OF-TRUTH.md"
replace_once(master, "As of **30 August 2026**:", "As of **2 September 2026**:")
replace_once(
    master,
    "- Phase 15 controlled live launch is not permitted.\n\n`PROJECT_STATE.json` is the machine-readable record",
    "- Phase 14 Gate A timestamp-coherent V2 is production-accepted from forward epoch `2026-09-02T12:18:02Z`; the continuous V2 forward-coverage collector package is implemented and under review but is not deployed or enabled;\n- Phase 15 controlled live launch is not permitted.\n\n`PROJECT_STATE.json` is the machine-readable record",
)
master_path = Path(master)
master_text = master_path.read_text(encoding="utf-8")
section36 = """

---

# 36. Phase 14 Gate A production acceptance and continuous V2 forward-coverage boundary

On 2 September 2026, the separately authorized research-only Gate A production rollout passed and advanced the deployed checkout from `be1f82f65d15b2e172495e6ae934ec9a78648c32` to `d077e45f24704e6038c947169c84527e954de975`. The canonical V2 forward epoch is `2026-09-02T12:18:02Z`. All seven established research services remained active and effective safety remained `MODE=research`, `LIVE_TRADING_ENABLED=false`, `MAX_TRADE_SIZE_USD=0`, and `MAX_DAILY_LOSS_USD=0`.

Host acceptance proved dedicated first-party Polymarket WebSocket last-trade provenance using provider source timestamp, BP receipt timestamp, price, and dedupe identity, and proved that unrelated later market activity did not refresh the dedicated last-trade timestamp. One fully completed post-epoch 5m market then produced exactly four immutable `core-v2-last-trade` feature rows at the frozen 60/120/180/240-second offsets, with zero future-source-cutoff violations. The outcome-blind coverage report observed one market/four rows, emitted `policy_selected=false` and `automatic_promotion=false`, and recorded coverage input SHA-256 `44592883ca47d18337b4c4385f3e34badc00bc30e5a124a02c0c4c99cccf6891`. Sanitized evidence is `docs/evidence/phase-14-v2-gate-a-rollout-20260902.json`.

That one-market operational sample is not a V2 timing, freshness, calibration, model, edge, profitability, or promotion result. Observed selected-book ages were about 0.4–1.1 seconds while last-trade source ages reached about 18–20 seconds, reinforcing that policy selection must remain deferred and independent of both the V1 failure sample and this single acceptance market. Selected-book freshness remains frozen at 10 seconds, and no V2 last-trade freshness threshold is selected.

The approved next operational package is a continuous **outcome-blind V2 forward-coverage collector**. It is limited to completed 5m markets starting at or after the canonical forward epoch, exactly the four approved feature offsets, immutable/preserve-existing semantics, descriptive coverage reporting, structured logging, and restart-safe reconciliation using database natural keys rather than a mutable cursor. It must not read labels/outcomes, compute accuracy/P&L/calibration metrics, choose a V2 policy, create V2 predictions or paper orders, mutate V1 evidence, alter the selected-book freshness rule, enable live trading, or begin Phase 15.

The implementation package adds `bp_engine.features.v2_forward`, a thin research-zero-money CLI/script, hardened `bp-v2-forward-coverage.service` and persistent one-minute `bp-v2-forward-coverage.timer`, plus an exact-head rollback-capable rollout helper. The oneshot may connect only to the local PostgreSQL service over localhost/Unix networking; non-loopback IP traffic is denied by systemd. Rollback may restore checkout and unit state but must never delete, truncate, or rewrite immutable `market_features` or any other research ledger.

Pre-packaging exact-head CI #1978 (`33639062997`) passed all 860 Python tests, Ruff, deployment validation, health checks, dashboard tests/typecheck/build, Python wrapper compilation, and rollout-helper Bash syntax. Full source-diff review against deployed head `d077e45f24704e6038c947169c84527e954de975` leaves the frozen V1 feature service and the complete `live_prediction`, `calibration`, and `execution` paths unchanged, with no migration, live activation, wallet/secret path, risk-limit increase, geographic bypass, V2 economic policy, or Phase 15 implementation.

The continuous collector is **not yet deployed or enabled** at this checkpoint. Code review/merge and a separately explicit research-only production-rollout authorization are required before activating `bp-v2-forward-coverage.timer`. Even a later successful collector rollout would authorize only continuing outcome-blind forward evidence collection; it would not authorize the next research gate, policy selection, promotion, paper/live execution changes, or real-money trading. The Section 4.3 Master live gate remains `fail`, `automatic_promotion=false`, and Phase 15 remains blocked.
"""
if "# 36. Phase 14 Gate A production acceptance and continuous V2 forward-coverage boundary" in master_text:
    raise SystemExit("MASTER section 36 already exists")
master_path.write_text(master_text.rstrip() + section36 + "\n", encoding="utf-8")

changelog_path = Path("docs/CHANGELOG.md")
changelog = changelog_path.read_text(encoding="utf-8")
changelog_marker = "# Changelog\n\n"
if not changelog.startswith(changelog_marker):
    raise SystemExit("CHANGELOG header mismatch")
entry = """## 0.14.5 — 2 September 2026

Phase 14 Gate A timestamp-coherent V2 is now **production-accepted for research evidence collection only**. The separately authorized guarded rollout advanced `/opt/bp` from `be1f82f65d15b2e172495e6ae934ec9a78648c32` to `d077e45f24704e6038c947169c84527e954de975` and established canonical forward epoch `2026-09-02T12:18:02Z`. All seven established research services stayed active with `MODE=research`, `LIVE_TRADING_ENABLED=false`, and both real-money limits at zero.

Gate A host acceptance demonstrated real dedicated Polymarket WebSocket last-trade provider/receipt timestamps and dedupe identity, proved unrelated later market activity did not refresh the trade timestamp, and generated exactly four immutable `core-v2-last-trade` rows at 60/120/180/240 seconds for one completed post-epoch 5m market. Future-source-cutoff violations were zero. The outcome-blind coverage report observed one market/four rows and remained `policy_selected=false` / `automatic_promotion=false`; sanitized evidence is `docs/evidence/phase-14-v2-gate-a-rollout-20260902.json`.

The follow-up continuous V2 forward-coverage collector is now implementation-complete on `phase14-v2-gate-a-rollout-evidence` but **not deployed or enabled**. It adds restart-safe discovery of completed post-epoch 5m markets with missing approved V2 keys, preserve-existing immutable generation, descriptive coverage reporting, a thin research-zero-money CLI, hardened systemd oneshot/timer packaging, and an exact-head rollback-capable rollout helper. The service permits local PostgreSQL networking while systemd denies non-loopback IP traffic; rollback restores code/unit state only and never deletes or rewrites `market_features`.

Pre-packaging exact-head CI #1978 (`33639062997`) passed all 860 Python tests, Ruff, deployment-asset validation, health checks, dashboard tests/typecheck/build, wrapper compilation, and rollout-helper Bash syntax. Full diff review against deployed head `d077e45f24704e6038c947169c84527e954de975` leaves frozen V1 feature-service, live-prediction, calibration, and execution paths unchanged and introduces no migration, V2 label/outcome join, economic freshness/timing/model/calibration/edge policy, wallet/secret change, risk-limit increase, geoblock bypass, live activation, or Phase 15 implementation.

Fresh documentation-complete CI plus PR-triggered Historical Backfill Smoke, Live Recorder Smoke, and Recorder Short Soak remain review gates before merge. Production activation of `bp-v2-forward-coverage.timer` is a **separate explicit authorization** after review/merge; this packaging work does not run that rollout. Existing V1 evidence remains immutable and separate, selected-book freshness stays exactly 10 seconds, `automatic_promotion=false`, the Master live gate remains `fail`, and Phase 15 remains blocked.

"""
if "## 0.14.5 — 2 September 2026" in changelog:
    raise SystemExit("CHANGELOG 0.14.5 already exists")
changelog_path.write_text(
    changelog_marker + entry + changelog[len(changelog_marker) :],
    encoding="utf-8",
)

spec = "docs/superpowers/specs/2026-09-02-phase-14-v2-forward-coverage-collector-design.md"
replace_once(
    spec,
    "**Status:** Design approved; implementation not started",
    "**Status:** Implementation complete; review packaging; not deployed",
)
replace_once(
    spec,
    "**Canonical decision:** D-033\n\n## 1. Purpose",
    "**Canonical decision:** D-033\n\n**Implementation checkpoint:** `af8b2e4f741851df1b055b57c3fc44e39c7b6b06`  \n**Pre-packaging verification:** CI #1978 / run `33639062997`, 860 tests, full Python/deployment/dashboard GREEN  \n**Production collector activation:** not performed; separately authorized after review/merge\n\n## 1. Purpose",
)

plan = "docs/superpowers/plans/2026-09-02-phase-14-v2-forward-coverage-collector.md"
replace_once(
    plan,
    "**Spec:** `docs/superpowers/specs/2026-09-02-phase-14-v2-forward-coverage-collector-design.md`\n\n## Global Constraints",
    "**Spec:** `docs/superpowers/specs/2026-09-02-phase-14-v2-forward-coverage-collector-design.md`\n\n**Implementation status:** Tasks 1–5 complete and pre-packaging exact-head CI #1978 (`33639062997`) GREEN with 860 tests; Task 6 review packaging in progress. Production collector rollout remains separately gated and has not been performed.\n\n## Global Constraints",
)
