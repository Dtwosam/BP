# Phase 14 — bounded read-only V4 full-history audit runbook

**Status:** Candidate engineering for review, not yet executed against production. Phase 14 **HOLD**; research-only, zero money, live trading disabled, no automatic promotion.

This follow-up addresses the independent integrity audit that previously hit an 8-second PostgreSQL statement timeout during a single broad full-history scan. It **does not** raise PostgreSQL limits, change indexes, restart services, or run a V4 feature writer.

## Audit implementation and controls

Script: `scripts/audit_phase14_v4_forward_history_readonly.py`.

- Uses the *pinned production checkout's* `bp_engine.config.Settings` and `bp_engine.features.hashing.canonical_hash` via the existing bp virtualenv; it does not replace or change `/opt/bp`.
- Blocks unless research mode, live disabled, and exposure/trade/daily-loss limits all remain zero.
- Connects with `default_transaction_read_only=on`, 3,000ms **per SQL statement** timeout, 500ms lock timeout, 3-second connection timeout, and autocommit so no long snapshot transaction is held.
- Captures `max(id)` high-water marks for `market_features` and `polymarket_markets` and uses 64-row primary-key keyset pages with a 150ms delay between pages. The full run has a 900-second budget and fails incomplete on any SQL error or exceeded budget; no retry loop or read-limits increase.
- Reviews every V4 feature row with `id <= feature_high_water` and `market_start_at >= 2026-09-20T12:40:53Z`: exact market/offset geometry; JSON map types and boolean missing flags; persisted `feature_hash` recalculation; 64-character source fingerprint **format**; no Polymarket predictor keys, invalid regimes or future/unparseable source cutoffs.
- Reviews every 300s market in the forward epoch with `id <= market_high_water` and `end_at <= audit_start - 15s`; for each bounded market page, uses existing `market_features(condition_id, feature_at)` index to fetch offset evidence and requires **exactly** `[60,120,180,240]`, without duplicates or extra offsets. Reports pending count, oldest pending age, and up to five sample condition IDs.
- These high-water surveys are **not a single point-in-time database snapshot**. If the collector writes concurrently, a recently completed market may appear pending transiently; review failures using only read-only evidence rather than forcing a re-run. No source-observation fingerprint reconstruction is claimed.
- The scripted `PASS_BOUNDED_FULL_HISTORY` verdict means all rows and markets within the captured primary-key high-water boundaries were examined under the stated checks. It is not a backtest, a source-level data provenance proof, an authorization for another rollout, or a lift of Phase 14 HOLD.

## Operator path (only after PR review, merge, and green exact-main CI)

From a clean local `main` checkout at the **reviewed audit commit**, with the production V4 runtime still pinned to `2d82c38a526b8f1b579766100e3e832715977ac2` and protected core controls still active, stream the script over SSH **without copying it to or changing the host filesystem**:

```bash
gcloud compute ssh bp-recorder \
  --project=project-4397f2c0-7098-4c1c-abb \
  --zone=us-east1-c --quiet \
  --command='sudo -n -u bp env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/bp/src /opt/bp/.venv/bin/python - --env-file /etc/bp/bp.env' \
  < scripts/audit_phase14_v4_forward_history_readonly.py \
  2>&1 | tee "$HOME/bp-phase14-v4-history-audit.log"
```

Enable `set -o pipefail` in the invoking shell if pipeline exit-code preservation is required. **Do not** invoke any V4 collector CLI, restart timer/services, or run an unbounded SQL `COUNT(*)`/JSON scan instead.

Interpretation:

- `DATABASE_READ_ONLY=PASS` and `V4_HISTORY_AUDIT=PASS_BOUNDED_FULL_HISTORY` with exit 0: bounded complete survey within two recorded high-water marks; record feature row count, zero violations, eligible market count, zero pending, no mutation.
- `V4_HISTORY_AUDIT=FAIL_INTEGRITY` or `REVIEW_PENDING_BACKLOG` with exit 1: fail or backlog; investigate read-only, do not rerun blindly.
- `V4_HISTORY_AUDIT=INCOMPLETE` or `INCONCLUSIVE_NO_ROWS` with nonzero exit: partial/inconclusive evidence only. Do not raise timeout, disable throttling, or treat earlier pages as a PASS.

The existing independently observed operator evidence remains in [the production evidence record](PHASE-14-V4-COVERAGE-OPTIMIZATION-PRODUCTION-EVIDENCE.md). Prior 48-feature/22-market bounded sample PASS remains valid but is **not** replaced by a PASS until a whole-high-water audit actually completes.

**No production command has been executed by creating this script or this runbook.**
