import type { AlpacaHistoryPoint, AlpacaPosition, StrategyPerformance } from "./api";

export type PerformancePoint = { time: number; value: number | null };

export function portfolioPerformance(points: AlpacaHistoryPoint[]) {
  const funded = points.findIndex((point) => point.equity != null && point.equity !== 0);
  const lastObserved = points.findLastIndex((point) => point.equity != null);
  const history = funded < 0 ? [] : points.slice(funded, lastObserved + 1);
  const pnl: PerformancePoint[] = [];
  const returns: PerformancePoint[] = [];
  const changes: PerformancePoint[] = [];
  const drawdown: PerformancePoint[] = [];
  let previousPnl: number | null = null;
  let peak = 0;
  let maxDrawdown: number | null = null;

  for (const { time, equity, profitLoss, profitLossPct } of history) {
    if (equity != null) peak = Math.max(peak, equity);
    const fromPeak = equity != null && peak > 0 ? ((equity - peak) / peak) * 100 : null;
    if (fromPeak != null) maxDrawdown = Math.min(maxDrawdown ?? 0, fromPeak);

    pnl.push({ time, value: profitLoss });
    returns.push({ time, value: profitLossPct });
    changes.push({
      time,
      value: profitLoss != null && previousPnl != null ? profitLoss - previousPnl : null,
    });
    drawdown.push({ time, value: fromPeak });
    previousPnl = profitLoss;
  }

  return { latest: history.at(-1), pnl, returns, changes, drawdown, maxDrawdown };
}

export function openPositionPerformance(positions: AlpacaPosition[]) {
  let pnl = 0;
  let long = 0;
  let short = 0;
  const rows = positions.map((position) => {
    const marketValue = Math.abs(position.marketValue);
    const isShort = position.side === "short";
    if (isShort) short += marketValue;
    else long += marketValue;
    pnl += position.unrealizedPl;
    return {
      symbol: position.symbol,
      side: position.side,
      pnl: position.unrealizedPl,
      unrealizedPlpc: position.unrealizedPlpc,
      exposure: isShort ? -marketValue : marketValue,
    };
  });

  return { rows, pnl, long, short };
}

type StrategyResult = NonNullable<StrategyPerformance[keyof StrategyPerformance]>;

/** Closed-trade quality for one strategy. Ratios are null until there are trades to divide by. */
export function tradeQuality(s: StrategyResult) {
  const avgWin = s.wins ? s.gross_win / s.wins : null;
  const avgLoss = s.losses ? s.gross_loss / s.losses : null;
  return {
    winRate: s.closed ? (s.wins / s.closed) * 100 : null,
    avgWin,
    avgLoss,
    expectancy: s.closed ? (s.gross_win + s.gross_loss) / s.closed : null,
    profitFactor: s.gross_loss < 0 ? s.gross_win / -s.gross_loss : null,
  };
}

/** Counts of trade returns (in percent) in equal-width bins centred on zero. */
export function returnBins(returns: number[], bins = 12) {
  if (!returns.length) return [];
  const reach = Math.max(...returns.map(Math.abs)) || 1;
  const width = (2 * reach) / bins;
  const out = Array.from({ length: bins }, (_, i) => ({ from: -reach + i * width, to: -reach + (i + 1) * width, count: 0 }));
  for (const r of returns) out[Math.min(bins - 1, Math.floor((r + reach) / width))].count++;
  return out;
}

const etDay = new Intl.DateTimeFormat("en-CA", { timeZone: "America/New_York" });

/** Each session's P&L change from daily history, grouped into Monday-to-Friday weeks, oldest first. */
export function pnlCalendar(points: AlpacaHistoryPoint[]) {
  const weeks: { monday: string; days: ({ date: string; pnl: number; pct: number | null } | null)[] }[] = [];
  let previous: AlpacaHistoryPoint | null = null;
  for (const point of points) {
    if (point.equity == null || point.equity === 0 || point.profitLoss == null) {
      previous = null; // a missing observation leaves a gap; the next cell must not span several sessions
      continue;
    }
    if (previous) {
      const date = etDay.format(new Date(point.time * 1000));
      const day = new Date(`${date}T12:00:00Z`);
      const weekday = (day.getUTCDay() + 6) % 7;
      if (weekday < 5) {
        const monday = new Date(day.getTime() - weekday * 86_400_000).toISOString().slice(0, 10);
        if (weeks.at(-1)?.monday !== monday) weeks.push({ monday, days: [null, null, null, null, null] });
        const pnl = point.profitLoss - previous.profitLoss!;
        weeks.at(-1)!.days[weekday] = { date, pnl, pct: previous.equity ? (pnl / previous.equity) * 100 : null };
      }
    }
    previous = point;
  }
  return weeks;
}
