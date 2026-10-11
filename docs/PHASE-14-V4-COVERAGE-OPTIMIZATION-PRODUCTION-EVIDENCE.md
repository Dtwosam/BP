# Phase 14 — optimized V4 forward coverage production evidence (2026-10-11)

**Decision:** V4-only optimization deployment and the observed sustained-stability/limited-sample acceptance **PASS**. Full-history independent database integrity audit **NOT COMPLETE**. Phase 14 **HOLD** continues; no live trading or automatic model promotion. This record does not authorize any additional production mutation.

## Provenance and scope

- Repository candidate and observed optimized runtime: `2d82c38a526b8f1b579766100e3e832715977ac2`, runtime path `/var/lib/bp/runtime/v4-forward-2d82c38a526b8f1b579766100e3e832715977ac2`.
- Recorder/production checkout pinned and unchanged during guarded acceptance: `a352c66ec0110925727bc40de767ee4ba981f965`; previous bounded V4 runtime: `e7a21462374a1c19beae878c98ca319f13fb2d69`.
- Guarded rollout output: `PHASE14_V4_COVERAGE_ROLLOUT=PASS`, `ROLLOUT_COMMAND_EXIT=0`, `V4_COVERAGE_ROLLOUT_SAFETY_AND_DB_BASELINE=PASS`, `V4_OLD_ONESHOT_QUIESCED=true`, `V4_COVERAGE_FEATURE_COMMIT_VERIFIED=true`; total V4 feature rows **21,928 -> 21,932** (+4). This was a **separately operator-authorized, narrow V4-only production mutation**.
- Host-held machine evidence: `/var/lib/bp/evidence/phase14-v4-coverage-optimized-2d82c38a526b8f1b579766100e3e832715977ac2-NynkkSdd/evidence.json`, with `verdict=PASS` and UTC acceptance timestamp `2026-10-10T22:05:12.384377+00:00`.
- This document records **operator-supplied read-only command output** after the rollout. The production evidence file and journals were not copied into this repository and the observations were not independently rerun by CI. Avoid treating this record as a cryptographic attestation or as exhaustive production validation.

## Independent runtime and scheduled operation

- Post-rollout read-only check: V4 runtime symlink resolved to the exact optimized SHA; V4 timer active and enabled; PostgreSQL, recorder, frozen V3 predictor and V3 paper-execution services active.
- Initial post-rollout journal slice: **13 commits**, **0 failure log events**, commit times **39.01s min / 59.44s median / 83.35s max**.
- Longer journal checkpoint: **788.5 minutes** (13h 8m 30s), from acceptance through approximately `2026-10-11T11:13:40Z`. **708 discovery starts, 707 committed stages, 0 matched failure log messages** (`start operation timed out`, `Failed with result`, `Traceback`). The one-start difference could be an in-flight cycle; it is **not proof of an extra completed cycle or failure**.
- Longer-window committed-stage elapsed times: **25.8s minimum, 53.0s median, 98.3s maximum**, below the unchanged **120s** systemd service timeout. Last commit at `2026-10-11T11:13:31.556150Z`, age **8.3s** when sampled.
- Journal `generation_start remaining=` observations: first **0**, latest **0**, maximum **0** during that window. These are collector-generated pending-count observations, not independent full-history database backlog verification.
- Research/zero-money environment files read-only checked **PASS**; automatic promotion **DISABLED**; timer restored; all four protected services active. Long-window checkpoint printed `V4_SUSTAINED_CHECKPOINT=SUSTAINED_STABILITY_CANDIDATE` and `PRODUCTION_MUTATION=false`.

## Database integrity: bounded sample, not exhaustive

- Initial independent full-epoch query against `market_features` ran under `default_transaction_read_only=on` and was canceled at an **8-second `statement_timeout`** during its first broad row/JSON integrity count. **No corruption was established**, but that audit provided no feature-integrity or backlog-age result. Do not remove query limits or increase shared production DB load merely to make this check pass.
- Follow-up smaller, separately read-only audit used **3-second statement timeout**, **500ms lock timeout**, recent primary-key feature range and the **32 most recent discovered markets**, with indexed joins by `condition_id`. Output:
  - `DATABASE_READ_ONLY=PASS`
  - `FEATURE_ROWS_SAMPLED=48`; `SAMPLE_INTEGRITY_VIOLATIONS=0` (static geometry, expected 60/120/180/240-second offset membership, JSON object types, and stored `feature_hash` recomputed from `features` and `missing_flags`).
  - `RECENT_ELIGIBLE_MARKETS_SAMPLED=22`; `RECENT_PENDING_MARKETS=0`; `OLDEST_RECENT_PENDING_AGE_SECONDS=NONE`.
  - `V4_INTEGRITY_AUDIT=BOUNDED_SAMPLE_PASS`; `PRODUCTION_MUTATION=false`.
- **Limitations:** only 48 selected feature rows and 22 eligible recent markets were checked; original source-observation `input_fingerprint` was not independently reconstructed; old rows and full-history pending-market age remain outside this sampled audit. A successful sample is not a full integrity closeout.

## Decision and remaining gates

1. Record **guarded deployment acceptance PASS** and **sustained observed scheduled stability PASS** for this V4-only optimization; sampled database integrity and recent-market backlog checks also **PASS within their tested scope**.
2. **Do not declare exhaustive historical feature integrity, full-history pending-age clearance, or a measured controlled production speedup.** The earlier 3.66x synthetic benchmark is not a production speedup claim.
3. Future exhaustive checking should be engineered as an independently reviewed, bounded/read-only, index-aware or chunked method and assessed for recorder/DB load before use. Any production schema change, service restart, new collector deployment, or trading change requires its own authorization.
4. Preserve the pinned recorder/V3 services and existing V4 timer. Phase 14 **HOLD**, `LIVE_TRADING_ENABLED=false`, research-only zero-money limits, and disabled automatic promotion remain mandatory.

See `docs/PHASE-14-V4-COVERAGE-OPTIMIZATION-READINESS.md` for the historical authorization and guarded rollout contracts.
