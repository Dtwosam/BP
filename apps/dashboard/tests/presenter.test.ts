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

test("phase12 KPIs show realized paper P&L from execution evidence", () => {
  const phase12Snapshot = {
    ...snapshot,
    mode: { ...snapshot.mode, paper_execution_available: true },
    paper_pnl: {
      status: "AVAILABLE",
      starting_cash: 100,
      current_cash: 101.1496,
      open_capital: 0,
      unrealized_value: null,
      realized_pnl: 1.1496,
      return_on_starting_cash: 0.011496,
      max_realized_equity_drawdown: 0,
      settled_trade_count: 1,
      open_position_count: 0,
      fill_count: 1,
      no_fill_expired_count: 0,
      total_fees: 0.0504,
      total_slippage_cost: 0,
      reconciliation: { status: "OK", violation_count: 0 },
    },
  };

  assert.deepEqual(buildKpis(phase12Snapshot), {
    activeMarkets: 1,
    healthyFeeds: 1,
    totalFeeds: 2,
    evaluatedPredictions: 12,
    paperPnl: "+$1.15",
  });
});

test("snapshot freshness becomes stale after one minute and preserves refresh errors", () => {
  const now = new Date("2026-08-28T12:00:30Z");
  assert.equal(classifySnapshotFreshness(snapshot.generated_at, now, false), "fresh");

  const later = new Date("2026-08-28T12:01:01Z");
  assert.equal(classifySnapshotFreshness(snapshot.generated_at, later, false), "stale");
  assert.equal(classifySnapshotFreshness(snapshot.generated_at, later, true), "error");
});
