# Phase 14 — V4 source-observation fingerprint replay preflight

**Status (2026-10-11): Repository-only candidate. No production replay has been run or authorized by this file.** Research-only/zero money and Phase 14 HOLD continue; no trading, promotion, additional holdout access, service restart, model training, schema change, or deployment.

## Verified implementation identity and remaining boundary

The independently completed high-water database survey (22,604 forward V4 feature rows, 5,651 eligible markets, zero integrity violations and zero pending) verified the stored `feature_hash` from `features` and `missing_flags`. It did **not** reconstruct `input_fingerprint` from source observations.

Source inspection at the frozen bounded runtime `e7a21462374a1c19beae878c98ca319f13fb2d69` and optimized runtime `2d82c38a526b8f1b579766100e3e832715977ac2` verified matching Git blob identities for `v4_service.py`, `v4_sources.py`, `v4_calculators.py`, `v4_models.py`, `features/sources.py`, and `features/hashing.py`. Optimization changed V4 forward coverage scanning, not feature-generation fingerprint inputs. The separately pinned production recorder checkout `a352c66e` predates some V4 modules and must **not** be used as the feature-generation import path for replay.

V4 `input_fingerprint` hashes sorted canonical descriptors: the target descriptor plus the compact BTC source observations actually selected. Each observed source descriptor includes primary-key ID, compact bucket and event timestamps, venue/stream/instrument, price, freshness, and as-of age. The feature itself stores neither the full descriptor sequence nor an immutable source-row-ID list.

The canonical compact-state retention **configuration target** is 90 days, but the presence of each historical source state in the current production database is **not proven merely by this setting**. Some historical compact states can be absent/expired or differ from the values visible when the feature was first generated. A replay mismatch is not automatically corruption or evidence of target leakage; a match attests only to that feature at its recorded feature time under the currently retained source view.

## Proposed strictly bounded first-stage audit

Script: `scripts/audit_phase14_v4_single_source_replay_readonly.py`.

- Exactly **one** forward V4 feature is inspected per invocation: highest matching feature ID by default, or one explicit positive `--feature-id`. Selects by indexed primary key; no row scans or bulk joins.
- Rebuilds through the immutable optimized V4 runtime's original `build_v4_feature(..., repository=None)`, at the original `feature_at` and stored `generated_at`. This selects historical `market_state_1s` snapshots strictly as of each source lookup cutoff, but performs **no feature insertion**.
- Applies research/zero-money gating, `default_transaction_read_only=on`, autocommit, 2,000ms statement timeout, 500ms lock timeout, 3s connection timeout and no retries. No Postgres config changes or file writes.
- Rechecks stored `feature_hash` integrity; compares original/reconstructed `input_fingerprint`, `features`, `missing_flags`, `feature_hash` and `source_cutoffs`. Reports only the feature ID, feature timestamp, changed-field names and status—not raw source payloads or connection secrets.
- Verdict `PASS_EXACT_SOURCE_REPLAY` is narrowly scoped to one stored feature and its **current** historical source view. `INCONCLUSIVE_SOURCE_VIEW_CHANGED` or `INCONCLUSIVE_SOURCE_DESCRIPTOR_CHANGED` blocks a source-provenance claim. `FAIL_STORED_FEATURE_HASH` or `FAIL_REPLAY_INCONSISTENCY` requires independent diagnosis. A query/lookup exception or absent target is inconclusive.
- This is not a complete 22,604-feature provenance audit, not a historical snapshot proof, and not source-state immutability proof. No V4 training/paper model is activated.

## Candidate operator invocation — *not yet executable until review, merge, post-merge CI*

From a clean, reviewed main checkout, after independently confirming the exact protected V4 runtime and zero-money settings, stream the script via stdin to Python with the **immutable optimized V4** source on `PYTHONPATH`. Do not install the script, start the collector, edit the pinned recorder, or execute a writer.

```bash
set -o pipefail
gcloud compute ssh bp-recorder \
  --project=project-4397f2c0-7098-4c1c-abb \
  --zone=us-east1-c --quiet \
  --command='test "$(sudo -n readlink -f /var/lib/bp/runtime/v4-forward-current)" = "/var/lib/bp/runtime/v4-forward-2d82c38a526b8f1b579766100e3e832715977ac2" && sudo -n -u bp env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/var/lib/bp/runtime/v4-forward-2d82c38a526b8f1b579766100e3e832715977ac2/src /opt/bp/.venv/bin/python - --env-file /etc/bp/bp.env' \
  < scripts/audit_phase14_v4_single_source_replay_readonly.py \
  2>&1 | tee "$HOME/bp-v4-one-source-replay.log"
```

Do **not** rerun on mismatch, raise SQL timeouts, or expand the sample automatically. Preserve the output and assess the exact class of missing/changed compact inputs first. A single PASS does **not** close the original-input-fingerprint provenance gate globally.

See the completed [V4 production evidence](PHASE-14-V4-COVERAGE-OPTIMIZATION-PRODUCTION-EVIDENCE.md) and [high-water audit runbook](PHASE-14-V4-FULL-HISTORY-AUDIT-RUNBOOK.md).

**No production contact or command execution is part of preparing this repository candidate.**
