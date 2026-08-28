export type DashboardMode = {
  trading_mode: string;
  live_trading_enabled: boolean;
  execution_available: boolean;
  paper_execution_available: boolean;
};

export type ActiveMarket = {
  condition_id: string;
  slug?: string | null;
  question?: string | null;
  horizon_seconds?: number | null;
  start_at?: string | null;
  end_at?: string | null;
  calibrated_probability?: number | null;
  raw_probability?: number | null;
  predicted_side?: string | null;
  market_probability?: number | null;
  up_best_bid?: number | null;
  up_best_ask?: number | null;
  down_best_bid?: number | null;
  down_best_ask?: number | null;
  selected_side?: string | null;
  selected_ask?: number | null;
  selected_bid?: number | null;
  selected_spread?: number | null;
  raw_edge?: number | null;
  cost_adjusted_edge?: number | null;
  decision_reason?: string | null;
  executable?: boolean | null;
  trade?: boolean | null;
  scheduled_at?: string | null;
  recorded_at?: string | null;
};

export type FeedHealth = {
  source: string;
  stream: string;
  status?: string | null;
  last_received_at?: string | null;
  last_source_timestamp?: string | null;
  updated_at?: string | null;
  age_seconds?: number | null;
  details?: Record<string, unknown> | null;
};

export type CalibrationBucket = {
  label: string;
  count: number;
  mean_probability?: number | null;
  observed_up_rate?: number | null;
};

export type PerformanceRow = {
  horizon_seconds: number;
  total_predictions: number;
  evaluated_predictions: number;
  coverage?: number | null;
  accuracy?: number | null;
  calibrated_brier?: number | null;
  calibrated_log_loss?: number | null;
  calibration_buckets: CalibrationBucket[];
};

export type PredictionHistoryRow = {
  prediction_id: string;
  condition_id?: string | null;
  slug?: string | null;
  horizon_seconds?: number | null;
  scheduled_at?: string | null;
  recorded_at?: string | null;
  calibrated_probability?: number | null;
  predicted_side?: string | null;
  market_probability?: number | null;
  selected_side?: string | null;
  selected_ask?: number | null;
  selected_bid?: number | null;
  selected_spread?: number | null;
  raw_edge?: number | null;
  cost_adjusted_edge?: number | null;
  decision_reason?: string | null;
  executable?: boolean | null;
  trade?: boolean | null;
  official_outcome?: string | null;
  official_target?: number | null;
  evaluated_at?: string | null;
  correct?: boolean | null;
  calibrated_brier?: number | null;
  calibrated_log_loss?: number | null;
};

export type PaperReconciliation = {
  status: string;
  violation_count: number;
  violations?: Record<string, number>;
  paper_order_count?: number;
  trade_signal_count?: number;
  no_trade_signal_count?: number;
};

export type PaperPnl = {
  status: string;
  value?: number | null;
  starting_cash?: number | null;
  current_cash?: number | null;
  open_capital?: number | null;
  unrealized_value?: number | null;
  realized_pnl?: number | null;
  return_on_starting_cash?: number | null;
  max_realized_equity_drawdown?: number | null;
  settled_trade_count?: number | null;
  open_position_count?: number | null;
  fill_count?: number | null;
  no_fill_expired_count?: number | null;
  total_fees?: number | null;
  total_slippage_cost?: number | null;
  reconciliation?: PaperReconciliation | null;
};

export type PaperOrderRow = {
  paper_order_id: string;
  prediction_id?: string | null;
  condition_id?: string | null;
  token_id?: string | null;
  selected_side?: string | null;
  requested_shares?: number | null;
  target_notional_usd?: number | null;
  submitted_at?: string | null;
  arrival_at?: string | null;
  expires_at?: string | null;
  limit_price?: number | null;
  signal_selected_ask?: number | null;
  signal_fee_rate?: number | null;
  signal_slippage_buffer?: number | null;
  terminal_status?: string | null;
  remaining_shares?: number | null;
  terminal_event_at?: string | null;
  realized_pnl?: number | null;
};

export type PaperFillRow = {
  paper_order_id: string;
  fill_key?: string | null;
  fill_at?: string | null;
  shares?: number | null;
  price?: number | null;
  gross_cost?: number | null;
  fee?: number | null;
  total_cost?: number | null;
  signal_ask_slippage?: number | null;
  replay_cutoff_at?: string | null;
};

export type PaperSettlementRow = {
  paper_order_id: string;
  label_version?: string | null;
  official_outcome?: string | null;
  filled_shares?: number | null;
  total_fill_cost?: number | null;
  total_fees?: number | null;
  payout?: number | null;
  realized_pnl?: number | null;
  settled_at?: string | null;
};

export type DashboardSnapshot = {
  generated_at: string;
  mode: DashboardMode;
  active_markets: ActiveMarket[];
  feed_health: FeedHealth[];
  performance: PerformanceRow[];
  prediction_history: PredictionHistoryRow[];
  paper_pnl: PaperPnl;
  paper_orders: PaperOrderRow[];
  paper_fills: PaperFillRow[];
  paper_settlements: PaperSettlementRow[];
};

const LOOPBACK_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);

export function getDashboardApiUrl(): string {
  const raw =
    process.env.BP_DASHBOARD_API_URL ??
    "http://127.0.0.1:8787/api/v1/snapshot";
  const url = new URL(raw);
  if (!LOOPBACK_HOSTS.has(url.hostname)) {
    throw new Error("BP dashboard API URL must use loopback only");
  }
  if (url.protocol !== "http:") {
    throw new Error("BP dashboard API URL must use local HTTP");
  }
  return url.toString();
}

function isDashboardSnapshot(value: unknown): value is DashboardSnapshot {
  if (!value || typeof value !== "object") {
    return false;
  }
  const row = value as Partial<DashboardSnapshot>;
  return (
    typeof row.generated_at === "string" &&
    !!row.mode &&
    typeof row.mode === "object" &&
    Array.isArray(row.active_markets) &&
    Array.isArray(row.feed_health) &&
    Array.isArray(row.performance) &&
    Array.isArray(row.prediction_history) &&
    !!row.paper_pnl &&
    typeof row.paper_pnl === "object" &&
    Array.isArray(row.paper_orders) &&
    Array.isArray(row.paper_fills) &&
    Array.isArray(row.paper_settlements)
  );
}

export async function fetchSnapshot(): Promise<DashboardSnapshot> {
  const response = await fetch(getDashboardApiUrl(), {
    cache: "no-store",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error(`dashboard API returned ${response.status}`);
  }
  const payload: unknown = await response.json();
  if (!isDashboardSnapshot(payload)) {
    throw new Error("dashboard API returned an invalid snapshot");
  }
  return payload;
}
