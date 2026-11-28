import type { Bar, Point } from "./api";

export function calcSma(bars: Bar[], period: number): Point[] {
  const out: Point[] = [];
  let sum = 0;
  for (let i = 0; i < bars.length; i++) {
    sum += bars[i].close;
    if (i >= period) sum -= bars[i - period].close;
    if (i >= period - 1) {
      out.push({ time: bars[i].time, value: sum / period });
    }
  }
  return out;
}

export function calcEma(bars: Bar[], period: number): Point[] {
  if (bars.length < period) return [];
  const k = 2 / (period + 1);
  let ema = 0;
  for (let i = 0; i < period; i++) ema += bars[i].close;
  ema /= period;
  const out: Point[] = [{ time: bars[period - 1].time, value: ema }];
  for (let i = period; i < bars.length; i++) {
    ema = bars[i].close * k + ema * (1 - k);
    out.push({ time: bars[i].time, value: ema });
  }
  return out;
}

/** Session-resetting intraday VWAP (resets when the UTC date of NY wall-clock changes). */
export function calcVwap(bars: Bar[]): Point[] {
  const out: Point[] = [];
  let lastDay = -1;
  let cumPv = 0;
  let cumVol = 0;
  for (const b of bars) {
    const day = Math.floor(b.time / 86400);
    if (day !== lastDay) {
      lastDay = day;
      cumPv = 0;
      cumVol = 0;
    }
    const tp = (b.high + b.low + b.close) / 3;
    cumPv += tp * b.volume;
    cumVol += b.volume;
    out.push({ time: b.time, value: cumVol > 0 ? cumPv / cumVol : b.close });
  }
  return out;
}

export function calcBollinger(bars: Bar[], period = 20, mult = 2) {
  const upper: Point[] = [];
  const lower: Point[] = [];
  const mid: Point[] = [];
  for (let i = period - 1; i < bars.length; i++) {
    let sum = 0;
    for (let j = i - period + 1; j <= i; j++) sum += bars[j].close;
    const mean = sum / period;
    let sq = 0;
    for (let j = i - period + 1; j <= i; j++) {
      const d = bars[j].close - mean;
      sq += d * d;
    }
    const sd = Math.sqrt(sq / period);
    const t = bars[i].time;
    mid.push({ time: t, value: mean });
    upper.push({ time: t, value: mean + mult * sd });
    lower.push({ time: t, value: mean - mult * sd });
  }
  return { upper, mid, lower };
}

/** Wilder's RSI(14). */
export function calcRsi(bars: Bar[], period = 14): Point[] {
  if (bars.length <= period) return [];
  let gain = 0;
  let loss = 0;
  for (let i = 1; i <= period; i++) {
    const diff = bars[i].close - bars[i - 1].close;
    if (diff >= 0) gain += diff;
    else loss -= diff;
  }
  let avgGain = gain / period;
  let avgLoss = loss / period;
  const out: Point[] = [];
  const rsiVal = (g: number, l: number) => (l === 0 ? 100 : 100 - 100 / (1 + g / l));
  out.push({ time: bars[period].time, value: rsiVal(avgGain, avgLoss) });
  for (let i = period + 1; i < bars.length; i++) {
    const diff = bars[i].close - bars[i - 1].close;
    const g = diff > 0 ? diff : 0;
    const l = diff < 0 ? -diff : 0;
    avgGain = (avgGain * (period - 1) + g) / period;
    avgLoss = (avgLoss * (period - 1) + l) / period;
    out.push({ time: bars[i].time, value: rsiVal(avgGain, avgLoss) });
  }
  return out;
}

/** MACD(12, 26, 9): the fast/slow EMA spread, its signal line and their difference. */
export function calcMacd(bars: Bar[], fast = 12, slow = 26, signal = 9) {
  const slowEma = calcEma(bars, slow);
  const fastAt = new Map(calcEma(bars, fast).map((p) => [p.time, p.value]));
  const macd = slowEma.map((p) => ({ time: p.time, value: fastAt.get(p.time)! - p.value }));
  const k = 2 / (signal + 1);
  const signalLine: Point[] = [];
  let ema = 0;
  macd.forEach((p, i) => {
    if (i < signal) ema += p.value / signal;
    else ema = p.value * k + ema * (1 - k);
    if (i >= signal - 1) signalLine.push({ time: p.time, value: ema });
  });
  const signalAt = new Map(signalLine.map((p) => [p.time, p.value]));
  const histogram = macd.flatMap((p) =>
    signalAt.has(p.time) ? [{ time: p.time, value: p.value - signalAt.get(p.time)! }] : [],
  );
  return { macd, signal: signalLine, histogram };
}

/** Wilder's average true range. */
export function calcAtr(bars: Bar[], period = 14): Point[] {
  const out: Point[] = [];
  let atr = 0;
  for (let i = 1; i < bars.length; i++) {
    const prev = bars[i - 1].close;
    const range = Math.max(bars[i].high - bars[i].low, Math.abs(bars[i].high - prev), Math.abs(bars[i].low - prev));
    if (i <= period) {
      atr += range / period;
      if (i === period) out.push({ time: bars[i].time, value: atr });
    } else {
      atr = (atr * (period - 1) + range) / period;
      out.push({ time: bars[i].time, value: atr });
    }
  }
  return out;
}

/** The close before the session a chart ends on. Before the open Alpaca's latest daily bar is still the prior
 *  session, so its close is the reference; once today's bar exists the reference is the bar before it. */
export function previousClose(
  snap: { prevClose: number | null; dailyClose: number | null; dailyDay: string | null } | undefined,
  lastBarTime: number,
): number | null {
  if (!snap) return null;
  const plotted = new Date(lastBarTime * 1000).toISOString().slice(0, 10);
  return snap.dailyDay && lastBarTime && plotted > snap.dailyDay ? snap.dailyClose : snap.prevClose;
}
