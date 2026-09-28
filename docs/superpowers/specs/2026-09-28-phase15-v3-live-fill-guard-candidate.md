# Phase 15 — Frozen V3 Live Fill Guard Candidate

**Date:** 28 September 2026  
**Status:** engineering candidate only; not authorized or deployed for another live order

## Problem

Both controlled live canaries reached Polymarket, were accepted, and were later reconciled as zero-fill.

The second canary's recorded paper/live-account timestamp was `2026-09-28T13:54:11.790812+00:00`, while its executor completion was `2026-09-28T13:54:29.198434+00:00`. That is about **17.4 seconds** between the frozen paper-order timestamp and the completed live submission path.

The live executor reused the frozen paper limit price exactly and then left the order open for two seconds. A price that was marketable when the paper order was created can therefore be stale by the time the real order reaches the CLOB.

Increasing the limit price would change the economics of the frozen V3 trade and could erase the minimum edge. Increasing TTL alone would leave a stale, non-marketable order resting longer without proving that it is fillable.

## Candidate fix

Before any activation is created, kill switch is removed, global attempt marker is written, or network submission attempt is consumed:

1. call a read-only quote action on the Johannesburg executor;
2. fetch the current official Polymarket order book for the exact token;
3. compute the current best ask;
4. sum visible ask depth at prices **less than or equal to the already-frozen limit price**;
5. proceed only if:
   - best ask <= frozen limit price; and
   - marketable depth >= the exact requested shares.

If either check fails, write a local fill-guard receipt and leave:

- kill switch engaged;
- activation absent;
- authorization slot unconsumed;
- network submission attempt unconsumed;
- real order unsubmitted.

The existing supervisor can then allow the prepared market window to end, reconcile the intent as closed-before-submission, and move to a new candidate without spending the one real network attempt.

## What this deliberately does not do

- no repricing above the frozen limit;
- no market order;
- no TTL increase;
- no stake increase;
- no strategy/model/edge change;
- no additional network attempt;
- no third-order authorization;
- no global-live enablement;
- no production deployment.

## Expected effect

For a $5 order, the submission path is entered only when the current displayed book shows enough immediately marketable inventory at or better than the frozen price. This does not guarantee a fill because the book can change between the read-only quote and order arrival, but it removes the known failure mode of knowingly submitting a stale/non-marketable limit and materially raises the probability that the two-second order is filled immediately.

## Candidate artifacts

- `scripts/deploy/phase15_v3_canary_executor_fill_guard_candidate.py`
- `src/bp_engine/execution/telegram_privileged_consumer_fill_guard_candidate.py`
- `tests/execution/test_phase15_fill_guard_candidate.py`

Production-bound executor and privileged-consumer files remain unchanged.
