# Phase 15 — V3 Telegram Approval v1

**Date:** 24 September 2026  
**Status:** engineering candidate; listener and handoff code only; not deployed; no new live authorization

## Current source-truth boundary

The first frozen-V3 real-money canary has already consumed the single authorized network
submission attempt and has been reconciled as zero fill. Current source truth keeps global live
trading disabled and does not authorize a second order.

This Telegram work does not change that boundary. In particular, it does not set or imply:

- `second_order_authorized = true`;
- `automated_real_money_submission = true`;
- `telegram_one_tap_submission_authorized = true`;
- `telegram_persistent_execution_transport_authorized = true`.

Missing Telegram authorization fields are interpreted as false by the readiness gate.

## Objective

Replace terminal/chat latency in a future separately authorized live action with one exact,
short-lived Telegram approval:

```text
new frozen-V3 candidate
-> existing prepare/risk path
-> private Telegram notification
-> APPROVE or SKIP
-> exact intent/request binding revalidated
-> separately authorized persistent execution transport
-> existing arm/executor/record semantics
```

The approval must never approve a later candidate accidentally.

## Approval binding

Every pending Telegram approval is bound to:

- `intent_id`;
- `prediction_id`;
- `paper_order_id`;
- SHA-256 of the exact prepared request;
- one random callback nonce;
- one Telegram user ID;
- one private Telegram chat ID;
- a short expiry no later than the market safety floor.

Wrong user, wrong chat, wrong nonce, request mutation, identity mutation, expiry, SKIP, stale
prepared state, or a second/replayed attempt fails closed.

## Persistent listener

The listener is designed to run on `bp-recorder` as
`bp-phase15-canary-telegram-approval.service`.

The service is explicitly research/zero-money:

```text
MODE=research
LIVE_TRADING_ENABLED=false
MAX_TRADE_SIZE_USD=0
MAX_DAILY_LOSS_USD=0
BP_TELEGRAM_HANDOFF_ENABLED=no
```

It unsets Polymarket key/wallet variables, reads the prepare-watch state read-only, and writes
only its own approval state.

The listener installer is:

```text
scripts/deploy/phase15_v3_telegram_approval_install_cloudshell.sh
```

It requires explicit install authorization through
`PHASE15_ACCEPT_TELEGRAM_APPROVAL_INSTALL=yes`, a clean checkout exactly at current
`origin/main`, and an authenticated Cloud Shell session.

The bot token is entered at a hidden `/dev/tty` prompt. It is never committed to Git and is
copied to `/etc/bp/telegram-approval.env` as `root:bp 0640`. The installer validates the bot
and private chat through Telegram before upload, installs no handoff command, preserves core
service PIDs, and rolls back the unit/env/current symlink on install failure.

The read-only status verifier is:

```text
scripts/deploy/phase15_v3_telegram_approval_status_cloudshell.sh
```

It reports service/binding/permission health without printing the bot token or Telegram IDs.

The emergency listener/token disable helper is:

```text
scripts/deploy/phase15_v3_telegram_approval_disable_cloudshell.sh
```

It requires explicit disable authorization, stops/disables only the Telegram listener, removes
the bot-token environment file, preserves per-intent approval audit state, and verifies that
the recorder, V3 predictor, and V3 paper executor PIDs did not change.

## Handoff state machine

An approved intent is revalidated immediately before handoff and copied to an immutable
per-intent `handoff-prepared.json` snapshot.

Before any configured handoff command starts, the listener persists `handoff-attempt.json`.
After that marker exists, a missing result, crash, timeout, restart, or ambiguous outcome is
terminal and must never be retried automatically.

The child handoff environment removes:

- `BP_TELEGRAM_BOT_TOKEN`;
- `POLYMARKET_PRIVATE_KEY`;
- `POLYMARKET_WALLET_ADDRESS`.

An approval received while handoff is unconfigured or invalid is also terminal. Turning on
handoff later cannot resurrect an old approval.

## Existing arm/submission bridge

`scripts/deploy/phase15_v3_canary_telegram_handoff.sh` binds the approved snapshot to the
existing Phase 15 arm, Johannesburg executor, and record semantics.

The bridge additionally requires a future source-truth flag:

```text
telegram_one_tap_submission_authorized = true
```

Current source truth does not contain that authorization, so the bridge fails before arming.

The bridge is intentionally not configured in the listener service or installer.

## Carrier-independent transport protocol

Before selecting a network carrier, BP defines the execution transport payload independently.

`src/bp_engine/execution/telegram_transport.py` creates a canonical JSON envelope containing
the exact prepared order and only the execution-relevant approval fields. Telegram user ID,
chat ID, and callback query ID remain on `bp-recorder`; they are not sent to the execution
host. A SHA-256 of the fuller local approval record is carried only as an audit link.

The envelope uses a dedicated 256-bit HMAC-SHA256 transport key that is separate from both the
Telegram bot token and the Polymarket signing key. The adapters load this secret only from a
regular, non-symlink `0600` or `0640` key file; the raw key is not accepted through adapter
environment. Every envelope also carries an explicit bounded `key_id`, and intake must be
configured for that exact key ID before HMAC verification. This supports deliberate key
rotation without "try every key" behavior.

The envelope binds:

- exact transport key ID;
- exact intent, prediction, and paper-order identities;
- exact request SHA-256;
- exact prepared payload SHA-256;
- execution-relevant approval payload SHA-256;
- local full-approval audit SHA-256;
- one transport nonce;
- creation and expiry timestamps;
- a fixed Phase 15 transport purpose/version.

Transport lifetime is capped at 15 seconds and cannot outlive either Telegram approval or the
existing market-end submission safety floor.

Replay identity is `(intent_id, request_sha256)`, not the transport nonce. An attacker or
bug cannot create a second executable claim for the same exact order merely by changing the
envelope nonce. Claim state is persisted with exclusive creation and `retry_allowed=false`.

Origin authentication is independent of transport authentication. The transport envelope
carries the approval-origin attestation, but the `bp-transport` receiver and claim worker do
not receive the origin key. They may authenticate transport delivery and consume the one-shot
transport claim, but the resulting `claimed_ready` bundle is still not execution-authorized.

The separate read-only boundary

```text
scripts/run_phase15_v3_telegram_execution_ready_verify.py
```

holds the origin-key trust domain. It requires the protected origin key file and exact origin
key ID, verifies the origin-attestation HMAC against the materialized prepared/approval
payloads, and cross-checks the ready receipt and envelope. Only a bundle that passes this step
can be labelled `execution_ready_origin_verified`.

This split is deliberate: a transport-key compromise alone cannot create an origin-verified
execution-ready artifact, while the transport service never receives the origin secret. A
forged or wrong-key origin proof fails at the separate pre-execution verifier. The verifier is
read-only and contains no arm, executor, wallet, network, or order-submission path.


After origin verification, BP applies a second read-only source-truth gate:

```text
scripts/run_phase15_v3_telegram_pre_execution_gate.py
```

It re-verifies the ready bundle, hashes the supplied `PROJECT_STATE.json`, and requires all
relevant policy decisions to be explicit before it can report
`pre_execution_authorized`: the first canary must be reconciled, no pending intent may
exist, second-order authorization must be true, automated real-money submission must be
authorized, manual-only submission must be lifted, and the Telegram one-tap, persistent
transport, and Pub/Sub transport authorizations must all be true.

The current source truth does not satisfy those requirements, so the gate reports
`pre_execution_blocked`. The gate never arms, signs, submits, cancels, mutates source truth,
or calls a network service; even its synthetic authorized result is only an authorization
report for a later separately reviewed execution boundary.


The authorization report binds the transport key ID, origin key ID, exact intent/order
identities, request/prepared/approval hashes, origin-attestation hash and lifetime, plus a
SHA-256 of the complete source-truth document. The report itself is then canonically hashed as
`authorization_report_sha256`.

That report is not durable permission. Immediately before any future execution boundary may
use it, BP must call the pure `verify_pre_execution_snapshot(..., require_authorized=True)`
revalidator with the current origin-verified ready result and current source truth. Any source
truth change, ready-bundle change, extra/modified report field, or blocked authorization makes
the snapshot stale and fails closed. This closes the authorization-check-to-execution TOCTOU
gap without giving the pre-execution layer any execution capability.

The offline dispatch layer is:

```text
src/bp_engine/execution/telegram_dispatch_ticket.py
scripts/run_phase15_v3_telegram_dispatch_ticket.py
```

A dispatch ticket can be created only from a fully authorized pre-execution report. It binds
the transport key ID, origin key ID, exact intent/order identities, request/prepared/approval
hashes, origin-attestation hash/lifetime, source-truth hash, and
`authorization_report_sha256`. The ticket itself performs no mutation, network action, arm,
wallet access, or submission and expires no later than the origin authorization.

The ticket is still not durable bearer permission. Before a dispatch claim is consumed, BP
re-runs `verify_pre_execution_snapshot(..., require_authorized=True)` against the **current**
origin-verified ready result and **current** source truth, reconstructs the expected ticket,
and requires exact equality. Any source-truth drift, ready-bundle drift, report mutation, ticket
mutation, blocker, or expiry fails before the claim directory is created.

The dispatch claim is one-shot for the exact `(intent_id, request_sha256)` pair and is written
with exclusive-create semantics. A duplicate claim fails closed. The claim preserves the
dispatch-ticket expiry rather than converting short-lived authorization into durable
permission. The claim record keeps `retry_allowed=false`, `executor_invoked=false`, and
`real_order_submitted=false`. This layer deliberately stops before any arm/executor boundary.

The existing engineering-only Telegram handoff refuses to proceed without that dispatch claim.
Before its existing arm boundary it requires the complete future source-truth authorization
set, exact dispatch-claim fields, the current source-truth hash, exact
prepared/approval/request binding, and an unexpired dispatch claim. It snapshots the dispatch
claim bytes and checks them again after arm before any existing submission boundary. Current
source truth does not satisfy those authorizations, and the listener service does not
configure the handoff, so this remains inert engineering code rather than an enabled execution
path.

The CLI requires explicit enablement plus research/live-disabled/zero-money runtime and rejects
wallet, Telegram-bot, or Google application credentials in its environment. Current source
truth remains blocked, so it cannot produce a valid current-state dispatch claim for another
live order.

The carrierless adapters are:

```text
scripts/run_phase15_v3_telegram_transport_outbox.py
scripts/run_phase15_v3_telegram_transport_intake.py
```

The outbox requires an explicit enable flag, research/zero-money runtime, the dedicated
protected transport-key file plus exact key ID, and absence of Telegram/trading secrets in its
child environment. It writes
exactly one local envelope per exact order and performs no network send.

The intake requires a separate explicit enable flag, the protected transport-key file, and the
same exact key ID. It verifies the HMAC, expiry, exact prepared/approval binding, claims the
exact order once, and materializes
the prepared/approval receipt under a hashed claim path. It does not invoke the executor or
submit an order.

These adapters allow the full `APPROVE -> envelope -> verify -> claim` semantics to be tested
locally before any carrier or cloud IAM change exists.

## Authenticated transport envelope boundary

The engineering branch now defines the payload contract that a future persistent transport
must carry, without defining or enabling the network transport itself.

`src/bp_engine/execution/telegram_transport.py` creates a short-lived HMAC-SHA256 envelope
bound to the exact approved prepared order. The envelope includes the prepared payload,
sanitized approval fields, exact identity/request hashes, a transport nonce, creation/expiry
timestamps, and a purpose/schema version. Telegram user/chat IDs and callback-query IDs are
not forwarded to the execution side.

The transport key must be exactly 32 bytes encoded as base64url and may be loaded only from a
regular non-symlink file with mode `0600` or `0640`. It is not accepted through a command
line argument or environment variable by the boundary runners.

The recorder-side approved staging boundary is:

```text
scripts/run_phase15_v3_telegram_approved_outbox.py
```

It is the safe future handoff target for the Telegram listener. The listener already injects
the exact approved `BP_APPROVED_INTENT_ID` and `BP_APPROVED_REQUEST_SHA256`; the staging
command requires those values to match the prepared request before it writes anything. It
uses a dedicated origin-attestation key and a separate transport key, rejects identical key
material or key IDs, creates the origin proof, embeds that proof into the transport-HMAC-bound
envelope, and writes the immutable envelope into the local outbox with create-exclusive
`0600` semantics. It performs no Pub/Sub call, arm operation, executor invocation, or order
submission.

The lower-level producer boundary remains:

```text
scripts/run_phase15_v3_telegram_transport_pack.py
```

It requires an already-created origin attestation plus the approved prepared/approval files
and restricted transport key. It creates a new `0600` envelope file with create-exclusive
semantics and performs no network action.

The receiver boundary is:

```text
scripts/run_phase15_v3_telegram_transport_claim.py
```

It verifies the exact schema, HMAC, hashes, approval validity, transport lifetime, and request
binding; then atomically claims the exact `intent_id + request_sha256` in a `0700` claim
state directory. A different transport nonce cannot make the same order claimable twice.

After claim, the receiver materializes `prepared.json`, sanitized `approval.json`,
`origin-attestation.json`, `envelope.json`, and `receipt.json` as `0600` files under a
`0700` directory. The claim path authenticates the transport HMAC and exact embedded
origin-attestation binding before consuming the exact order, but it does not receive the
origin HMAC key. The separate execution-ready verifier authenticates that origin HMAC before
the bundle can be labelled `execution_ready_origin_verified`. If materialization fails after
the claim, the claim remains consumed and automatic retry is forbidden.

The read-only execution-side verifier is:

```text
scripts/run_phase15_v3_telegram_execution_ready_verify.py
```

It re-verifies the origin attestation against the exact ready prepared/approval payload,
checks the claim receipt and envelope hashes/identities, and keeps the transport key ID and
origin key ID distinct. It has no network, wallet, arm, cancellation, or order-submission
path. Passing this verifier is still not authorization to execute.

These two runners contain no HTTP client, `gcloud`, Polymarket SDK/order call, wallet key, arm
operation, or submission operation. A future authorized transport may carry the envelope
between them, but that network mechanism remains a separate gate.

## Persistent execution transport is still a separate gate

Existing BP live control uses authenticated Cloud Shell `gcloud compute ssh` commands. The
repository does not currently define an approved persistent control channel from
`bp-recorder` to `bp-v3-canary-exec`.

The listener therefore must not silently depend on Cloud Shell credentials, user home files,
or an undeclared host-to-host tunnel.

Before one-tap execution can be enabled, a separate persistent carrier design must be
reviewed and explicitly authorized. Any accepted carrier must move the authenticated envelope
without rewriting it, preserve its short expiry, authenticate both endpoints using dedicated
least-privilege credentials, provide durable delivery/audit evidence, and preserve the
one-order no-retry semantics. Carrier retries may redeliver bytes, but executor claim logic
must reject replay before any arm/submission action.

### Pub/Sub carrier candidate

The engineering candidate now includes a transport-only Google Cloud Pub/Sub carrier:

```text
scripts/run_phase15_v3_telegram_pubsub_publish.py
scripts/run_phase15_v3_telegram_pubsub_receive.py
src/bp_engine/execution/telegram_pubsub.py
```

The publisher runs only against a prebuilt, locally reverified authenticated envelope. It
obtains a short-lived OAuth bearer token from the Compute Engine metadata server, publishes
the exact authenticated envelope content plus routing attributes to one configured topic, and
records a local publish receipt. Metadata-token requests are explicitly downscoped to the
Pub/Sub OAuth scope, require the Google metadata response flavor, and use HTTP clients with
environment proxy inheritance disabled. It has no wallet/signing material and no
executor/arm/submission code. Its systemd sandbox exposes only the transport key/config needed
for publishing and explicitly makes the independent `origin.key` inaccessible.

The unary REST receiver in
`scripts/run_phase15_v3_telegram_pubsub_receive.py` remains a diagnostic/contract adapter.
It is not the low-latency production candidate because the transport envelope is short-lived
and continuous subscription delivery should use StreamingPull through the high-level client.

The low-latency receiver candidate is:

```text
scripts/run_phase15_v3_telegram_pubsub_streaming_receive.py
deploy/bp-phase15-telegram-pubsub-streaming-receiver.service
```

It uses `google-cloud-pubsub` StreamingPull with one outstanding message at a time. The
callback validates routing attributes, HMAC, exact key ID, approval/request binding, and
expiry. A valid envelope is durably persisted before ACK. An exact duplicate is revalidated
and ACKed without creating another authorization. A malformed, tampered, or expired message
is durably recorded as rejected before ACK so it cannot become a poison-message loop. Local
key/config or durable-storage failures NACK so a legitimate message can survive host repair.

The candidate service runs as a dedicated `bp-transport` account, keeps research/zero-money
environment values, has no Linux capabilities, and makes `/etc/bp-canary` plus the
independent `origin.key` inaccessible. It can read only its receiver config and transport
key from the transport config directory. Therefore the receiver cannot read the Polymarket
signing key, approval-origin secret, kill switch, or activation file and cannot invoke the
executor.

The post-receive claim candidate is:

```text
scripts/run_phase15_v3_telegram_transport_claim_worker.py
deploy/bp-phase15-telegram-transport-claim-worker.service
```

It is intentionally a separate offline process. It reads only verified inbox envelopes,
revalidates the transport HMAC/key ID/expiry and exact embedded origin-attestation
binding, atomically consumes the application-level `(intent_id, request_sha256)` claim,
and materializes a `0700` ready directory containing `0600` prepared, approval,
origin-attestation, envelope, and receipt files. It does **not** receive the protected
origin secret and therefore does not authenticate the origin-attestation HMAC at this
transport stage. That independent HMAC check remains exclusively in the separate
`execution_ready_origin_verified` boundary described above. It writes a processed receipt
so the same inbox object is not reconsidered.

If materialization or processed-receipt persistence fails after the claim is consumed, the
worker records a terminal no-retry failure. It never recreates the claim or treats a carrier
redelivery as a second authorization. An expired or otherwise invalid inbox envelope is
terminalized without creating a claim. A key-ID mismatch is left pending for deliberate key
rotation rather than being consumed under the wrong key.

The claim worker also runs as `bp-transport`, has no network address family beyond
`AF_UNIX`, cannot access `/etc/bp-canary` or `origin.key`, and can read only its claim
config plus transport key. It contains no arm, executor, order, wallet, or Cloud API path. A
ready artifact is therefore still only authenticated pre-execution material; it is not
permission to submit money.

The intended IAM boundary is resource-level least privilege:

- the recorder-side service account may publish only to the dedicated Telegram transport topic;
- the execution-host transport service account may consume only the dedicated subscription;
- neither role grants topic/subscription administration;
- no long-lived Google service-account JSON key is stored by BP;
- no inbound listener, SSH tunnel, VPN, proxy, or public execution endpoint is introduced.

Pub/Sub is at-least-once delivery. That is acceptable only because BP's application-level
execution claim remains keyed to `(intent_id, request_sha256)`. A lost publish response may
cause the same immutable envelope to be published again; a lost acknowledgement may cause the
same message to be delivered again. Both are transport duplicates, not authorization to retry
an order.

A same-order inbox collision with different authenticated envelope bytes fails closed and is
not overwritten. Network delivery success alone never arms or submits anything.

No Pub/Sub topic, subscription, IAM role, VM service-account change, receiver service
installation, service enablement, or production configuration has been performed by this
branch. A hardened systemd unit definition exists only as an engineering artifact.

The readiness gate treats both

```text
telegram_persistent_execution_transport_authorized
telegram_pubsub_transport_authorized
```

as false unless source truth explicitly sets them true.

The read-only readiness helper is:

```text
scripts/deploy/phase15_v3_telegram_activation_readiness_cloudshell.sh
```

It checks source-truth authorization, listener health, and the Johannesburg executor's safe
idle state. It performs no arm, kill-switch removal, order submission, cancellation, or state
mutation.

### Deterministic transport release boundary

The engineering candidate also includes a secret-free release builder and independent
verifier:

```text
scripts/deploy/phase15_v3_telegram_transport_build_release.py
scripts/deploy/phase15_v3_telegram_transport_verify_release.py
```

The builder accepts only an exact source-file whitelist covering the transport services,
workers, and trust-chain modules. It requires a clean Git working tree, binds the release to
the exact commit SHA, normalizes archive ownership/mode/timestamps, and emits a manifest with
the SHA-256 and size of every member. Rebuilding the same commit must produce byte-identical
archive bytes. The recorder-side Pub/Sub publisher and Johannesburg receiver/claim worker all
execute from the same versioned `/opt/bp-telegram-transport/current` release tree, keeping the
carrier independently deployable and rollbackable from the Telegram approval listener.

The release archive deliberately contains no `PROJECT_STATE.json`, `.env` files, HMAC key
files, service-account credentials, wallet material, or other runtime secrets. The independent
verifier refuses extra or duplicate paths, links, path traversal, changed bytes, metadata
drift, manifest tampering, a wrong expected commit, or secret-bearing file types. Verification
does not extract the archive to the host and performs no network or production mutation.

This is a packaging boundary only. It does not install either service, create a Pub/Sub
resource, alter IAM, provision a key, enable a unit, arm the executor, or authorize a live
order.

### Read-only transport install preflight

The engineering candidate also includes separate Cloud Shell install preflights for both
ends of the carrier:

```text
scripts/deploy/phase15_v3_telegram_transport_publisher_install_preflight_cloudshell.sh
scripts/deploy/phase15_v3_telegram_transport_install_preflight_cloudshell.sh
```

They are not installers. Before any production mutation, both require a clean checkout exactly
at current `origin/main`, verify the same deterministic transport release against that exact
commit, and prove from current source truth that global/Phase-15 live trading remains off,
the first canary has already been submitted, no second order is authorized, automated
submission is off, manual submission is still required, and all Telegram execution-transport
authorization flags remain false.

The recorder-side publisher preflight then read-only verifies the `bp-recorder` VM identity,
service-account shape/cloud-platform scope, active core research services and stable PIDs,
Python/systemd availability, minimum root free space, absence of an existing transport
publisher install, and absence of the canary wallet path on the US host. The independent
Telegram approval sidecar may exist and is not treated as the transport release root.

The Johannesburg preflight read-only verifies the executor VM identity/zone, service-account
shape and cloud-platform scope, Python/systemd availability, minimum root free space, absence
of an existing receiver/claim transport install, presence of the existing executor and kill
switch, and an executor `health` response showing safe idle state: kill switch engaged, no
valid activation, submission not ready, no live order submitted, and direct ZA geoblock
eligibility.

Both preflights perform no `scp`, package installation, user/group creation, archive
extraction, filesystem mutation, IAM change, Pub/Sub creation, systemd start/enable/restart,
arm, cancellation, or order submission. PASS means only that the verified release and host
shape are suitable for a separately authorized install review.

### Transactional transport staging lifecycle

The next engineering boundary is an explicitly authorized **stage-only** installer:

```text
scripts/deploy/phase15_v3_telegram_transport_stage_install_cloudshell.sh
```

It requires `PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_INSTALL=yes`, a clean checkout exactly
at current `origin/main`, the exact verified transport release, and PASS from both read-only
host preflights before the first archive copy. This authorization is only permission to stage
software/runtime files; it is not transport activation or live-order authorization.

The stage installer copies the same archive to both hosts, verifies its SHA-256 again remotely,
creates a dedicated transport virtual environment from the release-carried direct dependency
pins using binary wheels only, verifies `httpx==0.28.1` and
`google-cloud-pubsub==2.41.0`, installs only the appropriate systemd unit files, and creates
the transport state directories. The recorder publisher remains owned by `bp`; the
Johannesburg receiver/claim services use the dedicated `bp-transport` system account.

Before each host's first stage mutation, the installer writes a durable stage-owner record
binding the random stage ID, role, release head, archive SHA-256, and any planned
`bp-transport` user/group creation. Final stage metadata records `stage_complete=true`.
This ownership record lets local rollback safely identify partial work even if SSH is lost
mid-stage. A two-host failure rolls back any already-completed side using that exact stage
identity.

Staging deliberately creates **no** publisher/receiver/claim environment files, transport
key, origin key, Pub/Sub topic/subscription, IAM binding, activation, wallet material, or
handoff configuration. It performs `systemctl daemon-reload` only; all transport services
must remain disabled and inactive. Recorder/predictor/paper PIDs are preserved, and the
Johannesburg executor must remain safe-idle with the kill switch engaged before and after
staging.

The stage can be inspected read-only with:

```text
scripts/deploy/phase15_v3_telegram_transport_stage_status_cloudshell.sh
```

That verifier requires both hosts to bind to one stage ID and archive SHA, the staged release
head to equal current `main`, installed unit hashes to match release bytes, the pinned direct
runtime versions to match, all transport services to remain inactive/disabled, all runtime
env/key files to remain absent, recorder core services to remain healthy, current source
truth to remain no-second-order/live-disabled, and the Johannesburg executor to remain
safe-idle and directly eligible in ZA. It performs no mutation.

A never-activated stage can be removed with:

```text
scripts/deploy/phase15_v3_telegram_transport_stage_rollback_cloudshell.sh
```

Rollback requires `PHASE15_ACCEPT_TELEGRAM_TRANSPORT_STAGE_ROLLBACK=yes` plus the exact
`PHASE15_TELEGRAM_TRANSPORT_STAGE_ID`. It probes both hosts before deletion and refuses if
any transport service is active/enabled or if any publisher/receiver/claim env file,
transport key, or origin key exists. It revalidates owner+metadata immediately before removal,
preserves recorder core PIDs and Johannesburg safe-idle health, and can finish a prior partial
rollback by treating an already-clean side as absent. It does not remove keys/env because
their presence makes this stage-only rollback fail closed.

None of these staging helpers has been run against production by this branch.

### Approval-to-authenticated-outbox adapter

The transport release also carries a small executable handoff adapter:

```text
deploy/phase15-telegram-approved-outbox-handoff.sh
```

The recorder stage installer places it at
`/opt/bp-telegram-transport/bin/approved-outbox-handoff` as `root:bp 0750`, and the
read-only stage-status verifier binds its installed SHA-256 to the exact release bytes. The
adapter can only invoke the release-pinned Python approved-outbox producer. It contains no
Cloud API, Pub/Sub publish, wallet, arm, executor, cancellation, or order-submission path.

The listener unit keeps `BP_TELEGRAM_HANDOFF_ENABLED=no` as its built-in default and may
read a separate optional `/etc/bp/telegram-approval-handoff.env`. Normal listener install
requires that optional file to be absent, listener status fails if it appears unexpectedly,
and emergency listener disable removes it. A future separately reviewed configuration can
therefore connect one exact APPROVE callback to the local authenticated outbox without
putting handoff settings in the bot-token environment file. That future file must provide
separate origin/transport key paths and IDs plus the staged adapter path. No such file or key
is created by the current branch.

### Read-only transport activation plan

The remaining carrier configuration can be inspected without applying it:

```text
scripts/deploy/phase15_v3_telegram_transport_activation_plan_cloudshell.sh
```

The planner requires a clean checkout exactly at current `origin/main`, reads current
source truth, and performs only read-only Google Cloud describes/IAM reads. It reuses the
established topic/subscription IDs, resolves the actual recorder publisher and Johannesburg
subscriber service accounts, checks for dedicated identities and `cloud-platform` scope,
reports whether the resource-scoped publisher/subscriber IAM bindings already exist, and
flags broad project-level roles.

The plan outputs the exact future publisher, receiver, claim-worker, and listener-handoff
environment-file shapes; key-file paths, ownership and modes; required resource-scoped IAM;
service activation order; and source-truth values that would have to be explicitly authorized.
It never generates or emits key material, writes environment files, changes IAM, creates
Pub/Sub resources, starts/enables services, invokes the executor, or submits an order.

The plan is deliberately hard-blocked by
`persistent_execution_authorization_consumer_not_defined`. The carrier can deliver and
claim a verified ready bundle, but BP does not yet have a persistent Johannesburg service
that owns the final chain:

```text
origin-HMAC verification
-> fresh PROJECT_STATE authorization evaluation
-> one-shot dispatch ticket creation/claim
-> exact prepared/approval/dispatch binding
-> Johannesburg executor handoff
```

That missing consumer must be designed and reviewed before transport activation can be
considered complete. The planner always reports
`TELEGRAM_TRANSPORT_ACTIVATION_PERMITTED=false`.

## Safety invariants

- no second order without new explicit source-truth authorization;
- no bot token, wallet key, or wallet address in Git or chat;
- private Telegram chat only;
- exact intent/request binding only;
- approval expiry enforced at callback and handoff;
- no approval reuse after disabled/invalid handoff;
- attempt marker before any handoff command;
- no automatic retry after attempted/ambiguous handoff;
- listener remains research/zero-money even when persistent;
- no Polymarket signing material on `bp-recorder`;
- no VPN/proxy/tunnel geographic circumvention;
- Johannesburg executor retains its independent geoblock/account/activation/request checks;
- current deployment configuration contains no execution handoff.

## Production status

No Telegram listener, handoff command, transport, source-truth authorization, or new
real-money order has been deployed by this engineering branch.
