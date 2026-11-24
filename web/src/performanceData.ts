import type { AlpacaHistoryPoint, AlpacaPosition } from "./api";

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
