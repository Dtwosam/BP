"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  buildKpis,
  classifySnapshotFreshness,
  formatPercent,
} from "../lib/presenter";
import type {
  ActiveMarket,
  DashboardSnapshot,
  FeedHealth,
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
  if (["connected", "ok", "healthy", "pass"].includes(value)) return "good";
  if (["reconnecting", "warning", "stale", "degraded"].includes(value)) return "warn";
  return "bad";
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

  return (
    <main className="shell">
      <header className="topbar">
        <div>
          <span className="eyebrow">BP · Phase 11</span>
          <h1>Prediction operator dashboard</h1>
          <p>Live evidence, model performance, and feed health. Read-only by design.</p>
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
          Live trading is {snapshot.mode.live_trading_enabled ? "enabled" : "disabled"}. Execution is {snapshot.mode.execution_available ? "available" : "unavailable"}. This dashboard cannot place orders.
        </p>
      </section>

      <section className="kpi-grid" aria-label="Dashboard summary">
        <article><span>Active markets</span><strong>{kpis.activeMarkets}</strong></article>
        <article><span>Healthy feeds</span><strong>{kpis.healthyFeeds}/{kpis.totalFeeds}</strong></article>
        <article><span>Evaluated predictions</span><strong>{kpis.evaluatedPredictions}</strong></article>
        <article><span>Paper P&amp;L</span><strong className="small-value">{kpis.paperPnl}</strong></article>
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

      <section className="phase-boundary">
        <div>
          <span className="eyebrow">Phase boundary</span>
          <h2>Paper P&amp;L is intentionally unavailable</h2>
        </div>
        <p>
          Phase 10 prediction economics are not paper fills. Real paper-fill P&amp;L starts in Phase 12, so this dashboard does not manufacture a return number from historical or hypothetical fields.
        </p>
      </section>

      <section className="panel">
        <div className="section-heading">
          <div><span className="eyebrow">Ledger</span><h2>Immutable prediction history</h2></div>
          <p>Predictions stay immutable; official outcomes appear later when evaluation evidence exists.</p>
        </div>
        <HistoryRows rows={snapshot.prediction_history} />
      </section>

      <footer>
        RESEARCH mode · no wallet · no signing · no execution controls · auto-refresh every 15 seconds
      </footer>
    </main>
  );
}
