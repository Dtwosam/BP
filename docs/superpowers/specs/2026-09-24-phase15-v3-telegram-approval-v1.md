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
`0700` directory. The claim path authenticates both the transport HMAC and the separate
origin attestation before consuming the exact order. If materialization fails after the
claim, the claim remains consumed and automatic retry is forbidden.

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
executor/arm/submission code.

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
environment values, has no Linux capabilities, and makes `/etc/bp-canary` inaccessible.
Therefore the receiver cannot read the Polymarket signing key, kill switch, or activation
file and cannot invoke the executor.

The post-receive claim candidate is:

```text
scripts/run_phase15_v3_telegram_transport_claim_worker.py
deploy/bp-phase15-telegram-transport-claim-worker.service
```

It is intentionally a separate offline process. It reads only verified inbox envelopes,
revalidates the transport HMAC/key ID/expiry and the embedded origin attestation using the
configured origin key, atomically consumes the application-level
`(intent_id, request_sha256)` claim, and materializes a `0700` ready directory containing
`0600` prepared, approval, origin-attestation, envelope, and receipt files. It writes a
processed receipt so the same inbox object is not reconsidered.

If materialization or processed-receipt persistence fails after the claim is consumed, the
worker records a terminal no-retry failure. It never recreates the claim or treats a carrier
redelivery as a second authorization. An expired or otherwise invalid inbox envelope is
terminalized without creating a claim. A key-ID mismatch is left pending for deliberate key
rotation rather than being consumed under the wrong key.

The claim worker also runs as `bp-transport`, has no network address family beyond
`AF_UNIX`, cannot access `/etc/bp-canary`, and contains no arm, executor, order, wallet, or
Cloud API path. A ready artifact is therefore still only authenticated pre-execution material;
it is not permission to submit money.

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
