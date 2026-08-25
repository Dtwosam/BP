# Phase 7 Immutable Historical Expansion — Design Clarification

**Date:** 25 August 2026  
**Phase:** 7 — baselines before fancy ML  
**Applies to:** `docs/superpowers/specs/2026-08-25-phase-7-baseline-modeling-design.md`

## Trigger

Production acceptance on the fixed 2026-08-24 UTC day first expanded historical Coinbase and Polymarket inputs, then attempted to regenerate `core-v1` features. A Phase 6 feature at `2026-08-24T18:01:00Z` already existed. The newly recovered historical observations had effective timestamps no later than that feature time, so a strict recomputation selected more input data and produced different immutable semantics. `MarketFeatureRepository` correctly raised `FeatureConflict` rather than rewriting the Phase 6 row.

This is not a reason to weaken conflict detection, delete accepted data, or create synthetic history.

## Normative clarification

Phase 7 historical expansion is **additive over immutable feature snapshots**.

The normal/default feature-generation contract is unchanged:

- first insert creates the snapshot;
- exact semantic rerun returns existing;
- semantic drift at an existing natural key raises `FeatureConflict`;
- no existing `core-v1` row may be updated or replaced.

For the controlled Phase 7 production expansion only, feature generation may use the explicit `--preserve-existing` mode after historical backfill. That mode must:

1. look up an existing `(condition_id, feature_at, feature_version)` key before source readers execute;
2. verify slug, horizon, market start/end, and feature offset still match the frozen row;
3. count the row as existing without recomputing its feature payload or input fingerprint;
4. compute and insert only natural keys that do not already exist;
5. preserve the original payload, missing flags, source cutoffs, hashes, and `generated_at` of every frozen row.

Static metadata disagreement still fails closed.

## Production evidence

Before Phase 7 feature expansion, the host gate records the count of existing `core-v1` rows in the fixed acceptance day as `FEATURE_ROWS_BEFORE`. The `existing` count reported by `generate_features.py --preserve-existing` is recorded as `PRESERVED_FEATURE_ROWS` and must equal `FEATURE_ROWS_BEFORE`.

A mismatch is a hard failure.

## Modeling consequence

The full-day `supervised-core-v1` dataset may contain a small subset of Phase 6 snapshots whose missingness reflects the data actually materialized when those rows were originally frozen, alongside later snapshots generated after additional historical inputs were recovered. This is intentional provenance, not leakage. Missing flags and input fingerprints remain explicit predictors/metadata under the original Phase 6 contract.

Phase 7 must not backdate recovered data by rewriting old snapshots. A future feature version may deliberately adopt a different source-snapshot policy, but that would be a new feature-version decision rather than a Phase 7 acceptance shortcut.

## Safety boundary

This clarification changes no trading setting and authorizes no order execution. Live trading remains disabled and trade/loss limits remain zero throughout Phase 7.
