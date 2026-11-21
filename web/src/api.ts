export type Run = {
  run_id: string;
  mode: string;
  created_at: string;
  start: string | null;
  end: string | null;
  status: string | null;
  notes: string | null;
};
export type Group = "pattern" | "inverse" | "random" | "benchmark";
export type Stat = {
  group: Group;
  n_trades: number;
  win_rate: number | null;
  profit_factor: number | null;
  avg_return_pct: number | null;
  avg_cost_pct: number | null;
  total_return_pct: number | null;
  sharpe: number | null;
  max_drawdown_pct: number | null;
  final_equity: number | null;
  q_mc?: number | null;
  p_ttest?: number | null;
  verdict: string;
};
export type Point = { time: number; value: number };
export type Bar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};
export type Trade = {
  trade_id: string;
  portfolio: string;
  symbol: string;
  side: number;
  qty: number;
  entry_time: number;
  exit_time: number;
  entry_price: number;
  exit_price: number;
  gross_pnl: number;
  costs: number;
  net_pnl: number;
  return_pct: number;
  exit_reason: string;
  meta: Record<string, unknown>;
};
export type Position = {
  portfolio: string;
  symbol: string;
  side: number;
  qty: number;
  entry_price: number;
  entry_time: number;
  last: number;
  stop: number | null;
  target: number | null;
  dividends: number;
  unrealised: number;
};
export type Live = {
  now: string;
  heartbeat: string | null;
  heartbeat_age_s: number | null;
  market_open: boolean;
  session: { open: string; close: string } | null;
  equity: Record<string, number>;
  positions: Position[];
  tax_due: Record<string, number>;
};
export type Config = {
  symbols: string[];
  starting_capital: number;
  tax_rate_pct: number;
  withholding_pct: number;
  feed: string;
};

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`/api/${path}`);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json() as Promise<T>;
}
