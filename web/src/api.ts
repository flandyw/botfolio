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
  /** The execution monitor's last error, if any. */
  message?: string | null;
  enabled: boolean;
  armed: boolean;
  /** The emergency stop is active. */
  stopped: boolean;
  worker_ready: boolean;
  news_fresh: boolean;
  provider_failures: boolean;
  issuer_feed_failures: boolean;
  ai_ready: boolean;
  open_intents: number | null;
  strategies: Partial<Record<StrategyName, StrategyStatus>>;
  strategy_config: Record<StrategyName, StrategyConfig>;
  news_check: { at: string; ticker: string; result: string } | null;
  intents: Intent[];
};

export type AiModelUsage = { calls: number; failures: number; input: number; output: number; latency: number };

export type AiUsageDay = {
  day: string;
  neurons: number;
  cf_usd: number;
  vercel_usd: number;
  models: Record<string, AiModelUsage>;
};

export type AiUsage = {
  settings: {
    primary_provider: string;
    primary_model: string;
    secondary_ready: boolean;
    secondary_model: string;
    share: number | "auto";
    monthly_usd: number;
  };
  free_neurons: number;
  max_monthly_usd: number;
  budget: {
    monthly_usd: number;
    month_usd: number;
    today_usd: number;
    per_day: number;
    pace: number;
    neurons: number;
    rpm: number;
    balance: number | null;
    throttled: { at: string; status: number; until: string } | null;
  };
  options: { primary: string[]; secondary: string[] };
  days: AiUsageDay[];
};

export type StrategyName = "news" | "momentum" | "etf_trend" | "etf_rotation" | "cash_park";

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
  symbol?: string;
  symbols?: string[];
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
  max_symbols: number;
  password_required: boolean;
};

/** One row of the worker's order journal. */
export type JournalRow = {
  key: string;
  strategy: StrategyName;
  symbol: string;
  side?: string;
  qty?: number;
  limit?: number;
  stop?: number | null;
  state: string;
  broker_status?: string;
  opened?: string;
  exit_requested?: string;
  error?: string;
};

export type NewsItem = {
  key: string;
  ticker: string;
  source: string;
  url: string;
  published_at: string;
  received_at: string;
  text: string;
  truncated: boolean;
  decision: {
    action: string;
    reasoning: string;
    evidence: string[];
    risks: string[];
    model: string;
    experiment: string;
    at: string;
  } | null;
};

export type News = {
  tickers: string[];
  sources: string[];
  total: number;
  archived: boolean;
  queued: string[];
  unassessed: number;
  last_assessed: string | null;
  items: NewsItem[];
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

const PASSWORD_KEY = "botfolio-password";

export function savedPassword() {
  try {
    return sessionStorage.getItem(PASSWORD_KEY) ?? "";
  } catch {
    return "";
  }
}

export function savePassword(password: string) {
  try {
    sessionStorage.setItem(PASSWORD_KEY, password);
  } catch {
    // storage is unavailable; the password lasts until the page reloads
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

/** Fired when the saved password is refused, e.g. after it was changed on the server. */
export const LOCKED = "botfolio-locked";

async function call<T>(path: string, init: RequestInit, given?: string): Promise<T> {
  const password = given ?? savedPassword();
  const headers = { ...(init.headers as Record<string, string>), ...(password && { "X-Botfolio-Password": password }) };
  const r = await fetch(`/api/${path}`, { ...init, headers });
  const text = await r.text();
  if (!r.ok) {
    if (r.status === 401 && given === undefined) {
      savePassword("");
      dispatchEvent(new Event(LOCKED));
    }
    let message = text || r.statusText;
    try {
      message = (JSON.parse(text) as { error?: string }).error ?? message;
    } catch {
      // not JSON; show the body as it is
    }
    throw new ApiError(r.status, message);
  }
  return JSON.parse(text) as T;
}

export const get = <T,>(path: string, password?: string) => call<T>(path, {}, password);

/** A change to the bot: arming, pausing, stopping or saving the watchlist. */
export const send = <T,>(method: "POST" | "PUT", path: string, body: unknown = {}) =>
  call<T>(path, {
    method,
    headers: { "Content-Type": "application/json", "X-Botfolio": "1" },
    body: JSON.stringify(body),
  });
