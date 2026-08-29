# Phase 13 Improvement Loop V1 — Design

Date: 2026-08-29
Status: accepted for implementation under the project’s standing autonomous-approval instruction
Branch: `phase-13-improvement-loop-v1`
Base: `main@5f04b5f0188ee20eb252200dbd8fc88e64a7cc44`

## 1. Purpose

Phase 13 turns model improvement into an auditable research process rather than a sequence of ad-hoc retrains.

The phase must answer a narrow question:

> Can a frozen challenger beat the current accepted policy on executable-price economics and calibration using evidence that was not repeatedly reused to select it?

The phase does **not** authorize real-money trading, stake increases, automatic self-modification, or automatic model promotion. `LIVE_TRADING_ENABLED=false`, maximum real trade size remains zero, and the accepted Phase 12 paper-execution worker remains money-disabled.

## 2. Evidence that constrains the design

The accepted system already proves why Phase 13 cannot optimize headline accuracy alone:

- Phase 8 5m ordinary OOS accuracy exceeded 80% while observed-ask gross P&L was negative.
- Phase 8 15m ordinary OOS accuracy was very high but the untouched final holdout degraded sharply and P&L was negative.
- Phase 9 5m ordinary OOS produced only three trades with positive assumed-cost P&L, while the frozen final holdout also produced three trades and lost money.
- Phase 9 15m correctly selected `no_trade` throughout.
- Phase 12 proved realistic, causal paper execution and reconciliation, but its current paper sample is far too small to claim profitability.

Therefore Phase 13 must prefer abstention to weak evidence and must treat a losing or inconclusive independent confirmation as a failed promotion, even if development metrics look attractive.

## 3. Chosen architecture

Phase 13 adds a new `bp_engine.improvement` package and three append-only records:

1. **Experiment spec** — the frozen hypothesis and evaluation plan.
2. **Evaluation report** — deterministic challenger-versus-champion evidence with exact provenance.
3. **Promotion decision** — a deliberate append-only decision to reject, keep the champion, or promote a challenger for future research/paper use.

The implementation reuses the existing modeling, walk-forward, calibration/edge, live-prediction, and paper-execution systems. It does not fork or duplicate those engines.

### Why this architecture

A mutable “current experiment” table would make it too easy to rewrite hypotheses after seeing results. A single monolithic retraining service would blur training, selection, evaluation, and promotion boundaries. Separate immutable records make the temporal order auditable:

`hypothesis frozen -> challenger produced -> evaluation frozen -> decision recorded`.

## 4. Frozen champion baseline

V1 begins from the accepted Phase 9 policies already used by Phase 10/12:

- 5m: `phase9-300-c9f0e00eb7836af08008c66909f8f179`
- 15m: `phase9-900-15c234f25588b23cce73a12f87a2e2ea`

The 15m `no_trade` policy is a valid champion. Phase 13 must not manufacture a challenger trade merely to create activity.

A champion reference includes the full immutable source chain where available:

`training run -> walk-forward run -> calibration/edge run -> semantic SHA-256`.

The experiment spec freezes the champion reference before challenger results exist.

## 5. Experiment specification

`ImprovementExperimentSpec` is immutable and contains at minimum:

- deterministic `experiment_id`;
- `experiment_version`;
- human-readable hypothesis;
- horizon seconds;
- change family: `feature`, `model`, `calibration`, `timing`, `abstention`, or `cost_assumption`;
- exact champion Phase 9 run id and semantic SHA-256;
- challenger definition/configuration;
- approved source dataset/feature/label versions;
- research time range;
- selection-evidence policy;
- independent-confirmation policy;
- executable-cost assumptions;
- primary and guardrail metrics;
- creation timestamp.

The semantic hash excludes only the creation timestamp. Re-registering an identical spec is an existing/no-op result. Reusing an `experiment_id` with different semantics fails closed.

The hypothesis must be falsifiable. Examples:

- “A validation-selected max-spread guard reduces negative 5m executable-price tail outcomes without degrading calibration.”
- “A new feature group improves 15m calibration enough to create positive independent-confirmation expectancy; otherwise retain `no_trade`."

“Try more features” is not a valid hypothesis.

## 6. Evidence roles and anti-overfitting rules

Every evaluation input is assigned one explicit role:

- `development_train`
- `development_validation`
- `ordinary_oos`
- `fresh_holdout`
- `prospective_paper`

### Selection boundary

Only development train/validation evidence may select hyperparameters, feature subsets, calibration method, timing, or trade threshold.

Ordinary OOS may be used for iteration diagnostics, but repeated use makes it research-development evidence rather than untouched confirmation.

The existing Phase 8/9 final holdouts are historical evidence already observed by the project. They may be reported as legacy evidence but are **not** eligible as fresh promotion confirmation for new Phase 13 challengers.

### Independent confirmation

A challenger cannot become promotion-eligible without evidence that was unavailable for selecting it. V1 allows either:

1. a chronologically later `fresh_holdout` frozen before outcomes are consulted for that challenger; or
2. `prospective_paper` evidence generated after the challenger specification and policy are frozen.

The evidence ledger stores exact condition/prediction identifiers and role so the system can detect prohibited reuse.

## 7. Comparison metrics

### Primary economic metric

Primary comparison is net executable-price economics, not accuracy:

- paper-settled realized P&L when official settlement evidence exists; otherwise
- the existing assumed-cost executable edge/P&L metric under explicitly frozen assumptions.

The report always preserves trade count and coverage so a tiny lucky sample cannot masquerade as a robust gain.

### Calibration guardrails

Track at minimum:

- log loss;
- Brier score;
- ECE/calibration summary;
- accuracy as descriptive evidence only.

A challenger cannot be promoted by P&L while materially destroying probability quality unless a later version explicitly changes that contract.

### Risk/robustness diagnostics

Track at minimum:

- trade coverage;
- mean net P&L per trade;
- total net P&L;
- maximum drawdown where a sequential P&L series exists;
- losing streak where available;
- horizon/regime breakdowns when the underlying evaluation provides them;
- count of missing/unexecutable observations;
- source/provenance/integrity violations.

## 8. Statistical uncertainty

There is no fixed magic sample count.

Promotion eligibility must include uncertainty rather than relying on a round-number minimum alone. V1 uses a deterministic paired market-level bootstrap for challenger-minus-champion economic deltas when both policies are evaluated on the same independent-confirmation markets.

Rules:

- resampling unit is `condition_id`, never individual overlapping feature rows;
- bootstrap seed is derived from the evaluation semantic hash input so reruns are deterministic;
- report the 95% interval for mean net P&L delta;
- promotion requires the lower bound to be strictly positive when the comparison is defined;
- if the interval cannot be computed meaningfully, promotion is ineligible rather than guessed.

For prospective paper data, unresolved markets are not treated as zero-P&L outcomes.

## 9. Promotion policy

An evaluation may report `promotion_eligible=true` only when all of the following hold:

1. experiment and challenger semantics were frozen before independent confirmation;
2. exact source provenance and hashes validate;
3. no train/validation/test/holdout boundary violation is present;
4. deterministic rerun semantics match;
5. independent confirmation exists;
6. primary economic delta is positive and its 95% lower confidence bound is positive when paired comparison is available;
7. calibration guardrails pass;
8. no integrity, reconciliation, or execution-semantic violation is present;
9. current cash/exposure constraints remain valid for paper evidence;
10. no live-money path is introduced.

Promotion eligibility is not promotion.

A separate append-only `ImprovementPromotionDecision` records one of:

- `reject_challenger`
- `keep_champion`
- `promote_challenger`

`promote_challenger` is rejected unless the referenced evaluation is promotion-eligible. Promotion affects only future **research/paper** policy selection in Phase 13. It does not enable live trading.

## 10. Storage model

Migration `0013_improvement_loop.sql` adds three tables.

### `improvement_experiments`

- `experiment_id` unique
- version
- horizon
- change family
- champion run id/hash
- hypothesis
- canonical spec JSON
- semantic SHA-256
- created timestamp

### `improvement_evaluations`

- `evaluation_id` unique
- `experiment_id`
- challenger identity/config/hash
- exact evidence manifest JSON
- champion metrics JSON
- challenger metrics JSON
- comparison/uncertainty JSON
- `promotion_eligible`
- explicit ineligibility reasons
- full report JSON
- semantic SHA-256
- created timestamp

### `improvement_promotion_decisions`

- `decision_id` unique
- `evaluation_id`
- decision
- operator/research rationale
- resulting champion reference JSON
- semantic SHA-256
- created timestamp

No Phase 13 table supports UPDATE-based rewriting of prior semantics. Repository methods use identical-rerun/no-op and semantic-conflict behavior consistent with existing Phase 7–12 immutable registries.

## 11. Package boundaries

### `improvement.models`
Frozen dataclasses/enums and version constants.

### `improvement.hashing`
Canonical JSON and deterministic ids/hashes.

### `improvement.repository`
Append-only Postgres persistence with conflict detection.

### `improvement.evidence`
Evidence-role validation and prohibited-reuse checks.

### `improvement.statistics`
Deterministic paired market-level bootstrap and sequential P&L diagnostics.

### `improvement.comparison`
Pure champion/challenger comparison and promotion-eligibility logic.

### `improvement.source`
Read-only loaders for accepted training/backtest/calibration runs plus evaluated live/paper evidence. It may not mutate any source ledger.

### `improvement.service`
Orchestrates register/evaluate/decide operations while preserving temporal and provenance boundaries.

### `improvement.cli`
Network-free/read-only-to-source command surface for reproducible research operations.

## 12. First challenger direction

The first production research hypothesis after the framework is installed should target the existing weakness rather than add complexity for its own sake:

> For 5m, test whether a validation-selected spread/abstention guard can reduce negative executable-price outcomes versus the accepted Phase 9 policy without degrading calibration.

Rationale:

- the accepted 5m holdout loss occurred despite apparently positive ordinary-OOS edge;
- the existing edge engine already supports `max_spread`, so this tests execution-quality selectivity before adding a more complex predictive model;
- it is cheap, interpretable, and directly tied to tradability.

For 15m, the initial challenger baseline remains `no_trade` unless a separately specified hypothesis produces independent evidence strong enough to justify selective entry.

## 13. CLI contract

V1 commands:

```text
python -m bp_engine.improvement register --spec <json>
python -m bp_engine.improvement evaluate --experiment-id <id> ...
python -m bp_engine.improvement decide --evaluation-id <id> --decision <...> --rationale <text>
python -m bp_engine.improvement report --experiment-id <id>
```

Commands emit structured JSON and never place real orders.

The first implementation tasks may expose lower-level Python APIs before all CLI subcommands are complete, but the final Phase 13 acceptance must provide one reproducible command path.

## 14. Failure behavior

Fail closed on:

- changed semantics under an existing immutable id;
- missing/incorrect source hashes;
- final-holdout evidence passed as selection evidence;
- evidence generated before the challenger freeze when it is labeled prospective confirmation;
- duplicate independent-confirmation evidence reused in a prohibited role;
- non-finite metrics;
- malformed or empty hypothesis;
- unsupported change family;
- attempted promotion of an ineligible evaluation;
- any request to enable live execution.

Inconclusive evidence means `keep_champion`, not “best effort” promotion.

## 15. Testing strategy

Use RED -> GREEN for every implementation slice.

Required tests include:

- deterministic experiment id/hash;
- immutable experiment rerun/conflict behavior;
- immutable evaluation/decision rerun/conflict behavior;
- evidence-role validation;
- existing final holdout rejected as fresh confirmation;
- prospective evidence must post-date challenger freeze;
- condition-level paired bootstrap is deterministic;
- unresolved paper outcomes excluded from realized-P&L comparison;
- lower confidence bound <= 0 makes promotion ineligible;
- calibration guardrail failure makes promotion ineligible;
- ineligible evaluation cannot be promoted;
- `keep_champion`/`reject_challenger` remain valid outcomes;
- no live-trading setting or real execution path is changed.

CI continues to run the complete existing suite, deployment validation, health check, and dashboard lane.

## 16. Production acceptance

Phase 13 cannot close on framework tests alone. Production-host acceptance must demonstrate:

- exact accepted commit and green CI;
- migration applied idempotently;
- an experiment spec stored and immediate rerun returns existing/no-op;
- semantic mutation under the same id fails closed;
- source champion hashes match accepted Phase 9 records;
- one real challenger evaluation is produced from production research data;
- evaluation records exact evidence roles/ids;
- no prohibited holdout/selection reuse;
- deterministic evaluation rerun;
- promotion eligibility follows the frozen rule rather than a hand-edited result;
- a deliberate decision record is stored;
- recorder, paper worker, PostgreSQL, dashboard API, and dashboard web remain healthy;
- reconciliation remains OK;
- real execution remains unavailable and live trading remains disabled.

A challenger does **not** have to win for Phase 13 to succeed. A rigorous `keep_champion` or `reject_challenger` result is valid acceptance evidence.

## 17. Out of scope

Phase 13 V1 does not add:

- wallet/funder/signing support;
- real Polymarket order placement or cancellation;
- live risk limits above zero;
- automatic stake sizing;
- automatic model promotion;
- martingale/Kelly/loss chasing;
- deep learning by default;
- reinforcement learning;
- LLM-based prediction;
- automatic feature generation loops.

Those would either violate current gates or add complexity before evidence justifies it.
