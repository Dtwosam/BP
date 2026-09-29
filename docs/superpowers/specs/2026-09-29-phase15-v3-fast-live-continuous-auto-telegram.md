# Phase 15 — Continuous V3 fast-live with Telegram auto-approval

**Date:** 29 September 2026  
**Status:** engineering implementation in review; production activation not performed

## User-required behavior

Continuous live trading keeps the existing operator Telegram auto-approver.

The approval step is still present for every trade. It is not replaced by a
blanket session approval.

The intended critical path is:

`V3 signal -> exact candidate -> [Telegram auto-approval + final risk +
Johannesburg preparation in parallel] -> final join gate -> fresh executable
book -> at most one network submission attempt for the exact intent ->
reconciliation/settlement -> continue monitoring`.

Every candidate gets a fresh Telegram nonce and expiry. The auto-approver sends
the same exact APPROVE callback that the operator could press manually.

Auto-approval alone never authorizes submission. Final risk, exact request
binding, Johannesburg checks, fresh-book availability, frozen-price limits,
exposure/loss controls, unresolved-trade recovery, and exactly-once submission
semantics remain mandatory.

## Explicit approval modes

Continuous source truth supports two distinct modes:

- `manual-telegram-continuous-v1`
- `auto-telegram-continuous-v1`

Both require `requires_telegram_approval=true` and
`max_network_submission_attempts_per_intent=1`.

Manual mode remains mutually exclusive with an active operator auto-approver.

Auto mode requires all of the following:

- operator auto-approver source truth remains `ACTIVE_*`;
- `live_auto_approve_authorized=true`;
- source truth records the exact reviewed approval-contract Git blob;
- source truth explicitly records continuous candidate prompt auto-approval as
  authorized.

Runtime authorization must use the exact same approval mode as source truth. A
runtime file cannot switch manual to auto or auto to manual.

## Reviewed candidate prompt

Continuous fast-live begins Telegram approval while final risk and Johannesburg
preparation are still running.

The prompt is therefore intentionally different from the historical finalized
prompt:

`BP V3 LIVE TRADE CANDIDATE`

The exact candidate text is now produced by
`build_candidate_prompt()` in
`src/bp_engine/execution/telegram_approval.py`.

The listener imports that function rather than carrying a separate free-form
copy of the candidate text.

The operator auto-approver pins that same source file by Git blob SHA and accepts
only the exact reviewed candidate grammar. A mutation such as changing
`Approval does not bypass them.` causes a prompt mismatch and no callback.

The reviewed approval-contract blob for this change is:

`5676efcb60840f4533a7f43b3c6a7efab9e97541`

The auto-approver retains its existing identity, keyboard-shape, nonce,
deadline, replay, reservation-before-click, and no-automatic-retry controls.

## Per-trade auto-approval semantics

A valid candidate message must still satisfy all existing auto-approver
conditions:

- exact configured BP bot identity;
- exact operator account identity;
- private incoming message;
- not forwarded, replied, edited, or historical;
- exact reviewed prompt grammar;
- exact `APPROVE` / `SKIP` keyboard;
- shared exact nonce;
- displayed time remaining at or above the minimum;
- local approval deadline including the two-second safety margin;
- durable reservation before callback dispatch;
- no second callback after duplicate, ambiguous result, or restart.

A successful Telegram callback changes only the approval side of the join gate.
It cannot create a live order by itself.

## Source authorization binding

`verify_source_authorization(... continuous_session=True,
requires_telegram_approval=True)` enforces the selected approval mode.

For `auto-telegram-continuous-v1`, it additionally requires:

- active + authorized operator auto-approver;
- exact reviewed approval-contract blob;
- `continuous_candidate_prompt_authorized=true`.

For `manual-telegram-continuous-v1`, an active auto-approver remains a
fail-closed error.

`verify_runtime_authorization` requires runtime
`authorization_mode` to equal the source-truth mode exactly.

## Operator compatibility preflight

`scripts/deploy/phase15_v3_telegram_auto_approver_continuous_preflight_operator.sh`

This is read-only.

From a clean macOS checkout at exact current `main`, it verifies:

- global and Phase 15 live flags remain false;
- legacy auto-approval source truth is still active and authorized;
- no continuous fast-live authorization exists yet;
- the code's approval-contract pins agree;
- exact candidate prompt is accepted;
- a mutated candidate prompt is rejected;
- `~/.config/bp/telegram-auto-approver.env` contains the exact live-enable
  value;
- the installed launchd plist points to the exact current checkout;
- the launchd service is active and has a live PID.

The preflight explicitly reports that a process restart is still required to
prove the running service loaded the new code.

It does not restart anything, edit source truth, contact the recorder/executor,
or submit an order.

## Explicit operator runtime upgrade

`scripts/deploy/phase15_v3_telegram_auto_approver_upgrade_continuous_operator.sh`

This is a production mutation and must not be run from a generic continuation
request.

It requires the exact acceptance phrase:

`I_ACCEPT_RESTART_AUTO_APPROVER_WITH_CONTINUOUS_CANDIDATE_CONTRACT`

The helper:

- requires a clean exact-main checkout;
- requires the existing auto-approver to remain source-truth-authorized;
- requires both live flags false and no continuous fast-live authorization yet;
- verifies the new approval contract and an offline exact-candidate decider
  self-test;
- verifies the installed launchd service points to the exact checkout;
- verifies the existing live auto-approval environment;
- runs the service's non-network `--check-config`;
- restarts only `com.bp.telegram-auto-approver` with
  `launchctl kickstart -k`;
- requires a new PID and then requires that PID to remain stable;
- writes mode-0600 evidence under `docs/evidence/`.

The helper does not disable auto-approval, delete the Telegram session, replace
Telegram credentials, edit `PROJECT_STATE.json`, contact the recorder or
Johannesburg executor, remove any trading kill switch, or submit an order.

The evidence purpose is:

`phase15-v3-telegram-auto-approver-continuous-contract-upgrade-v1`

## Reviewable auto authorization candidate

`scripts/deploy/phase15_v3_fast_live_build_auto_authorization_candidate.py`

This helper is non-deploying.

It requires the exact operator-upgrade evidence described above and refuses to
overwrite `PROJECT_STATE.json`.

The generated candidate:

- preserves `live_auto_approve_authorized=true`;
- preserves the active operator auto-approver;
- records the new reviewed approval-contract blob;
- records `continuous_candidate_prompt_authorized=true`;
- adds `fast_live_preauthorization` with
  `status=AUTHORIZED_CONTINUOUS_SESSION`;
- selects `auto-telegram-continuous-v1`;
- requires Telegram approval;
- allows one network submission attempt per exact intent;
- preserves the frozen $5 target, $10 trade/exposure/daily-loss limits,
  one-loss limit, 0.075 edge threshold, ZA executor, frozen V3 versions, and
  two-second transit limit;
- keeps global and Phase 15 live flags false;
- records runtime authorization, deployment, activation, kill-switch removal,
  and source-candidate order submission as not performed.

The candidate is passed through the same source authorization verifier used by
the live runtime before it is written.

## Existing execution safeguards remain unchanged

The auto-approved mode does not change the continuous fast-live execution
design already merged in PR #374:

- preview Telegram approval, final risk, and Johannesburg PREPARE run in
  parallel;
- final risk must preserve the exact preview prediction/order/request binding;
- no upward price chasing;
- executable fresh book must satisfy the frozen approved price and size;
- one POST attempt per exact intent;
- durable attempt/result state before and after submission;
- uncertain submission or unresolved fill blocks new trading;
- cancellation and official reconciliation recover after restart;
- settlement-required fills remain blocked until durable settlement completes;
- result replay integrity is HMAC/authenticated and semantic-hash bound;
- result-integrity faults latch persistently and stop new trading;
- ordinary completed trades do not stop the continuous session.

## Activation mode inheritance

`scripts/deploy/phase15_v3_fast_live_activate_cloudshell.sh` reads the reviewed
continuous authorization mode from source truth and carries that exact mode into
the runtime authorization.

The helper accepts only the two reviewed continuous modes:

- `manual-telegram-continuous-v1`;
- `auto-telegram-continuous-v1`.

It does not select or override the mode independently. The runtime verifier still
requires runtime approval mode to equal reviewed source truth exactly, preserving
the fail-closed boundary if either artifact is changed or mismatched.

## Production boundary

Merging this engineering code does not:

- restart the operator auto-approver;
- write operator-upgrade evidence;
- change `PROJECT_STATE.json`;
- create a continuous source authorization;
- create runtime authorization;
- stage either production host;
- start the source or receiver;
- remove the Johannesburg KILL marker;
- submit a real order.

The production sequence, after all engineering and review prerequisites are
complete, is:

1. merge reviewed auto-mode engineering;
2. run the read-only operator compatibility preflight;
3. with separate explicit authorization, restart/upgrade the Mac auto-approver
   and record evidence;
4. generate and review the auto-mode source-truth candidate;
5. merge that source-truth authorization;
6. build and stage the exact release;
7. run read-only fast-live preflight;
8. only after activation tooling can inherit the exact source-truth mode, use a
   separate explicit live-session authorization for activation.
