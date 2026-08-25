# Phase 5 Official Outcome/Label Pipeline Design

**Date:** 25 August 2026
**Phase:** 5 — official outcome/label pipeline
**Authority:** subordinate to `docs/MASTER-SOURCE-OF-TRUTH.md`, `PROJECT_STATE.json`, and `docs/BUILD-ORDER.md`.

## Goal

Create deterministic, leakage-safe labels for BTC Up/Down markets from official Polymarket resolution evidence already preserved by Phase 4. Phase 5 does not build features, models, backtests, predictions, or execution.

## Source of truth for labels

The authoritative label remains the official Polymarket resolved outcome. Phase 4 already stores immutable Gamma market snapshots with `downloaded_at`, canonical payload SHA-256, condition ID, slug, and the raw Gamma payload.

Phase 5 derives labels only from those stored snapshots. It does not infer labels from Coinbase, Bybit, token-price history, or local recorder data.

The existing strict Gamma parser remains authoritative for market normalization and current rule fingerprinting. A resolved outcome is eligible only when the payload is closed and `outcomePrices` contains exactly one winner at 1 and all remaining outcomes at 0, with the winner equal to `Up` or `Down`.

## Label eligibility

A stored market snapshot is eligible only when all of the following are true:

1. the raw payload parses successfully using the existing `parse_gamma_market` contract;
2. the parsed condition ID and slug match the snapshot envelope;
3. the target market's parsed `window_start_at` falls inside the requested half-open generation window;
4. the market is closed;
5. `resolved_outcome` is exactly `Up` or `Down`;
6. the snapshot `downloaded_at` is timezone-aware and is greater than or equal to the market `window_end_at`.

A snapshot that claims a resolution before the market end is a leakage/data-integrity violation and fails closed.

Unresolved, still-open, or ambiguous snapshots are not labels and are skipped.

## Multiple snapshots for one market

All eligible resolved snapshots for the same condition must agree on:

- `resolved_outcome`;
- `rules_hash`;
- `resolution_source`;
- parsed market start/end;
- horizon.

Any disagreement is a hard `LabelSourceConflict` failure.

When snapshots agree, the earliest eligible resolved snapshot ordered by `(downloaded_at, id)` is the canonical source snapshot. This minimizes post-resolution drift while remaining deterministic.

## Stored label schema

Add an additive `market_labels` table with one immutable semantic label per `(condition_id, label_version)`.

Required fields:

- condition ID;
- Gamma market ID;
- slug;
- horizon seconds;
- market start/end UTC;
- official outcome (`Up`/`Down`);
- official start reference price, nullable;
- official end reference price, nullable;
- resolution source;
- rules hash;
- label source identifier;
- label version;
- source snapshot SHA-256;
- source observed/downloaded timestamp;
- first generated timestamp.

Phase 5 V1 uses `label_version = official-outcome-v1` and `label_source = polymarket_gamma_snapshot`.

The authentic Gamma payloads inspected for this project do not expose a verified explicit authoritative start/end reference-price field. Therefore `start_reference` and `end_reference` remain `NULL` in V1. They must not be substituted with exchange prices or inferred from token-price history. A future phase/version may populate them only after a first-party field/source is independently verified.

## Immutability and reruns

The natural key is `(condition_id, label_version)`.

- identical semantic rerun: existing/no-op;
- changed outcome, rules, market window, source snapshot, or other semantic label field at the same natural key: raise `LabelConflict`;
- `generated_at` records the first insertion and is not rewritten on a no-op rerun.

The pipeline therefore cannot silently relabel historical training targets.

## Generation API and CLI

Add a small label service that:

1. loads stored Gamma snapshots relevant to the requested market-start window;
2. parses and groups them by condition;
3. rejects leakage/source conflicts;
4. selects the canonical earliest eligible resolved snapshot;
5. stores immutable labels;
6. reports counts for inserted, existing, unresolved/skipped, and target conditions considered.

Add `scripts/generate_labels.py` with required timezone-aware `--start` and `--end` arguments plus the existing environment-file/database conventions.

The command has no network dependency. Phase 4 backfill/discovery remains responsible for acquiring the raw Gamma snapshots.

## Leakage guarantees

Automated tests must prove:

- open markets cannot become labels;
- closed markets without a one-hot official outcome cannot become labels;
- a resolved snapshot observed before market end is rejected;
- the canonical label source timestamp is never earlier than market end;
- repeated generation produces no duplicate label;
- conflicting official outcomes for one condition fail closed;
- a changed stored label at the same natural key fails closed.

Phase 5 does not expose any feature-building interface, so labels cannot be joined into feature-time data in this phase.

## Acceptance

Phase 5 is acceptable when:

- migration/schema is additive and reproducible;
- unit leakage tests pass;
- PostgreSQL-backed rerun/conflict tests pass;
- the CLI can generate labels from Phase 4 snapshot fixtures/data without network access;
- unresolved markets are demonstrably excluded;
- identical reruns insert zero new labels;
- source/rule provenance is preserved per label;
- documentation records that official reference prices remain unavailable in V1 rather than synthesized;
- live trading remains disabled;
- Phase 6 remains blocked until Phase 5 closeout is recorded.
