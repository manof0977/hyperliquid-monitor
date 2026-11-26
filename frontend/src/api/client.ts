export interface ScannerStatus {
  binance_connected: boolean;
  direct_connected?: boolean;
  bridge_connected?: boolean;
  feed_mode?: string;
  ws_connected: boolean;
  last_error: string | null;
  last_ok_ts: number | null;
  last_scan_ts: number | null;
  scanning: boolean;
  scan_progress: { done: number; total: number };
  eligible_contracts: number;
  total_scanned: number;
  long_candidates: number;
  short_candidates: number;
  confirmed_signals: number;
  developing_setups: number;
  early_watch_count: number;
}

export interface TradePlan {
  has_plan: boolean;
  opportunity_score: number;
  opp_breakdown?: Record<string, number>;
  zone_source?: string;
  entry_zone: [number, number] | null;
  entry: number | null;
  invalidation: number | null;
  targets: number[];
  rr: number;
  dist_atr?: number;
  in_zone?: boolean;
  ltf_confirmed?: boolean;
}

export interface ScanRow {
  symbol: string;
  base_asset?: string;
  price: number;
  change_24h: number;
  volume_24h: number;
  biases: Record<string, string>;
  long_tech_score: number;
  short_tech_score: number;
  long_opp_score: number;
  short_opp_score: number;
  long_tier?: string;
  short_tier?: string;
  long_breakdown?: Record<string, number>;
  short_breakdown?: Record<string, number>;
  long_plan?: TradePlan;
  short_plan?: TradePlan;
  active_side?: string;
  status: string;
  early_watch: boolean;
  early_reasons: string[];
  entry_zone?: [number, number] | null;
  entry?: number | null;
  invalidation?: number | null;
  targets?: number[];
  rr?: number;
  zone?: string;
}

export interface CandlePoint {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  ema20: number | null;
  ema50: number | null;
  ema100: number | null;
  ema200: number | null;
  rsi14: number | null;
}

export interface NextMoveMap {
  favored: 'BULLISH' | 'BEARISH' | 'BALANCED';
  bull_weight_pct: number;
  bear_weight_pct: number;
  lean_label: string;
  primary_magnet: string;
  bull_scenario: string;
  bear_scenario: string;
  positioning_read?: string | null;
  positioning_metrics?: {
    funding_rate_pct?: number;
    basis_pct?: number;
    open_interest_usd?: number;
    retail_long_pct?: number;
    retail_short_pct?: number;
    top_trader_long_pct?: number;
    top_trader_short_pct?: number;
  };
}

export interface ExplanationSection {
  title: string;
  summary: string;
  bullets: string[];
}

export interface ExplanationReport {
  symbol: string;
  tf: string;
  tone: 'bullish' | 'bearish' | 'warning' | 'neutral';
  headline: string;
  next_move_map?: NextMoveMap;
  sections: ExplanationSection[];
}

export interface CoinPayload {
  symbol: string;
  tf: string;
  candles: CandlePoint[];
  overlays: {
    structure: {
      trend: string;
      swings: { index: number; time: number; price: number; kind: string; label: string }[];
      breaks: { index: number; time: number; kind: string; direction: string; level: number; swing_time: number }[];
    };
    smc: {
      order_blocks: { kind: string; time: number; end_time: number; top: number; bottom: number; break_kind: string; mitigated: boolean }[];
      fvgs: { kind: string; time: number; end_time: number; top: number; bottom: number }[];
      sweeps: { kind: string; time: number; level: number; wick: number }[];
      equal_levels: { kind: string; price: number; t1: number; t2: number; swept: boolean }[];
      dealing_range: { high: number; low: number; eq: number; position: number } | null;
      zone: string;
    };
    sr: {
      supports: { price: number; source: string; touches: number; distance_pct: number; distance_atr: number }[];
      resistances: { price: number; source: string; touches: number; distance_pct: number; distance_atr: number }[];
      pdh: number | null;
      pdl: number | null;
      pwh: number | null;
      pwl: number | null;
    };
    ind: Record<string, number | string>;
    div: Record<string, boolean>;
  } | null;
  summary: ScanRow | null;
  sentiment?: Record<string, number> | null;
  explanation?: ExplanationReport | null;
}

export async function apiGet<T>(path: string): Promise<T> {
  const r = await fetch(path);
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export async function apiSend<T>(path: string, method: string, body?: unknown): Promise<T> {
  const r = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json' },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return r.json();
}

export function fmtPrice(p?: number | null): string {
  if (p === null || p === undefined || Number.isNaN(p)) return '—';
  const a = Math.abs(p);
  if (a >= 1000) return p.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (a >= 1) return p.toFixed(4);
  if (a >= 0.01) return p.toFixed(5);
  return p.toPrecision(4);
}

export function fmtVol(v?: number | null): string {
  if (!v) return '$0';
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  if (v >= 1e3) return `$${(v / 1e3).toFixed(1)}K`;
  return `$${v.toFixed(0)}`;
}
