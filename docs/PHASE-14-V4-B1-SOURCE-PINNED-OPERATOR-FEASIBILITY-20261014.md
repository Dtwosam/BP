# Phase 14 Stage B1 — source-pinned operator feasibility (NO GO)

**14 October 2026 · Issue #589 · REVIEW ONLY, NOT A DEPLOYMENT DECISION**

Base Git repository review: `main=4a92ffb29d431253b8a4572671030ae1df06f2ea`.
This is a targeted assessment of **checked-in code and unit templates**.
It does not establish current deployed source, overrides, process cgroup,
database health, any host runtime settings, or permission to read them.
The unresolved Bybit-linear bulk ingestion failure still has **no
production-root-cause diagnosis**. Phase 14 **HOLD**, economic acceptance
**NOT MET**, live trading disabled.

## Concrete source findings

| Reviewed source | Observed constraint | Operator implication |
|---|---|---|
| `deploy/systemd/bp-recorder.service` | `ExecStart=/opt/bp/.venv/bin/python -m bp_engine.recorder`, `Restart=always`, `TimeoutStopSec=60`, `KillSignal=SIGTERM` | Existing service restart policy and stop grace belong to the **ordinary recorder**, not a one-shot Stage B1 lifetime/restoration controller. No separate B1 watchdog or restoration witness is defined in this checked-in unit. Exact installed unit and systemd drop-ins are unverified. |
| `src/bp_engine/recorder/__main__.py` | `run_recorder()` calls `service_factory(settings)` without injecting the B1 observer | Standard startup cannot be used to obtain Stage B1 counters from the dormant injection seam. |
| `src/bp_engine/recorder/service.py` | The explicitly injected B1 observer is limited to Bybit-linear **bulk** parser/enqueue and bulk writer; normal builder calls `create_engine(settings.database_url)` and `metadata.create_all(engine)` and may run partitioned-storage checks | A parallel launch of the **full recorder factory** is not a wire-only/read-only diagnostic and could touch the live database and all feeds. No production SQL or second recorder launch is authorized. |
| `src/bp_engine/recorder/service.py` | Three `_DatabaseSink` methods use `await asyncio.to_thread(write)` | Event-loop cancellation cannot by itself prove synchronous writer/transaction exit. Task-based shutdown cannot substitute for an independently verified quiescence boundary. |
| `src/bp_engine/recorder/writer.py` | Writer supervision awaits workers and cancels remaining coroutine tasks on error | Async worker completion/cancellation does not attest underlying DB thread or transaction outcome. |
| `src/bp_engine/recorder/stage_b1_one_shot.py` | `run_offline_rehearsal()` is a separately injected library; its timeout is task-level and its JSON report is not a production exporter | It is **not** an approved operator entry point, process supervisor or bounded receipt collector. |

The systemd **60-second stop grace** is not a Stage B1 120-second
process deadline. `Restart=always` requires explicit, separately
verified service restoration/restart handling before any intrusive
instrumentation. No claim is made about installed unit overrides, the
real cgroup's `KillMode`, actual `systemctl` behavior or PostgreSQL
thread/transaction cleanup.

## Two unsafe shortcuts, and what a future design must resolve

### A. Restart/replace the existing recorder with a B1-enabled variant

**Current verdict: NO GO.** The normal entry point doesn't activate
B1. Changing it or installing a patch would alter the existing service
and require stopping/restarting its feed collectors and DB writers,
coordinating with `Restart=always`, preserving the exact previous
source/unit/venv state, and proving DB-worker and feed recovery. The
old source-only reversible patch and the synthetic watchdog do not
provide that proof. This option would require fresh authorization for
the precise runtime mutation and independently enforced restoration.

### B. Launch an additional full recorder instance in parallel

**Current verdict: NO GO.** The ordinary service factory initializes
database storage and multiple collectors, writers, state snapshots and
feed policies. A duplicate process may subscribe to live feeds, write
to production, create schema, consume CPU/RSS/queue capacity, and
affect recorded data. A 120-second observer limit neither makes that
process read-only nor bounds its descendants or `asyncio.to_thread`
writes. No copy of the production recorder is authorized.

A **different future candidate** could be a separately reviewed,
bounded, isolated *read-only* Bybit-linear bulk wire/parser probe,
but that would test only wire/parser arrival and topic ACK assumptions;
it **cannot** diagnose actual writer/commit/insert failures without
additional instrumentation that itself meets the same safety gates.
This is a research direction, not an implemented executable or a
recommendation to contact the exchange/host now.

## Static verification and drift policy

The local script `scripts/review_stage_b1_operator_feasibility.py`
reads **only the five fixed paths in the table**, validates each
exact Git blob hash previously observed on reviewed `main`, then
checks the above runtime-source boundaries. It performs no Git fetch,
network I/O, subprocess, imports of recorder runtime, database
connection, systemd action, or production observation:

```sh
python scripts/review_stage_b1_operator_feasibility.py
python -m pytest -q tests/recorder/test_stage_b1_operator_feasibility.py
```

The CLI **always exits 2 and says NO GO**. A match yields
`PINNED_SOURCE_REVIEW_ONLY_NO_GO`; missing/changed/symlinked/oversized
source or an unexpected source marker yields
`PINNED_SOURCE_DRIFT_OR_MISMATCH_NO_GO`. Both explicitly deny all
candidate, host, process, DB and deployment authority. The output
contains only fixed check names, enums and known caveats; it does
not disclose source contents, host paths or credentials.

The pinned blobs are **intentionally invalidated by any later edit**
to those five files, including benign changes. Refreshing them is
a **new source review**, not an automatic approval. CI checks only
the GitHub checkout and cannot authenticate production bytes.

## Stop/go boundary still unmet

The prior
[17-gate lifecycle contract](PHASE-14-V4-B1-OPERATOR-LIFECYCLE-RESTORE-CONTRACT-20261014.md)
remains the authoritative review proposal. No independently attested
installed unit/overrides, running PID/source/venv, representative
CPU/RSS/queue/write p95, OS/cgroup hard stop, real DB-thread
quiescence, safe output path, or independent restoration has been
supplied. Current production root cause is **UNKNOWN**; no new
production SQL, live socket, service restart, deploy, observer
activation, model/source gate, shadow, holdout or trading may be
inferred from this offline assessment. Issue #589 stays **OPEN**.
