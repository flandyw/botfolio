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

/* --- Alpaca JS SDK Bridge (/api/alpaca/*) types --- */

export type AlpacaClock = {
  isOpen: boolean;
  timestamp: string | null;
  nextOpen: string | null;
  nextClose: string | null;
};

export type AlpacaStatus = {
  configured: boolean;
  connected: boolean;
  paper: boolean;
  feed: string;
  sdk: string;
  streamClients?: number;
  streamConnected?: boolean;
  accountNumber: string | null;
  clock: AlpacaClock | null;
  error: string | null;
};

export type AlpacaSnapshot = {
  symbol: string;
  price: number | null;
  prevClose: number | null;
  change: number | null;
  changePct: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  volume: number | null;
  vwap: number | null;
  bid: number | null;
  ask: number | null;
  spread: number | null;
  updatedAt: string | null;
};

export type AlpacaSnapshotsResponse = {
  feed: string;
  snapshots: AlpacaSnapshot[];
};

export type AlpacaAccount = {
  accountNumber: string;
  status: string;
  equity: number | null;
  lastEquity: number | null;
  cash: number | null;
  buyingPower: number | null;
  portfolioValue: number | null;
  longMarketValue: number | null;
  shortMarketValue: number | null;
  dayPnl: number | null;
  dayPnlPct: number | null;
  daytradeCount: number;
  patternDayTrader: boolean;
};

export type AlpacaPosition = {
  symbol: string;
  side: string;
  qty: number;
  avgEntryPrice: number;
  currentPrice: number;
  marketValue: number;
  unrealizedPl: number;
  unrealizedPlpc: number;
  changeToday: number;
};

export type AlpacaOrder = {
  id: string;
  symbol: string;
  side: string;
  type: string;
  qty: number;
  filledAvgPrice: number | null;
  status: string;
  submittedAt: string | null;
  filledAt: string | null;
};

export type AlpacaAccountResponse = {
  account: AlpacaAccount;
  clock: AlpacaClock | null;
  positions: AlpacaPosition[];
  orders: AlpacaOrder[];
};

export type AlpacaHistoryPoint = {
  time: number;
  equity: number;
  profitLoss: number | null;
  profitLossPct: number | null;
};

export type AlpacaPortfolioHistory = {
  period: string;
  timeframe: string;
  baseValue: number | null;
  points: AlpacaHistoryPoint[];
};

export type AlpacaStreamBar = Bar & {
  symbol: string;
  vwap?: number | null;
};

export async function get<T>(path: string): Promise<T> {
  const r = await fetch(`/api/${path}`);
  const text = await r.text();
  if (!r.ok) throw new Error(text || r.statusText);
  return JSON.parse(text) as T;
}
