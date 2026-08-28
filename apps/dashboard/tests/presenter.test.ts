import assert from "node:assert/strict";
import test from "node:test";

import {
  buildKpis,
  classifySnapshotFreshness,
  formatPercent,
} from "../lib/presenter.ts";

const snapshot = {
  generated_at: "2026-08-28T12:00:00+00:00",
  mode: {
    trading_mode: "RESEARCH",
    live_trading_enabled: false,
    execution_available: false,
    paper_execution_available: false,
  },
  active_markets: [{ condition_id: "c1" }],
  feed_health: [
    { source: "coinbase", stream: "spot", status: "connected", age_seconds: 2 },
    { source: "bybit", stream: "linear", status: "reconnecting", age_seconds: 41 },
  ],
  performance: [
    { horizon_seconds: 300, total_predictions: 10, evaluated_predictions: 8 },
    { horizon_seconds: 900, total_predictions: 6, evaluated_predictions: 4 },
  ],
  prediction_history: [],
  paper_pnl: { status: "UNAVAILABLE_UNTIL_PHASE_12", value: null },
};

test("missing numeric evidence stays visibly missing", () => {
  assert.equal(formatPercent(null), "—");
  assert.equal(formatPercent(undefined), "—");
  assert.equal(formatPercent(0), "0.0%");
  assert.equal(formatPercent(0.6234), "62.3%");
});

test("operator KPIs summarize evidence without inventing paper P&L", () => {
  assert.deepEqual(buildKpis(snapshot), {
    activeMarkets: 1,
    healthyFeeds: 1,
    totalFeeds: 2,
    evaluatedPredictions: 12,
    paperPnl: "Unavailable until Phase 12",
  });
});

test("snapshot freshness becomes stale after one minute and preserves refresh errors", () => {
  const now = new Date("2026-08-28T12:00:30Z");
  assert.equal(classifySnapshotFreshness(snapshot.generated_at, now, false), "fresh");

  const later = new Date("2026-08-28T12:01:01Z");
  assert.equal(classifySnapshotFreshness(snapshot.generated_at, later, false), "stale");
  assert.equal(classifySnapshotFreshness(snapshot.generated_at, later, true), "error");
});
