# Phase 14 Recorder Backpressure Reliability Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Repair the research recorder so bounded database writer concurrency can sustain demonstrated Polymarket bursts without event loss or backpressure incident amplification.

**Architecture:** Keep the existing shared bounded `EventBuffer` and immutable raw-event repository contract, but let `BatchWriter` run a configurable bounded worker pool. Coalesce queue-full events into one backpressure episode start plus one deterministic recovery summary. Do not change V2 feature semantics, reconnect policy, selected-book freshness, trading safety, or production configuration as part of this implementation PR.

**Tech Stack:** Python 3.12, asyncio, SQLAlchemy 2.x, psycopg 3.x, Pydantic 2.x, pytest/pytest-asyncio, Ruff.

**Spec:** `docs/superpowers/specs/2026-09-03-phase-14-recorder-backpressure-reliability-design.md`

## Global Constraints

- `MODE=research`
- `LIVE_TRADING_ENABLED=false`
- `MAX_TRADE_SIZE_USD=0`
- `MAX_DAILY_LOSS_USD=0`
- `automatic_promotion=false`
- Phase 15 remains blocked.
- No live-order or real-money path is added or enabled.
- No geography bypass.
- No V2 model, calibrator, timing, freshness, edge, or `min_edge` policy selection.
- No V2 paper execution.
- No V1 retuning from failed V1 trades.
- Existing selected-book freshness remains exactly 10 seconds.
- No raw events or `market_features` rows are deleted or rewritten.
- `RECORDER_QUEUE_MAXSIZE=50000`, `RECORDER_BATCH_SIZE=500`, and `RECORDER_FLUSH_INTERVAL_SECONDS=0.25` remain unchanged by this implementation.
- `RECORDER_WRITER_WORKERS` defaults to `1`; production selection of a higher value is a later separately authorized rollout decision.

---

### Task 1: Add bounded writer-worker configuration and recorder wiring

**Files:**
- Modify: `src/bp_engine/config.py`
- Modify: `src/bp_engine/recorder/service.py`
- Modify: `tests/test_config.py`
- Modify: `tests/recorder/test_recorder_service.py`
- Modify: `.env.example`
- Modify: `deploy/bp.env.example`
- Modify: `scripts/deploy/bootstrap_ubuntu.sh`

**Interfaces:**
- Produces: `Settings.recorder_writer_workers: int`, environment key `RECORDER_WRITER_WORKERS`, and `BatchWriter(..., worker_count=settings.recorder_writer_workers)` wiring.
- Consumes later: Task 2 implements the `BatchWriter.worker_count` constructor argument.

- [ ] **Step 1: Write failing configuration tests**

Add to `tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError


def test_recorder_writer_workers_default_to_one_and_accept_bounded_override(monkeypatch) -> None:
    assert Settings(_env_file=None).recorder_writer_workers == 1

    monkeypatch.setenv("RECORDER_WRITER_WORKERS", "4")
    assert Settings(_env_file=None).recorder_writer_workers == 4


def test_recorder_writer_workers_reject_non_positive_values() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, recorder_writer_workers=0)
```

Extend `test_recorder_defaults_are_bounded_and_keep_trading_disabled` with:

```python
assert settings.recorder_writer_workers == 1
```

- [ ] **Step 2: Run config RED**

Run:

```bash
pytest tests/test_config.py -q
```

Expected: FAIL because `recorder_writer_workers` does not exist.

- [ ] **Step 3: Implement the setting**

In `src/bp_engine/config.py`, import `Field` and add the field beside the existing recorder settings:

```python
from pydantic import Field

# ...
recorder_queue_maxsize: int = 50_000
recorder_batch_size: int = 500
recorder_flush_interval_seconds: float = 0.25
recorder_writer_workers: int = Field(default=1, ge=1)
```

- [ ] **Step 4: Add environment examples and bootstrap default**

Add this exact line after `RECORDER_FLUSH_INTERVAL_SECONDS=0.25` in `.env.example`, `deploy/bp.env.example`, and the new-environment heredoc in `scripts/deploy/bootstrap_ubuntu.sh`:

```text
RECORDER_WRITER_WORKERS=1
```

Also add this existing-host bootstrap default after the `ensure_env_default` function:

```bash
ensure_env_default RECORDER_WRITER_WORKERS 1
```

Do not change any trading or existing queue/batch/flush values.

- [ ] **Step 5: Wire the setting into recorder construction**

In `src/bp_engine/recorder/service.py`, construct the writer as:

```python
writer = BatchWriter(
    buffer=buffer,
    sink=database_sink.write_events,
    batch_size=settings.recorder_batch_size,
    flush_interval_seconds=settings.recorder_flush_interval_seconds,
    worker_count=settings.recorder_writer_workers,
)
```

In `tests/recorder/test_recorder_service.py`, build with an explicit non-default value to prove the builder accepts it without changing safety:

```python
settings = Settings(
    database_url=f"sqlite:///{tmp_path / 'recorder.db'}",
    recorder_writer_workers=3,
)
```

Keep the existing `settings.live_trading_enabled is False` assertion.

- [ ] **Step 6: Run targeted GREEN**

Run:

```bash
pytest tests/test_config.py tests/recorder/test_recorder_service.py -q
ruff check src/bp_engine/config.py src/bp_engine/recorder/service.py tests/test_config.py tests/recorder/test_recorder_service.py
```

Expected: PASS after Task 2 supplies the new constructor argument; until then, the service test may remain RED specifically on `worker_count`.

- [ ] **Step 7: Commit configuration contract**

```bash
git add src/bp_engine/config.py src/bp_engine/recorder/service.py tests/test_config.py tests/recorder/test_recorder_service.py .env.example deploy/bp.env.example scripts/deploy/bootstrap_ubuntu.sh
git commit -m "feat: configure bounded recorder writer workers"
```

---

### Task 2: Implement bounded parallel `BatchWriter` workers

**Files:**
- Modify: `src/bp_engine/recorder/writer.py`
- Modify: `tests/recorder/test_writer.py`

**Interfaces:**
- Consumes: `worker_count: int` from Task 1.
- Produces: `BatchWriter(..., worker_count: int = 1)`; a fixed set of worker tasks named `recorder-writer-0`, `recorder-writer-1`, etc.; fail-fast propagation of worker exceptions.

- [ ] **Step 1: Write constructor validation RED**

Add to `tests/recorder/test_writer.py`:

```python
def test_batch_writer_requires_at_least_one_worker() -> None:
    buffer = EventBuffer(maxsize=10)

    async def sink(items: list[RawEvent]) -> None:
        return None

    with pytest.raises(ValueError, match="worker_count"):
        BatchWriter(
            buffer=buffer,
            sink=sink,
            batch_size=2,
            flush_interval_seconds=1,
            worker_count=0,
        )
```

Run:

```bash
pytest tests/recorder/test_writer.py::test_batch_writer_requires_at_least_one_worker -q
```

Expected: FAIL because `worker_count` is not accepted.

- [ ] **Step 2: Write concurrent-drain RED**

Add a test using a barrier to prove two sink calls overlap:

```python
@pytest.mark.asyncio
async def test_batch_writer_uses_bounded_parallel_workers() -> None:
    buffer = EventBuffer(maxsize=20)
    entered = 0
    max_entered = 0
    two_entered = asyncio.Event()
    release = asyncio.Event()

    async def sink(items: list[RawEvent]) -> None:
        nonlocal entered, max_entered
        entered += 1
        max_entered = max(max_entered, entered)
        if entered >= 2:
            two_entered.set()
        await release.wait()
        entered -= 1

    writer = BatchWriter(
        buffer=buffer,
        sink=sink,
        batch_size=1,
        flush_interval_seconds=1,
        worker_count=2,
    )
    stop = asyncio.Event()
    task = asyncio.create_task(writer.run(stop))

    await buffer.put(event(1))
    await buffer.put(event(2))
    await asyncio.wait_for(two_entered.wait(), timeout=1)
    release.set()
    stop.set()
    await asyncio.wait_for(task, timeout=1)

    assert max_entered == 2
```

Run it and confirm RED on the current single consumer.

- [ ] **Step 3: Write lossless burst/shutdown RED**

Add:

```python
@pytest.mark.asyncio
async def test_parallel_writer_drains_every_unique_event_on_shutdown() -> None:
    buffer = EventBuffer(maxsize=200)
    stored: list[str] = []

    async def sink(items: list[RawEvent]) -> None:
        await asyncio.sleep(0)
        stored.extend(str(item.sequence) for item in items)

    writer = BatchWriter(
        buffer=buffer,
        sink=sink,
        batch_size=7,
        flush_interval_seconds=0.01,
        worker_count=4,
    )
    for sequence in range(100):
        await buffer.put(event(sequence))

    stop = asyncio.Event()
    stop.set()
    await writer.run(stop)

    assert len(stored) == 100
    assert set(stored) == {str(sequence) for sequence in range(100)}
```

- [ ] **Step 4: Write worker-failure propagation RED**

Add:

```python
@pytest.mark.asyncio
async def test_parallel_writer_propagates_worker_failure() -> None:
    buffer = EventBuffer(maxsize=10)

    async def sink(items: list[RawEvent]) -> None:
        raise RuntimeError("database write failed")

    writer = BatchWriter(
        buffer=buffer,
        sink=sink,
        batch_size=1,
        flush_interval_seconds=1,
        worker_count=2,
    )
    await buffer.put(event(1))

    with pytest.raises(RuntimeError, match="database write failed"):
        await writer.run(asyncio.Event())
```

- [ ] **Step 5: Implement the worker pool**

Refactor `BatchWriter` so its constructor stores a validated `worker_count`, move the existing serial loop into `_run_worker`, and supervise a fixed task set:

```python
class BatchWriter:
    def __init__(
        self,
        *,
        buffer: EventBuffer,
        sink: Sink,
        batch_size: int,
        flush_interval_seconds: float,
        worker_count: int = 1,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be greater than zero")
        if flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be greater than zero")
        if worker_count <= 0:
            raise ValueError("worker_count must be greater than zero")
        self._buffer = buffer
        self._sink = sink
        self._batch_size = batch_size
        self._flush_interval_seconds = flush_interval_seconds
        self._worker_count = worker_count
```

Use this shutdown condition inside each worker so workers keep draining after `stop` until the shared queue is empty:

```python
if stop.is_set() and self._buffer.empty():
    break
```

Supervise workers without unbounded task creation:

```python
async def run(self, stop: asyncio.Event) -> None:
    workers = [
        asyncio.create_task(self._run_worker(stop), name=f"recorder-writer-{index}")
        for index in range(self._worker_count)
    ]
    try:
        await asyncio.gather(*workers)
    except BaseException:
        for task in workers:
            if not task.done():
                task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        raise
```

Keep batching bounded by `self._batch_size`; do not create one task per event or frame.

- [ ] **Step 6: Run writer GREEN**

```bash
pytest tests/recorder/test_writer.py -q
ruff check src/bp_engine/recorder/writer.py tests/recorder/test_writer.py
```

Expected: all writer tests PASS, including existing batch-size, interval, and graceful-shutdown tests.

- [ ] **Step 7: Run Task 1 service/config tests again**

```bash
pytest tests/test_config.py tests/recorder/test_recorder_service.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit the bounded worker pool**

```bash
git add src/bp_engine/recorder/writer.py tests/recorder/test_writer.py
git commit -m "fix: drain recorder events with bounded writers"
```

---

### Task 3: Coalesce backpressure into explicit overload episodes

**Files:**
- Modify: `src/bp_engine/recorder/service.py`
- Modify: `tests/recorder/test_recorder_service.py`

**Interfaces:**
- Produces: one `FeedIncident(incident_type="backpressure")` per continuous queue-full episode and one `FeedIncident(incident_type="backpressure_recovered")` on the first later successful `put_nowait`.
- Recovery details: `episode_started_at`, `recovered_at`, `duration_seconds`, `blocked_event_count`.
- Start details: `queue_size`, `episode_started_at`.

- [ ] **Step 1: Add a deterministic raw-event helper to service tests**

In `tests/recorder/test_recorder_service.py`, import `RawEvent`, `FeedIncident`, `EventBuffer`, and the private `_BufferedEventSink`, then add:

```python
def raw_event(sequence: int, second: int) -> RawEvent:
    observed_at = datetime(2026, 9, 3, 17, 0, second, tzinfo=UTC)
    return RawEvent.build(
        source="polymarket",
        stream="market",
        instrument="condition-test",
        event_type="last_trade_price",
        source_timestamp=observed_at,
        received_at=observed_at,
        sequence=sequence,
        asset_id="token-test",
        payload={"asset_id": "token-test", "price": "0.5"},
    )
```

- [ ] **Step 2: Write one-episode coalescing RED**

Add a test with `EventBuffer(maxsize=1)` that pre-fills the queue, starts two concurrent sink calls that both encounter full capacity, drains enough events for both calls to complete, then sends one later event that succeeds immediately.

Assert exactly:

```python
assert [incident.incident_type for incident in incidents] == [
    "backpressure",
    "backpressure_recovered",
]
assert incidents[0].details["queue_size"] == 1
assert incidents[1].details["blocked_event_count"] == 2
assert incidents[1].details["duration_seconds"] >= 0
```

Also assert that the sequences drained from the buffer include every submitted event; no event may disappear because of incident coalescing.

Run:

```bash
pytest tests/recorder/test_recorder_service.py -q
```

Expected: RED because the current sink emits one `backpressure` row per blocked event and has no recovery incident.

- [ ] **Step 3: Write second-episode RED**

After the first recovery, deliberately fill the queue again, block one more event, recover again, and assert incident types are:

```python
[
    "backpressure",
    "backpressure_recovered",
    "backpressure",
    "backpressure_recovered",
]
```

- [ ] **Step 4: Implement episode state without serializing the whole sink**

In `_BufferedEventSink.__init__`, add only bookkeeping state:

```python
self._backpressure_lock = asyncio.Lock()
self._backpressure_started_at: datetime | None = None
self._backpressure_blocked_events = 0
```

Add small helpers that hold the lock only while changing counters. Do not hold the lock while awaiting `buffer.put(...)` or writing an incident to PostgreSQL.

On queue full:

```python
emit_start = False
async with self._backpressure_lock:
    self._backpressure_blocked_events += 1
    if self._backpressure_started_at is None:
        self._backpressure_started_at = event.received_at
        emit_start = True

if emit_start:
    await self._record_incident(
        FeedIncident(
            source=event.source,
            stream=event.stream,
            incident_type="backpressure",
            observed_at=event.received_at,
            details={
                "queue_size": self._buffer.qsize(),
                "episode_started_at": event.received_at.isoformat(),
            },
        )
    )
await self._buffer.put(event)
```

On a successful `put_nowait`, atomically take and clear recovery state, then emit one recovery incident outside the lock:

```python
recovery: tuple[datetime, int] | None = None
async with self._backpressure_lock:
    if self._backpressure_started_at is not None:
        recovery = (
            self._backpressure_started_at,
            self._backpressure_blocked_events,
        )
        self._backpressure_started_at = None
        self._backpressure_blocked_events = 0

if recovery is not None:
    started_at, blocked_event_count = recovery
    await self._record_incident(
        FeedIncident(
            source=event.source,
            stream=event.stream,
            incident_type="backpressure_recovered",
            observed_at=event.received_at,
            details={
                "episode_started_at": started_at.isoformat(),
                "recovered_at": event.received_at.isoformat(),
                "duration_seconds": max(
                    0.0,
                    (event.received_at - started_at).total_seconds(),
                ),
                "blocked_event_count": blocked_event_count,
            },
        )
    )
```

Any incident write exception must still propagate; do not catch and hide it.

- [ ] **Step 5: Run coalescing GREEN**

```bash
pytest tests/recorder/test_recorder_service.py -q
ruff check src/bp_engine/recorder/service.py tests/recorder/test_recorder_service.py
```

Expected: PASS.

- [ ] **Step 6: Commit overload episode behavior**

```bash
git add src/bp_engine/recorder/service.py tests/recorder/test_recorder_service.py
git commit -m "fix: coalesce recorder backpressure incidents"
```

---

### Task 4: Add deterministic Polymarket burst regression coverage

**Files:**
- Create: `tests/recorder/test_backpressure_stress.py`
- Reuse without changing semantics: `src/bp_engine/collectors/websocket_runner.py`
- Reuse: `src/bp_engine/recorder/writer.py`
- Reuse: `src/bp_engine/recorder/service.py`

**Interfaces:**
- Consumes: `WebSocketCollectorRunner`, `EventBuffer`, `BatchWriter(worker_count=4)`, `_BufferedEventSink`.
- Produces: a deterministic sustainable-load regression and a separate bounded-overload fail-closed regression.

- [ ] **Step 1: Build deterministic fake socket and parser**

Create `tests/recorder/test_backpressure_stress.py` with a queue-backed fake WebSocket matching the existing collector tests. Each incoming message is a dict `{"sequence": N}` and the parser returns one Polymarket `RawEvent` with that sequence.

Use `BURST_EVENTS = 2_000`, `QUEUE_SIZE = 100`, `BATCH_SIZE = 25`, and `WORKERS = 4`. These values intentionally make the old single-writer path saturate in a fast unit test without attempting to simulate five real minutes wall-clock.

- [ ] **Step 2: Write sustainable burst RED/GREEN test**

Use a fake persistence sink with bounded artificial latency:

```python
stored: list[str] = []
active_writes = 0
max_active_writes = 0

async def slow_sink(items: list[RawEvent]) -> None:
    nonlocal active_writes, max_active_writes
    active_writes += 1
    max_active_writes = max(max_active_writes, active_writes)
    await asyncio.sleep(0.001)
    stored.extend(str(item.sequence) for item in items)
    active_writes -= 1
```

Run `BatchWriter(... worker_count=4)` and the `WebSocketCollectorRunner` together. Feed all `BURST_EVENTS`, wait until every event is persisted, allow at least one heartbeat interval to elapse, then stop both components.

Assert:

```python
assert len(stored) == BURST_EVENTS
assert len(set(stored)) == BURST_EVENTS
assert max_active_writes == WORKERS
assert "PING" in websocket.sent
assert connector.connection_count == 1
assert not [
    incident
    for incident in websocket_incidents
    if incident.incident_type in {"error", "reconnect"}
]
assert len([
    incident for incident in sink_incidents
    if incident.incident_type == "backpressure"
]) <= 1
```

The test is about sustainable bounded concurrency, not infinite ingress.

- [ ] **Step 3: Write explicit fail-closed overload test**

Use a tiny `EventBuffer(maxsize=1)` and a sink blocked on an `asyncio.Event`. Submit enough events to make the producer task block. Assert the producer is not complete while the queue remains saturated; then release the database sink and assert all submitted sequences eventually persist.

The core assertions are:

```python
assert not producer_task.done()
release_database.set()
await asyncio.wait_for(producer_task, timeout=2)
assert set(stored) == {str(sequence) for sequence in range(total_events)}
```

This proves overload remains bounded/fail-closed instead of silently dropping data.

- [ ] **Step 4: Run stress tests**

```bash
pytest tests/recorder/test_backpressure_stress.py -q
```

Expected: PASS with no reconnect and no event loss in the sustainable case; PASS with intentional producer blocking but eventual lossless drain in the overload case.

- [ ] **Step 5: Run existing WebSocket regressions unchanged**

```bash
pytest \
  tests/collectors/test_websocket_runner.py \
  tests/collectors/test_websocket_runner_reliability.py \
  tests/collectors/test_websocket_runner_outbound_reconnect.py \
  tests/collectors/test_polymarket_reconnect_resubscribe.py \
  -q
```

Expected: PASS. Do not change reconnect policy merely to satisfy the stress test.

- [ ] **Step 6: Commit stress regression**

```bash
git add tests/recorder/test_backpressure_stress.py
git commit -m "test: reproduce recorder burst backpressure"
```

---

### Task 5: Validate PostgreSQL idempotency, V2 isolation, and engineering-only completion

**Files:**
- Modify: `docs/CHANGELOG.md`
- No production rollout files are created or executed.

**Interfaces:**
- Produces: engineering acceptance evidence only; no deployment authorization or production worker-count selection.

- [ ] **Step 1: Run raw recorder repository tests**

```bash
pytest tests/storage/test_recorder.py -q
```

Expected: PASS, preserving PostgreSQL/SQLite dedupe behavior through `dedupe_key` and `ON CONFLICT DO NOTHING`.

- [ ] **Step 2: Run recorder/state regression set**

```bash
pytest \
  tests/recorder/test_writer.py \
  tests/recorder/test_recorder_service.py \
  tests/recorder/test_backpressure_stress.py \
  tests/recorder/test_state_reducer.py \
  tests/recorder/test_state_snapshotter.py \
  tests/recorder/test_soak.py \
  -q
```

Expected: PASS.

- [ ] **Step 3: Run V2 outcome-blind regression set**

```bash
pytest \
  tests/features/test_v2_forward.py \
  tests/features/test_v2_coverage.py \
  tests/features/test_v2_service.py \
  -q
```

Expected: PASS, including `policy_selected=false`, `automatic_promotion=false`, zero outcome/paper/P&L dependencies in V2 coverage/forward modules, and unchanged V2 timestamp semantics.

- [ ] **Step 4: Run configuration and safety checks**

```bash
pytest tests/test_config.py -q
```

Expected: PASS with research mode, live trading disabled, and zero real-money limits unchanged.

- [ ] **Step 5: Run Ruff and full Python suite**

```bash
ruff check .
pytest -q
```

Expected: PASS.

- [ ] **Step 6: Update changelog without claiming production deployment**

Add a new top entry to `docs/CHANGELOG.md` describing:

```text
Phase 14 recorder backpressure reliability repair is engineering-complete but not production-deployed. The repair adds configurable bounded raw-event writer concurrency with application default 1, coalesces repeated queue-full incidents into overload start/recovery evidence, and adds deterministic burst tests proving lossless bounded behavior. It does not change V2 semantics, policy selection, trading safety, selected-book freshness, or production worker configuration. Production rollout remains a separate explicit authorization.
```

Do not write that the incident is production-fixed until a separately authorized natural-load rollout/soak passes.

- [ ] **Step 7: Commit engineering completion documentation**

```bash
git add docs/CHANGELOG.md
git commit -m "docs: record recorder reliability engineering fix"
```

- [ ] **Step 8: Prepare PR only after verification**

Before opening the PR, inspect the complete diff from the implementation branch to `main` and confirm it contains only recorder reliability/config/tests/docs changes. The PR description must explicitly say:

```text
Production deployment is not included or authorized. RECORDER_WRITER_WORKERS remains application-default 1 until a separate rollout selects and validates a bounded production value.
```

Do not merge or deploy merely because the PR exists.
