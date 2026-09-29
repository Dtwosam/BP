# BP Telegram auto-approver

Operator-side watcher for the existing Phase 15 private approval prompt. It replaces the physical `APPROVE` button press and nothing else. The production listener still validates the callback, writes the approval record, and runs the existing handoff only when that handoff is already enabled.

This service is not part of the trading strategy. It does not change V3, V4, sizing, thresholds, the prepare watcher, the Telegram listener, transport, pre-execution checks, or the executor. Live mode is not authorized by adding this code.

## What it clicks

The listener in `scripts/run_phase15_v3_canary_telegram_approval.py` sends one private message from the BP bot. A finalized prompt is produced by `build_prompt`; a continuous fast-live preview is produced by the separately reviewed `build_candidate_prompt`. The keyboard is one row:

- `APPROVE` with callback data `approve:<nonce>`
- `SKIP` with callback data `skip:<nonce>`

The nonce is the 16-character value created by `secrets.token_urlsafe(12)`. Intent ID, prediction ID, and `request_sha256` are not in the message. They stay in the listener's pending record. This service logs those fields as absent, stores the nonce, and later records the intent ID only if the listener edits that same message to `BP V3 trade APPROVED`.

A click is the MTProto call `messages.getBotCallbackAnswer`, which is what the official Telegram app sends for an inline callback button. The Bot API cannot press another bot's button. See https://core.telegram.org/method/messages.getBotCallbackAnswer and https://core.telegram.org/bots/faq.

The service calls that method only when every check below passes:

- startup resolved `@configured_bot` to the configured numeric bot user ID, and that account is a bot
- the logged-in user is not a bot and its ID is `BP_TELEGRAM_OPERATOR_USER_ID` (the same human ID the listener stores as `BP_TELEGRAM_USER_ID`)
- the message is incoming, private, not forwarded, not a reply, and not edited
- the message date is strictly after this process started
- the text exactly matches either the reviewed `build_prompt` grammar or the reviewed continuous `build_candidate_prompt` grammar, including `Maximum spend: $5` and side `UP` or `DOWN`
- a `LIVE TRADE CANDIDATE` click is approval only; it does not bypass final live risk, Johannesburg preparation, fresh-book, frozen-price, or reconciliation gates
- displayed time remaining is at least 20.0 seconds
- `now` is still before `message_date + min(45s, time_remaining - 10s) - 2s`

The 2-second subtraction is a local safety margin. The listener stamps `expires_at` before `sendMessage`, while this client only sees Telegram's message date. A prompt that is still inside the shorter deadline is clicked immediately.

Before Telegram connects, and again before a message is processed, the service hashes `src/bp_engine/execution/telegram_approval.py` with the git blob algorithm. The process exits with `APPROVAL_CONTRACT_MISMATCH` unless the blob is `5676efcb60840f4533a7f43b3c6a7efab9e97541`. This reviewed blob contains both finalized and continuous-candidate prompt builders. A later edit of that file does not become accepted until this pin is reviewed and updated.
- the keyboard is exactly `APPROVE` then `SKIP`, sharing one 16-character nonce

Any other shape is ignored. `SKIP`, URL buttons, and every other callback are never sent.

For continuous auto-approved fast-live, source truth must explicitly select `auto-telegram-continuous-v1`, keep this operator auto-approver active/authorized, and bind it to the same reviewed approval-contract blob. Merely running this service does not authorize a continuous live session.

## Replay protection

SQLite is the durable state. The file lives outside the git checkout.

Before a live click, the service inserts a `reserved` row keyed by `(chat id, message id)` and by `nonce`, and commits that insert. It then sends the callback.

- `would_approve`: dry-run recorded the prompt and did not click
- `reserved`: a click was about to start
- `clicked`: Telegram returned the callback RPC
- `failed_or_unknown`: the RPC timed out, returned nothing, raised, or the process restarted while the row was still `reserved`
- `rejected`: a well-formed prompt was already expired

A second message, a replay, or a restart cannot move a row back to `reserved`. There is no automatic retry. Missing one prompt is preferred over sending the same callback twice.

On startup, leftover `reserved` rows become `failed_or_unknown` with reason `startup_found_unconfirmed_reservation` before any message is handled.

Messages dated at or before process start are ignored and are not written to the database. New messages are handled as Telegram delivers them. While the client is connected, the service also re-reads only the single latest message in the bound bot chat, including after a disconnect that was too short for the connection poll to observe. A message already recorded in SQLite is not clicked again, and a failed callback is not retried. Older history is not read. An expired prompt is recorded as `rejected` and is not clicked.

An edited `BP V3 trade APPROVED / Intent:` message updates the existing row's intent ID. It never creates a row and never clicks.

## Disconnects

Telethon is started with automatic reconnects. A dropped connection that the poll observes logs `TELEGRAM_DISCONNECTED`, and the following connected sample logs `TELEGRAM_RECONNECTED` and checks the latest message again. The same one-message check also runs on each connected poll, so a prompt that arrives during a disconnect the poll never sees is still eligible once it is the latest message. `SIGINT` and `SIGTERM` stop the loop and disconnect.

`APPROVAL_TRIGGERED` means the callback RPC returned. It does not by itself prove the listener wrote `approval.json`. The later edited message is that observation. A timeout is `APPROVAL_FAILED_OR_UNKNOWN` even if the listener may already have received the click.

## Telegram developer application

1. Log in at https://my.telegram.org as the human account that receives the BP approval prompts.
2. Open API development tools and create an application.
3. Put the numeric API ID and the API hash in a private environment file outside this repository.
4. Do not put a bot token in this service. The listener keeps the bot token. This process logs in as the user.
5. Set `BP_TELEGRAM_OPERATOR_USER_ID` to that human account's numeric ID. It must match the listener's `BP_TELEGRAM_USER_ID`.
6. Set `BP_TELEGRAM_EXPECTED_BOT_USERNAME` and `BP_TELEGRAM_EXPECTED_BOT_USER_ID` to the BP approval bot. From this user account, that bot is the private chat. Do not reuse the listener's `BP_TELEGRAM_CHAT_ID` here: that value is the human ID as seen by the bot.

The first foreground run in a terminal can ask Telegram for a login code. A non-interactive service refuses to start until that session file already exists. The session file and SQLite file must be absolute paths outside the repository. File mode is restricted to the current user.

## Commands

Create the private directory first, then a virtualenv:

```bash
mkdir -m 700 /absolute/path/outside/repo
python3 -m venv /absolute/path/outside/repo/venv
/absolute/path/outside/repo/venv/bin/pip install -r ops/telegram_auto_approver/requirements.txt
```

Dry-run, from the repository root. Leave `BP_TELEGRAM_AUTO_APPROVE` unset:

```bash
set -a
source /absolute/path/outside/repo/telegram-auto-approver.env
set +a
export PYTHONPATH="ops/telegram_auto_approver:src"
/absolute/path/outside/repo/venv/bin/python -m bp_telegram_auto_approver
```

Check the configuration without connecting:

```bash
PYTHONPATH="ops/telegram_auto_approver:src" \
  /absolute/path/outside/repo/venv/bin/python -m bp_telegram_auto_approver --check-config
```

Live mode, for a later explicit decision only. This branch does not authorize running it:

```bash
BP_TELEGRAM_AUTO_APPROVE=true \
  PYTHONPATH="ops/telegram_auto_approver:src" \
  /absolute/path/outside/repo/venv/bin/python -m bp_telegram_auto_approver
```

Only the raw environment value `true` clicks. `True`, `TRUE`, `1`, `yes`, and any value with surrounding spaces stay in dry-run. The comparison does not trim the value.

The Linux example is a user systemd unit: `deploy/bp-telegram-auto-approver.service`. Install it under `~/.config/systemd/user/`, edit the `%h` paths if the checkout or virtualenv is elsewhere, and start it with `systemctl --user`. Do not install it as a system service and do not run it as root. The Telegram session is a full user credential and must remain mode `0600`, owned by that operator account. The unit does not set `BP_TELEGRAM_AUTO_APPROVE`. The macOS plist is a user agent for `~/Library/LaunchAgents`, not a system daemon. Do not load either file as part of this change.

## Tests

```bash
PYTHONPATH="ops/telegram_auto_approver:src" python -m pytest tests/telegram_auto_approver
```

The tests load `src/bp_engine/execution/telegram_approval.py` directly and mock the click. They do not contact Telegram.
