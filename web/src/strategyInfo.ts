import type { StrategyConfig, StrategyName } from "./api";

export const STRATEGIES: {
  name: StrategyName;
  title: string;
  trades: (c?: StrategyConfig) => string;
}[] = [
  {
    name: "news",
    title: "News + AI",
    trades: () => "Fresh watchlist news with an AI buy or sell signal",
  },
  {
    name: "momentum",
    title: "Intraday momentum",
    trades: (c) =>
      `30-minute breakouts outside the normal range in ${c?.symbols?.length ? c.symbols.join(" and ") : "the watchlist"}, long or short, flat by close`,
  },
  {
    name: "orb",
    title: "Opening range",
    trades: () =>
      "15-minute opening-range breakouts in the watchlist stocks with the most unusual volume, flat by close",
  },
  {
    name: "vwap_reversion",
    title: "VWAP reversion",
    trades: (c) =>
      `Stretches beyond 2 deviations of VWAP in ${c?.symbols?.length ? c.symbols.join(", ") : "watchlist stocks"}, faded back to VWAP`,
  },
  {
    name: "pullback",
    title: "RSI(2) pullback",
    trades: () => "Oversold watchlist stocks above their 200-day average, held until they bounce",
  },
  {
    name: "etf_trend",
    title: "ETF trend",
    trades: (c) => `Watchlist ETFs above their ${c?.sma_days ?? 200}-day average, held overnight`,
  },
  {
    name: "etf_rotation",
    title: "ETF rotation",
    trades: (c) => `Top ${c?.max_positions ?? 5} watchlist ETFs by 1- to 12-month momentum`,
  },
  {
    name: "cash_park",
    title: "Parked cash",
    trades: (c) =>
      `Idle cash not used by ETF strategies, held in ${c?.symbol ?? "a Treasury-bill ETF"}`,
  },
];
export const STRATEGY_TITLES = Object.fromEntries(
  STRATEGIES.map((s) => [s.name, s.title]),
) as Record<StrategyName, string>;
