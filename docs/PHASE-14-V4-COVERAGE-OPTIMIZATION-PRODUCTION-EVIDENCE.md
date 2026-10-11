# Phase 14 — optimized V4 forward coverage production evidence (2026-10-11)

**Decision as of 2026-10-11:** V4-only deployment, observed sustained stability, sampled checks, and the independently run **bounded full-history integrity/backlog survey** are **PASS within their respective defined scopes**. This is not a single-transaction snapshot or independent source-observation fingerprint reconstruction. Phase 14 **HOLD** continues; no live trading or automatic model promotion. This record authorizes no further production mutation.

## Provenance and scope

- Repository candidate and observed optimized runtime: `2d82c38a526b8f1b579766100e3e832715977ac2`, runtime path `/var/lib/bp/runtime/v4-forward-2d82c38a526b8f1b579766100e3e832715977ac2`.
- Recorder/production checkout pinned and unchanged during guarded acceptance: `a352c66ec0110925727bc40de767ee4ba981f965`; previous bounded V4 runtime: `e7a21462374a1c19beae878c98ca319f13fb2d69`.
- Guarded rollout output: `PHASE14_V4_COVERAGE_ROLLOUT=PASS`, `ROLLOUT_COMMAND_EXIT=0`, `V4_COVERAGE_ROLLOUT_SAFETY_AND_DB_BASELINE=PASS`, `V4_OLD_ONESHOT_QUIESCED=true`, `V4_COVERAGE_FEATURE_COMMIT_VERIFIED=true`; total V4 feature rows **21,928 -> 21,932** (+4). This was a **separately operator-authorized, narrow V4-only production mutation**.
- Host-held machine evidence: `/var/lib/bp/evidence/phase14-v4-coverage-optimized-2d82c38a526b8f1b579766100e3e832715977ac2-NynkkSdd/evidence.json`, with `verdict=PASS` and UTC acceptance timestamp `2026-10-10T22:05:12.384377+00:00`.
- This document records **operator-supplied read-only command output** after the rollout. The production evidence file and journals were not copied into this repository and the observations were not independently rerun by CI. The subsequently completed bounded high-water survey is complete within its own defined database scope, not a cryptographic attestation, source-level provenance audit, or globally atomic DB snapshot.

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

## Independent bounded full-history V4 audit — completed 2026-10-11

Following merge of [PR #572](https://github.com/Dtwosam/BP/pull/572) at exact main `36fc5c4df02de793fe95d1ef47dd32b7b43f060d` (post-merge 2/2 checks passed), the operator fast-forwarded the Mac's clean `main` to that SHA and ran `scripts/audit_phase14_v4_forward_history_readonly.py` via stdin on the production host. The command checked the expected optimized V4 symlink before connecting as `bp` to the shared PostgreSQL database in read-only mode.

**Exact operator-reported acceptance markers:**

```text
DATABASE_READ_ONLY=PASS
FEATURE_ID_HIGH_WATER=361875
MARKET_ID_HIGH_WATER=16702
FORWARD_V4_FEATURE_ROWS_AUDITED=22604
FEATURE_INTEGRITY_VIOLATIONS=0
ELIGIBLE_MARKETS_AUDITED=5651
PENDING_MARKETS=0
OLDEST_PENDING_AGE_SECONDS=NONE
INPUT_FINGERPRINT_RECOMPUTED=false
SINGLE_TRANSACTION_SNAPSHOT=false
V4_HISTORY_AUDIT=PASS_BOUNDED_FULL_HISTORY
PRODUCTION_MUTATION=false
V4_HISTORY_AUDIT_COMMAND=PASS
```

- The survey walked primary-key pages through **feature ID 361875** and **market ID 16702**, applying the defined V4 feature version, forward epoch, 300-second eligible market horizon, and completed-market cutoff. It audited **22,604** applicable forward V4 rows and **5,651** eligible markets; the totals are consistent with four required offsets per market (`5651 * 4 = 22604`). No feature-integrity violations or eligible markets missing offsets were reported.
- The independent row checks covered feature geometry, stored `feature_hash` recomputation, source-cutoff and predictor-key/regime invariants, and source-fingerprint **format**, not original observation-derived fingerprint reconstruction.
- The audit used 64-row throttled primary-key keyset queries, 3,000ms per-statement timeout, 500ms lock timeout, a 900-second overall budget and read-only PostgreSQL settings. It completed without hitting the limits; the earlier broad 8-second query timeout remains historical context, not an unresolved result of this new survey.
- Because the scan uses high-water marks and autocommit pagination rather than one consistent snapshot, completion applies **to the captured ID boundaries and their audited predicates**, not to rows created later or a proof of atomicity under concurrent writes. The returned `PENDING_MARKETS=0` resolves *bounded audited historical* backlog-age concerns, but future ongoing production backlog must still be observed.
- `PRODUCTION_MUTATION=false` was emitted. The repository records the operator-supplied output; the live host log was not independently fetched by this GitHub change.

**Gate decision:** bounded forward-V4 feature integrity and eligible-market backlog verification **PASS**. Source-observation fingerprint recomputation and any higher-level Phase 14 holdout/trading gates remain separate and unproven.

## Decision and remaining gates

1. Record **guarded V4-only rollout PASS**, **sustained observed scheduled stability PASS**, **bounded feature-integrity high-water survey PASS**, and **zero pending eligible markets within the market high-water boundary**. The smaller 48-feature/22-market sample also passed, but was superseded in breadth by the full-high-water survey.
2. **Do not infer original input-observation fingerprint provenance, a globally consistent transaction snapshot, future backlog clearance, or a measured controlled production speedup.** The earlier 3.66x synthetic benchmark is not a production speedup claim.
3. A source-observation fingerprint reconstruction or other expanded provenance assessment would be **new, separately scoped research engineering**. No new production queries, schema changes, service restarts, runtime deployment, or trading changes are authorized by this document.
4. Preserve the pinned recorder/V3 services and existing V4 timer. Phase 14 **HOLD**, `LIVE_TRADING_ENABLED=false`, research-only zero-money limits, and disabled automatic promotion remain mandatory.

See `docs/PHASE-14-V4-COVERAGE-OPTIMIZATION-READINESS.md` for the historical authorization and guarded rollout contracts.
