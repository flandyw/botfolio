export type Point = { time: number; value: number };
export type Bar = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

export type Live = {
  heartbeat: string | null;
  heartbeat_age_s: number | null;
  execution_age_s: number | null;
  market_open: boolean | null;
  phase: string | null;
  enabled: boolean;
  armed: boolean;
  open_intents: number | null;
  strategies: Partial<Record<StrategyName, StrategyStatus>>;
  strategy_config: Record<StrategyName, StrategyConfig>;
  news_check: { at: string; ticker: string; result: string } | null;
  intents: Intent[];
};

export type StrategyName = "news" | "momentum" | "etf_trend";

export type StrategyStatus = {
  at?: string;
  note?: string | null;
  orders?: { symbol: string; side: string; result: string }[];
};

export type StrategyConfig = {
  enabled: boolean;
  max_positions: number;
  position_pct: number;
  risk_pct?: number;
  allocation_pct?: number;
  sma_days?: number;
};

/** An order or position the worker is managing, and the strategy that owns it. */
export type Intent = {
  symbol: string;
  strategy: StrategyName;
  side: string;
  qty: number;
  state: string;
  opened?: string;
  exit_requested?: string;
};

export type Config = {
  symbols: string[];
  etfs: string[];
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
