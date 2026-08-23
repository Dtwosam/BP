# AGENTS.md — Instructions for ChatGPT, Codex, and Future Developers

This repository is the BTC Polymarket Prediction Engine.

## Mandatory first reads

Before doing project work, read in this order:

1. `docs/MASTER-SOURCE-OF-TRUTH.md`
2. `PROJECT_STATE.json`
3. `docs/BUILD-ORDER.md`
4. `docs/DECISION-LOG.md`
5. most recent entries in `docs/CHANGELOG.md`

The Master Source of Truth wins if anything conflicts.

## Core behavior

- Continue from `current_phase`; do not restart the project from memory.
- Do not invent completed work.
- Verify repository state/tests before claiming a phase is complete.
- Keep horizons configurable.
- Do not assume a 10m Polymarket market exists.
- Treat current Polymarket market rules as external facts that can change.
- The user's 80% target is a desired research target, not a claim.
- Do not optimize for accuracy alone; track expected value and P&L.
- Avoid time-series leakage.
- Do not use random train/test shuffles for overlapping short-horizon observations.
- Preserve immutable predictions.
- Do not silently modify historical results.
- Default trading mode is `RESEARCH` or `PAPER`.
- Never enable live trading without the documented gate and explicit user authorization.
- Never request the user to paste a seed phrase/private key into chat.
- Never commit secrets.

## Development discipline

For code changes:

1. inspect current code/state;
2. make the smallest coherent change;
3. add/update tests;
4. run relevant tests;
5. report what passed/failed;
6. update project state only when evidence supports it.

## Documentation discipline

If architecture/scope/phase gates change, update:

- `docs/MASTER-SOURCE-OF-TRUTH.md`
- `docs/DECISION-LOG.md`
- `docs/CHANGELOG.md`
- `PROJECT_STATE.json`

Avoid copying mutable facts into many documents.

## External API discipline

Before coding against a third-party API:

- prefer official docs;
- pin/record assumptions;
- handle rate limits/reconnects;
- preserve source timestamps;
- add fixtures/tests around parsing;
- treat API schema/rules as changeable.

## Model discipline

Every candidate must be compared against baselines.

Required before promotion:

- chronological validation;
- leakage checks;
- walk-forward results;
- calibration;
- realistic execution assumptions;
- versioned dataset/features/model.

## Definition of “done”

A task is not done because code was written.

It is done when:

- implementation exists;
- tests exist where appropriate;
- tests pass;
- operational behavior is verified;
- documentation/state is updated when required.

## Handoff at the end of a work session

Always leave:

- what changed;
- tests/commands run;
- current phase;
- blockers/open questions;
- exact next action.

Write this into `PROJECT_STATE.json` and changelog when project state materially changes.
