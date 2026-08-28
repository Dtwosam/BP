export type DashboardSnapshotLike = {
  generated_at: string;
  active_markets: readonly unknown[];
  feed_health: readonly {
    status?: string | null;
    age_seconds?: number | null;
  }[];
  performance: readonly {
    evaluated_predictions?: number | null;
  }[];
  paper_pnl: {
    status?: string | null;
    value?: number | null;
  };
};

export type SnapshotFreshness = "fresh" | "stale" | "error";

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || !Number.isFinite(value)) {
    return "—";
  }
  return `${(value * 100).toFixed(1)}%`;
}

export function buildKpis(snapshot: DashboardSnapshotLike) {
  const healthyFeeds = snapshot.feed_health.filter(
    (feed) => feed.status?.toLowerCase() === "connected",
  ).length;
  const evaluatedPredictions = snapshot.performance.reduce(
    (total, row) => total + (row.evaluated_predictions ?? 0),
    0,
  );

  return {
    activeMarkets: snapshot.active_markets.length,
    healthyFeeds,
    totalFeeds: snapshot.feed_health.length,
    evaluatedPredictions,
    paperPnl:
      snapshot.paper_pnl.status === "UNAVAILABLE_UNTIL_PHASE_12"
        ? "Unavailable until Phase 12"
        : snapshot.paper_pnl.value === null || snapshot.paper_pnl.value === undefined
          ? "—"
          : String(snapshot.paper_pnl.value),
  };
}

export function classifySnapshotFreshness(
  generatedAt: string,
  now: Date,
  refreshError: boolean,
): SnapshotFreshness {
  if (refreshError) {
    return "error";
  }
  const generatedMs = Date.parse(generatedAt);
  if (!Number.isFinite(generatedMs)) {
    return "stale";
  }
  return now.getTime() - generatedMs > 60_000 ? "stale" : "fresh";
}
