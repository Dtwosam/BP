"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  buildKpis,
  classifySnapshotFreshness,
  formatPercent,
  formatSignedUsd,
} from "../lib/presenter";
import type {
  ActiveMarket,
  DashboardSnapshot,
  FeedHealth,
  PaperFillRow,
  PaperOrderRow,
  PaperSettlementRow,
  PerformanceRow,
  PredictionHistoryRow,
} from "../lib/snapshot";

const REFRESH_MS = 15_000;

function formatNumber(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return value.toFixed(digits);
}

function formatUsd(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  if (value < 0) return `-$${Math.abs(value).toFixed(2)}`;
  return `$${value.toFixed(2)}`;
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "—";
  return date.toLocaleString(undefined, {
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function formatAge(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "—";
  if (value < 60) return `${Math.max(0, Math.round(value))}s`;
  if (value < 3600) return `${Math.round(value / 60)}m`;
  return `${(value / 3600).toFixed(1)}h`;
}

function horizonLabel(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

function statusTone(status: string | null | undefined): string {
  const value = status?.toLowerCase() ?? "unknown";
  if (["connected", "ok", "healthy", "pass", "available", "filled"].includes(value)) {
    return "good";
  }
  if (["reconnecting", "warning", "stale", "degraded", "cancelled", "expired"].includes(value)) {
    return "warn";
  }
  if (["violation", "failed", "error", "blocked"].includes(value)) return "bad";
  return "neutral";
}

function truthLabel(value: boolean | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value ? "Yes" : "No";
}

function MarketRows({ rows }: { rows: ActiveMarket[] }) {
  if (!rows.length) {
    return <p className="empty">No active market evidence is available in this snapshot.</p>;
  }
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Market</th>
            <th>Window</th>
            <th>Model</th>
            <th>Market</th>
            <th>Bid / Ask</th>
            <th>Edge</th>
            <th>Decision</th>
            <th>Recorded</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.condition_id}>
              <td>
                <strong>{row.question ?? row.slug ?? row.condition_id}</strong>
                <span className="subtle mono">{row.slug ?? row.condition_id}</span>
              </td>
              <td>{horizonLabel(row.horizon_seconds)}</td>
              <td>
                <strong>{formatPercent(row.calibrated_probability)}</strong>
                <span className="subtle">{row.predicted_side ?? "—"}</span>
              </td>
              <td>{formatPercent(row.market_probability)}</td>
              <td>
                {formatPercent(row.selected_bid)} / {formatPercent(row.selected_ask)}
              </td>
              <td>{formatPercent(row.cost_adjusted_edge)}</td>
              <td>
                <span className={`pill ${row.trade ? "good" : "neutral"}`}>
                  {row.trade === null || row.trade === undefined
                    ? "—"
                    : row.trade
                      ? "TRADE SIGNAL"
                      : "NO TRADE"}
                </span>
                <span className="subtle">{row.decision_reason ?? "—"}</span>
              </td>
              <td>{formatTime(row.recorded_at ?? row.scheduled_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function FeedRows({ rows }: { rows: FeedHealth[] }) {
  if (!rows.length) {
    return <p className="empty">No feed-status rows are available.</p>;
  }
  return (
    <div className="table-wrap compact">
      <table>
        <thead>
          <tr>
            <th>Source</th>
            <th>Stream</th>
            <th>Status</th>
            <th>Age</th>
            <th>Last received</th>
            <th>Source timestamp</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.source}:${row.stream}`}>
              <td><strong>{row.source}</strong></td>
              <td>{row.stream}</td>
              <td><span className={`pill ${statusTone(row.status)}`}>{row.status ?? "unknown"}</span></td>
              <td>{formatAge(row.age_seconds)}</td>
              <td>{formatTime(row.last_received_at)}</td>
              <td>{formatTime(row.last_source_timestamp)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PerformanceCards({ rows }: { rows: PerformanceRow[] }) {
  if (!rows.length) {
    return <p className="empty">No immutable prediction performance is available yet.</p>;
  }
  return (
    <div className="performance-grid">
      {rows.map((row) => (
        <article className="performance-card" key={row.horizon_seconds}>
          <div className="card-heading">
            <div>
              <span className="eyebrow">Horizon</span>
              <h3>{horizonLabel(row.horizon_seconds)}</h3>
            </div>
            <span className="sample-count">{row.evaluated_predictions}/{row.total_predictions} evaluated</span>
          </div>
          <dl className="metric-list">
            <div><dt>Coverage</dt><dd>{formatPercent(row.coverage)}</dd></div>
            <div><dt>Accuracy</dt><dd>{formatPercent(row.accuracy)}</dd></div>
            <div><dt>Brier</dt><dd>{formatNumber(row.calibrated_brier)}</dd></div>
            <div><dt>Log loss</dt><dd>{formatNumber(row.calibrated_log_loss)}</dd></div>
          </dl>
          <div className="calibration">
            <span className="eyebrow">Calibration</span>
            {row.calibration_buckets.length ? (
              <div className="bucket-list">
                {row.calibration_buckets.map((bucket) => (
                  <div className="bucket" key={bucket.label}>
                    <span>{bucket.label}</span>
                    <strong>{formatPercent(bucket.observed_up_rate)}</strong>
                    <small>{bucket.count} · model {formatPercent(bucket.mean_probability)}</small>
                  </div>
                ))}
              </div>
            ) : (
              <p className="subtle">No evaluated calibration buckets yet.</p>
            )}
          </div>
        </article>
      ))}
    </div>
  );
}

function PaperOrderRows({ rows }: { rows: PaperOrderRow[] }) {
  if (!rows.length) return <p className="empty">No paper orders have been recorded yet.</p>;
  return (
    <div className="table-wrap compact">
      <table>
        <thead>
          <tr>
            <th>Submitted</th>
            <th>Side</th>
            <th>Requested</th>
            <th>Limit</th>
            <th>Status</th>
            <th>Remaining</th>
            <th>Realized P&amp;L</th>
            <th>Order</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.paper_order_id}>
              <td>{formatTime(row.submitted_at)}</td>
              <td><strong>{row.selected_side?.toUpperCase() ?? "—"}</strong></td>
              <td>{formatNumber(row.requested_shares, 4)}</td>
              <td>{formatPercent(row.limit_price)}</td>
              <td><span className={`pill ${statusTone(row.terminal_status)}`}>{row.terminal_status ?? "OPEN"}</span></td>
              <td>{formatNumber(row.remaining_shares, 4)}</td>
              <td>{row.realized_pnl === null || row.realized_pnl === undefined ? "—" : formatSignedUsd(row.realized_pnl)}</td>
              <td><span className="mono">{row.paper_order_id.slice(0, 12)}…</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PaperFillRows({ rows }: { rows: PaperFillRow[] }) {
  if (!rows.length) return <p className="empty">No paper fills have been recorded yet.</p>;
  return (
    <div className="table-wrap compact">
      <table>
        <thead>
          <tr>
            <th>Filled</th>
            <th>Shares</th>
            <th>Price</th>
            <th>Total cost</th>
            <th>Fee</th>
            <th>Ask slippage</th>
            <th>Order</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={row.fill_key ?? `${row.paper_order_id}:${index}`}>
              <td>{formatTime(row.fill_at)}</td>
              <td>{formatNumber(row.shares, 4)}</td>
              <td>{formatPercent(row.price)}</td>
              <td>{formatUsd(row.total_cost)}</td>
              <td>{formatUsd(row.fee)}</td>
              <td>{formatPercent(row.signal_ask_slippage)}</td>
              <td><span className="mono">{row.paper_order_id.slice(0, 12)}…</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function PaperSettlementRows({ rows }: { rows: PaperSettlementRow[] }) {
  if (!rows.length) return <p className="empty">No paper settlements have been recorded yet.</p>;
  return (
    <div className="table-wrap compact">
      <table>
        <thead>
          <tr>
            <th>Settled</th>
            <th>Outcome</th>
            <th>Shares</th>
            <th>Fill cost</th>
            <th>Payout</th>
            <th>Fees</th>
            <th>Realized P&amp;L</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={`${row.paper_order_id}:${row.label_version ?? index}`}>
              <td>{formatTime(row.settled_at)}</td>
              <td><strong>{row.official_outcome?.toUpperCase() ?? "—"}</strong></td>
              <td>{formatNumber(row.filled_shares, 4)}</td>
              <td>{formatUsd(row.total_fill_cost)}</td>
              <td>{formatUsd(row.payout)}</td>
              <td>{formatUsd(row.total_fees)}</td>
              <td>{row.realized_pnl === null || row.realized_pnl === undefined ? "—" : formatSignedUsd(row.realized_pnl)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OpenPaperPositions({ snapshot }: { snapshot: DashboardSnapshot }) {
  const settled = new Set(snapshot.paper_settlements.map((row) => row.paper_order_id));
  const sharesByOrder = new Map<string, number>();
  const costByOrder = new Map<string, number>();
  for (const fill of snapshot.paper_fills) {
    sharesByOrder.set(fill.paper_order_id, (sharesByOrder.get(fill.paper_order_id) ?? 0) + (fill.shares ?? 0));
    costByOrder.set(fill.paper_order_id, (costByOrder.get(fill.paper_order_id) ?? 0) + (fill.total_cost ?? 0));
  }
  const rows = snapshot.paper_orders.filter(
    (order) => !settled.has(order.paper_order_id) && (sharesByOrder.get(order.paper_order_id) ?? 0) > 0,
  );
  if (!rows.length) return <p className="empty">No open paper positions.</p>;
  return (
    <div className="table-wrap compact">
      <table>
        <thead><tr><th>Side</th><th>Filled shares</th><th>Capital paid</th><th>Status</th><th>Order</th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.paper_order_id}>
              <td><strong>{row.selected_side?.toUpperCase() ?? "—"}</strong></td>
              <td>{formatNumber(sharesByOrder.get(row.paper_order_id), 4)}</td>
              <td>{formatUsd(costByOrder.get(row.paper_order_id))}</td>
              <td>{row.terminal_status ?? "OPEN"}</td>
              <td><span className="mono">{row.paper_order_id.slice(0, 12)}…</span></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function HistoryRows({ rows }: { rows: PredictionHistoryRow[] }) {
  if (!rows.length) {
    return <p className="empty">No immutable prediction history is available.</p>;
  }
  return (
    <div className="table-wrap history-table">
      <table>
        <thead>
          <tr>
            <th>Recorded</th>
            <th>Market</th>
            <th>Horizon</th>
            <th>Model</th>
            <th>Side</th>
            <th>Market</th>
            <th>Edge</th>
            <th>Decision</th>
            <th>Official result</th>
            <th>Correct</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.prediction_id}>
              <td>{formatTime(row.recorded_at ?? row.scheduled_at)}</td>
              <td>
                <strong>{row.slug ?? row.condition_id ?? "—"}</strong>
                <span className="subtle mono">{row.prediction_id}</span>
              </td>
              <td>{horizonLabel(row.horizon_seconds)}</td>
              <td>{formatPercent(row.calibrated_probability)}</td>
              <td>{row.predicted_side ?? "—"}</td>
              <td>{formatPercent(row.market_probability)}</td>
              <td>{formatPercent(row.cost_adjusted_edge)}</td>
              <td>{row.decision_reason ?? "—"}</td>
              <td>{row.official_outcome ?? "Pending"}</td>
              <td>{truthLabel(row.correct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function LiveReadinessPanel({ snapshot }: { snapshot: DashboardSnapshot }) {
  const readiness = snapshot.live_readiness;
  const geoblock =
    readiness.geoblock_blocked === null
      ? "Unavailable"
      : readiness.geoblock_blocked
        ? "Blocked"
        : "Not blocked";
  const location = [readiness.country, readiness.region].filter(Boolean).join(" / ");

  return (
    <section className="panel" aria-label="Live readiness">
      <div className="section-heading">
        <div><span className="eyebrow">Phase 14</span><h2>Live readiness</h2></div>
        <p>Read-only diagnostics only. Readiness evidence never enables execution from this dashboard.</p>
      </div>
      <div className="performance-grid">
        <article className="performance-card">
          <div className="card-heading">
            <div><span className="eyebrow">Gate</span><h3>{readiness.eligible ? "Eligible" : "Blocked"}</h3></div>
            <span className={`pill ${readiness.eligible ? "good" : "bad"}`}>
              {readiness.eligible ? "DIAGNOSTIC PASS" : "DIAGNOSTIC BLOCK"}
            </span>
          </div>
          <dl className="metric-list">
            <div><dt>Activation authorized</dt><dd>{truthLabel(readiness.authorized)}</dd></div>
            <div><dt>Kill switch</dt><dd>{readiness.kill_switch_engaged ? "Engaged" : "Clear"}</dd></div>
            <div><dt>Wallet configured</dt><dd>{truthLabel(readiness.wallet_configured)}</dd></div>
          </dl>
        </article>
        <article className="performance-card">
          <div className="card-heading">
            <div><span className="eyebrow">Jurisdiction</span><h3>Geoblock</h3></div>
            <span className={`pill ${readiness.geoblock_blocked === false ? "good" : readiness.geoblock_blocked ? "bad" : "warn"}`}>
              {geoblock}
            </span>
          </div>
          <dl className="metric-list">
            <div><dt>Country / region</dt><dd>{location || "—"}</dd></div>
            <div><dt>Execution available</dt><dd>No</dd></div>
          </dl>
        </article>
        <article className="performance-card">
          <div className="card-heading">
            <div><span className="eyebrow">Ledger</span><h3>Reconciliation</h3></div>
            <span className={`pill ${statusTone(readiness.reconciliation_status)}`}>
              {readiness.reconciliation_status}
            </span>
          </div>
          <dl className="metric-list">
            <div><dt>Critical discrepancies</dt><dd>{readiness.critical_discrepancy_count ?? "—"}</dd></div>
            <div><dt>Real execution unavailable</dt><dd>Yes</dd></div>
          </dl>
        </article>
      </div>
    </section>
  );
}

export function DashboardClient({
  initialSnapshot,
}: {
  initialSnapshot: DashboardSnapshot | null;
}) {
  const [snapshot, setSnapshot] = useState<DashboardSnapshot | null>(initialSnapshot);
  const [refreshError, setRefreshError] = useState(initialSnapshot === null);
  const [now, setNow] = useState(() => new Date());

  const refresh = useCallback(async () => {
    try {
      const response = await fetch("/api/snapshot", { cache: "no-store" });
      if (!response.ok) throw new Error(`snapshot route returned ${response.status}`);
      const next = (await response.json()) as DashboardSnapshot;
      setSnapshot(next);
      setRefreshError(false);
      setNow(new Date());
    } catch {
      setRefreshError(true);
      setNow(new Date());
    }
  }, []);

  useEffect(() => {
    void refresh();
    const refreshTimer = window.setInterval(() => void refresh(), REFRESH_MS);
    const clockTimer = window.setInterval(() => setNow(new Date()), 5_000);
    return () => {
      window.clearInterval(refreshTimer);
      window.clearInterval(clockTimer);
    };
  }, [refresh]);

  const freshness = snapshot
    ? classifySnapshotFreshness(snapshot.generated_at, now, refreshError)
    : "error";
  const kpis = useMemo(() => (snapshot ? buildKpis(snapshot) : null), [snapshot]);

  if (!snapshot || !kpis) {
    return (
      <main className="shell">
        <section className="fatal-panel">
          <span className="pill bad">SNAPSHOT UNAVAILABLE</span>
          <h1>BP operator dashboard</h1>
          <p>The read-only dashboard API has not returned valid evidence yet.</p>
          <button type="button" onClick={() => void refresh()}>Retry snapshot</button>
        </section>
      </main>
    );
  }

  const reconciliation = snapshot.paper_pnl.reconciliation;

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">BP · Phase 14</span>
          <h1>Prediction &amp; paper execution dashboard</h1>
          <p>Live research evidence, deterministic paper execution, readiness diagnostics, and reconciliation. Read-only by design.</p>
        </div>
        <div className="snapshot-meta">
          <span className={`pill ${freshness === "fresh" ? "good" : freshness === "stale" ? "warn" : "bad"}`}>
            {freshness.toUpperCase()}
          </span>
          <span>Snapshot {formatTime(snapshot.generated_at)}</span>
          {refreshError ? <strong>Latest refresh failed · showing last successful snapshot</strong> : null}
        </div>
      </header>

      <section className="safety-banner" aria-label="Trading safety mode">
        <div>
          <span className="eyebrow">Current mode</span>
          <strong>{snapshot.mode.trading_mode}</strong>
        </div>
        <p>
          <span className={`pill ${snapshot.mode.paper_execution_available ? "good" : "warn"}`}>
            Paper execution {snapshot.mode.paper_execution_available ? "available" : "unavailable"}
          </span>{" "}
          <span className="pill bad">Real execution unavailable</span>{" "}
          Live trading is {snapshot.mode.live_trading_enabled ? "enabled" : "disabled"}. This dashboard cannot place orders.
        </p>
      </section>

      <LiveReadinessPanel snapshot={snapshot} />

      <section className="kpi-grid" aria-label="Dashboard summary">
        <article><span>Active markets</span><strong>{kpis.activeMarkets}</strong></article>
        <article><span>Healthy feeds</span><strong>{kpis.healthyFeeds}/{kpis.totalFeeds}</strong></article>
        <article><span>Evaluated predictions</span><strong>{kpis.evaluatedPredictions}</strong></article>
        <article><span>Paper P&amp;L</span><strong className="small-value">{kpis.paperPnl}</strong></article>
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><span className="eyebrow">BP · Phase 12 · Paper account</span><h2>Paper execution account</h2></div>
          <p>Derived only from immutable paper fills and official settlements. No real capital is used.</p>
        </div>
        <div className="performance-grid">
          <article className="performance-card">
            <div className="card-heading">
              <div><span className="eyebrow">Capital</span><h3>{formatUsd(snapshot.paper_pnl.current_cash)}</h3></div>
              <span className={`pill ${statusTone(snapshot.paper_pnl.status)}`}>{snapshot.paper_pnl.status}</span>
            </div>
            <dl className="metric-list">
              <div><dt>Starting cash</dt><dd>{formatUsd(snapshot.paper_pnl.starting_cash)}</dd></div>
              <div><dt>Realized P&amp;L</dt><dd>{formatSignedUsd(snapshot.paper_pnl.realized_pnl)}</dd></div>
              <div><dt>Return</dt><dd>{formatPercent(snapshot.paper_pnl.return_on_starting_cash)}</dd></div>
              <div><dt>Open capital</dt><dd>{formatUsd(snapshot.paper_pnl.open_capital)}</dd></div>
              <div><dt>Unrealized value</dt><dd>{formatUsd(snapshot.paper_pnl.unrealized_value)}</dd></div>
              <div><dt>Max realized drawdown</dt><dd>{formatUsd(snapshot.paper_pnl.max_realized_equity_drawdown)}</dd></div>
            </dl>
          </article>
          <article className="performance-card">
            <div className="card-heading">
              <div><span className="eyebrow">Integrity</span><h3>Reconciliation</h3></div>
              <span className={`pill ${statusTone(reconciliation?.status)}`}>{reconciliation?.status ?? "UNKNOWN"}</span>
            </div>
            <dl className="metric-list">
              <div><dt>Violations</dt><dd>{reconciliation?.violation_count ?? "—"}</dd></div>
              <div><dt>Paper orders</dt><dd>{reconciliation?.paper_order_count ?? snapshot.paper_orders.length}</dd></div>
              <div><dt>Trade signals</dt><dd>{reconciliation?.trade_signal_count ?? "—"}</dd></div>
              <div><dt>No-trade signals</dt><dd>{reconciliation?.no_trade_signal_count ?? "—"}</dd></div>
              <div><dt>Settled trades</dt><dd>{snapshot.paper_pnl.settled_trade_count ?? "—"}</dd></div>
              <div><dt>Open positions</dt><dd>{snapshot.paper_pnl.open_position_count ?? "—"}</dd></div>
            </dl>
          </article>
          <article className="performance-card">
            <div className="card-heading"><div><span className="eyebrow">Execution costs</span><h3>Paper diagnostics</h3></div></div>
            <dl className="metric-list">
              <div><dt>Fills</dt><dd>{snapshot.paper_pnl.fill_count ?? "—"}</dd></div>
              <div><dt>No-fill expiries</dt><dd>{snapshot.paper_pnl.no_fill_expired_count ?? "—"}</dd></div>
              <div><dt>Total fees</dt><dd>{formatUsd(snapshot.paper_pnl.total_fees)}</dd></div>
              <div><dt>Slippage cost</dt><dd>{formatUsd(snapshot.paper_pnl.total_slippage_cost)}</dd></div>
            </dl>
          </article>
        </div>
        <div className="calibration">
          <span className="eyebrow">Open paper positions</span>
          <OpenPaperPositions snapshot={snapshot} />
        </div>
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><span className="eyebrow">Paper ledger</span><h2>Paper orders</h2></div>
          <p>Orders are deterministic derivatives of immutable eligible prediction signals.</p>
        </div>
        <PaperOrderRows rows={snapshot.paper_orders} />
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><span className="eyebrow">Execution evidence</span><h2>Paper fills</h2></div>
          <p>Fill timing, price, fees, and signal-ask slippage come from causal book replay.</p>
        </div>
        <PaperFillRows rows={snapshot.paper_fills} />
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><span className="eyebrow">Official outcomes</span><h2>Paper settlements</h2></div>
          <p>Realized paper P&amp;L is recognized only after official prediction evaluation evidence exists.</p>
        </div>
        <PaperSettlementRows rows={snapshot.paper_settlements} />
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><span className="eyebrow">Now</span><h2>Active markets</h2></div>
          <p>Latest accepted model evidence joined to currently active Polymarket windows.</p>
        </div>
        <MarketRows rows={snapshot.active_markets} />
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><span className="eyebrow">Infrastructure</span><h2>Feed health</h2></div>
          <p>Stored recorder status and recency. Source status is not rewritten by the UI.</p>
        </div>
        <FeedRows rows={snapshot.feed_health} />
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><span className="eyebrow">Evidence</span><h2>Performance by horizon</h2></div>
          <p>Only official evaluations count toward accuracy, Brier score, log loss, and calibration.</p>
        </div>
        <PerformanceCards rows={snapshot.performance} />
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><span className="eyebrow">Ledger</span><h2>Immutable prediction history</h2></div>
          <p>Predictions stay immutable; official outcomes appear later when evaluation evidence exists.</p>
        </div>
        <HistoryRows rows={snapshot.prediction_history} />
      </section>

      <footer>
        RESEARCH mode · paper execution only · Phase 14 readiness diagnostics · real execution disabled · auto-refresh every 15 seconds
      </footer>
    </main>
  );
}