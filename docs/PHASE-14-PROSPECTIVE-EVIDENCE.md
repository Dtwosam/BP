# Phase 14 Prospective Evidence Reporter

This is a Phase 14 follow-up. It does not open Phase 15 and does not authorize live trading.

## Purpose

The prospective evidence reporter is deliberately separate from the paper execution worker. It reads existing immutable paper settlements, immutable prediction evaluations, and paper reconciliation evidence and emits a JSON summary for later Master live-gate reassessment.

It must not write to execution, prediction, evaluation, research, or live-readiness ledgers. It has no promotion command and reports `automatic_promotion=false`.

## Report contents

The report includes:

- settled prospective paper-trade count;
- prediction-evaluation count;
- realized after-cost paper P&L total and mean;
- deterministic bootstrap 95% interval for mean realized P&L;
- raw Brier and log-loss means;
- calibrated Brier and log-loss means;
- current paper reconciliation evidence;
- prospective evidence gates using only `pass`, `fail`, or `insufficient_evidence`;
- the existing Phase 14 Master live-gate snapshot.

The reporter does not invent a fixed minimum sample size. Until the canonical project specification approves a numerical prospective calibration acceptance threshold, `calibration_acceptable` remains `insufficient_evidence` even though the calibration metrics themselves are reported.

## Safety boundary

The CLI refuses to run unless all three real-money interlocks are disabled:

```text
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
```

The Cloud Shell wrapper additionally verifies that `bp-paper-execution.service` is active before and after the report, runs the candidate from an exact-SHA detached worktree, requires the deployed `/opt/bp` checkout to remain unchanged, requires `automatic_promotion=false`, and requires the existing Master live gate to remain `fail` for this Phase 14 follow-up.

The wrapper does not install packages, run migrations, restart services, stop services, or alter the production checkout.

## Exact-head host report

From Google Cloud Shell, on a CI-green candidate SHA:

```bash
export PROSPECTIVE_EVIDENCE_HEAD='<40-character-candidate-sha>'
bash scripts/deploy/phase14_prospective_evidence_cloudshell.sh
```

Defaults target the existing `bp-recorder` VM in `us-east1-c` and `/etc/bp/bp.env`. Override `PROSPECTIVE_EVIDENCE_PROJECT`, `PROSPECTIVE_EVIDENCE_ZONE`, `PROSPECTIVE_EVIDENCE_VM`, `PROSPECTIVE_EVIDENCE_BRANCH`, or `PROSPECTIVE_EVIDENCE_ENV_FILE` only when the actual deployment differs.

A successful wrapper run ends with:

```text
PAPER_SERVICE=active
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
PROSPECTIVE_EVIDENCE_HOST_REPORT=PASS
```

The JSON printed immediately before those markers is evidence, not an automatic live-gate update. Any future change to the Master live gate must be deliberate, documented, evidence-backed, and separately reviewed.
