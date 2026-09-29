# Phase 15 — Continuous live manual-approval transition tooling

**Date:** 29 September 2026  
**Status:** engineering tooling only; no production transition performed by this change

## Purpose

PR #374 merged the continuous Telegram-approved fast-live implementation to
`main`. Production source truth still records the legacy operator Telegram
auto-approver as active and does not yet contain a continuous
`fast_live_preauthorization`.

The transition to the manual-per-trade continuous session therefore has two
separate production decisions:

1. stop and persistently disable the old operator-side auto-clicker;
2. create a reviewed continuous source-truth authorization after that shutdown
   has been proven.

This tooling makes those steps explicit and evidence-bound. Merely merging the
tooling does not perform either production action.

## Read-only operator preflight

`scripts/deploy/phase15_v3_fast_live_manual_transition_preflight.sh`

Run this from a clean checkout at current `main` on the operator machine.

It verifies that source truth is still in the expected pre-transition state:

- global live trading flag is false;
- Phase 15 live trading flag is false;
- legacy operator auto-approver status is active and
  `live_auto_approve_authorized=true`;
- no continuous `fast_live_preauthorization` exists.

It then reports, without mutation:

- platform and service manager;
- known auto-approver service name;
- whether the service is currently active;
- whether it is enabled/loaded;
- whether a matching `bp_telegram_auto_approver` process exists.

The helper explicitly reports
`MUTATIONS_PERFORMED=false`,
`SOURCE_TRUTH_MUTATED=false`, and
`REAL_ORDER_SUBMITTED=false`.

## Explicit operator auto-approver deactivation

`scripts/deploy/phase15_v3_telegram_auto_approver_deactivate_operator.sh`

This is a production mutation and must not be run without explicit
authorization. It requires:

`PHASE15_ACCEPT_TELEGRAM_AUTO_APPROVER_DEACTIVATION=I_ACCEPT_STOP_LEGACY_TELEGRAM_AUTO_APPROVER`

It also requires an absolute evidence path in
`BP_TELEGRAM_AUTO_APPROVER_DEACTIVATION_EVIDENCE`.

Before changing the service it requires:

- clean checkout;
- checkout equals current remote `main`;
- source truth still says the old auto-approver is active/authorized.

On macOS it disables the known launchd label
`com.bp.telegram-auto-approver`, unloads it if currently loaded, verifies the
label is disabled, and verifies no matching process remains.

On Linux it stops and disables the known user systemd unit
`bp-telegram-auto-approver.service`, then verifies it is inactive and
disabled.

The helper does **not**:

- delete the Telegram user session;
- mutate Telegram credentials;
- edit `PROJECT_STATE.json`;
- enable live trading;
- deploy fast-live services;
- touch the Johannesburg kill switch;
- submit an order.

It writes mode-0600 JSON evidence bound to the exact repository `main` and
the exact pre-transition `PROJECT_STATE.json` bytes.

Because source truth is intentionally not changed by the deactivation helper,
the evidence records both facts separately:

- runtime auto-approval is no longer effective;
- source truth still says auto-approve was authorized before the reviewed
  source-truth transition.

## Reviewable continuous authorization candidate

`scripts/deploy/phase15_v3_fast_live_build_authorization_candidate.py`

This helper consumes:

- current `PROJECT_STATE.json`;
- the exact auto-approver deactivation evidence;
- the current expected main SHA;
- an explicit authorization ID;
- an explicit **new** source-of-truth version;
- an explicit future authorization expiry.

It requires the explicit candidate-generation phrase:

`I_ACCEPT_GENERATE_REVIEWABLE_CONTINUOUS_LIVE_AUTHORIZATION_CANDIDATE`

The helper refuses to overwrite `PROJECT_STATE.json`. It writes a separate
candidate state file plus separate candidate evidence.

The candidate transition:

- records the old auto-approver as
  `DEACTIVATED_FOR_MANUAL_TELEGRAM_CONTINUOUS_LIVE`;
- sets `live_auto_approve_authorized=false`;
- adds `fast_live_preauthorization` with
  `status=AUTHORIZED_CONTINUOUS_SESSION`;
- uses authorization mode
  `manual-telegram-continuous-v1`;
- requires fresh Telegram approval;
- sets one network submission attempt per intent;
- preserves the frozen V3 prediction/execution versions;
- preserves $5 target / $10 trade / $10 exposure / $10 daily-loss /
  one-loss / 0.075 edge limits;
- preserves executor country ZA;
- preserves two-second fast-live transport TTL;
- keeps global and Phase 15 live trading flags false;
- records deployment, activation, kill-switch removal, runtime authorization,
  and real-order submission as not performed.

Before writing output, the generated candidate is passed through the same
`verify_source_authorization(..., continuous_session=True,
requires_telegram_approval=True)` used by the live runtime.

The candidate evidence records the exact source state hash, deactivation
evidence hash, candidate state hash, authorization ID, version, and expiry.

## Review boundary

Generating the candidate is still not production activation. The candidate must
be reviewed as a source-truth change before it can become `main`.

Only after a reviewed source-truth authorization is merged can the existing
fast-live sequence proceed:

1. build deterministic release from that exact current `main`;
2. stage both hosts;
3. run the read-only fast-live readiness preflight;
4. with separate explicit live-session authorization, run activation.

This tooling intentionally does not merge a source-truth authorization,
stage production hosts, remove the Johannesburg kill switch, or submit orders.
