import type { JournalRow, TradeActivity } from "./api";

export type TradeStats = {
  pnl: number | null;
  returnPct: number | null;
  rMultiple: number | null;
  plannedRR: number | null;
  slippage: number | null;
  holdSeconds: number | null;
  mfe: number | null;
  mae: number | null;
  mfeR: number | null;
  maeR: number | null;
  capture: number | null;
};

const finite = (v: number) => (Number.isFinite(v) ? v : null);

/** Per-trade analytics from the broker fills and the candles around them. Prices are per share; P&L is gross. */
export function tradeStats(row: JournalRow, data?: TradeActivity, now = Date.now() / 1000): TradeStats {
  const entry = data?.entry;
  const dir = row.side === "long" ? 1 : row.side === "short" ? -1 : 0;
  const empty: TradeStats = {
    pnl: null, returnPct: null, rMultiple: null, plannedRR: null, slippage: null,
    holdSeconds: null, mfe: null, mae: null, mfeR: null, maeR: null, capture: null,
  };
  if (!entry || !dir || !data) return empty;

  const exits = data.exits;
  const closedQty = exits.reduce((sum, fill) => sum + fill.qty, 0);
  const pnl =
    exits.length && closedQty <= entry.qty + 1e-8
      ? exits.reduce((sum, exit) => sum + (exit.price - entry.price) * exit.qty, 0) * dir
      : null;
  const risk = row.stop != null ? Math.abs(entry.price - row.stop) : 0;
  const rMultiple = pnl != null && risk > 0 ? pnl / (risk * closedQty) : null;
  const plannedRR =
    row.stop != null && row.target != null && risk > 0 ? Math.abs(row.target - entry.price) / risk : null;
  // Positive slippage means the fill was worse than the limit.
  const slippage = row.limit != null ? (entry.price - row.limit) * dir : null;

  const closed = closedQty + 1e-8 >= entry.qty;
  const exitTimes = exits.map((fill) => fill.time).filter((t): t is number => t != null);
  const end = closed ? (exitTimes.length ? Math.max(...exitTimes) : null) : now;
  const holdSeconds = entry.time != null && end != null ? Math.max(0, end - entry.time) : null;

  let mfe: number | null = null;
  let mae: number | null = null;
  if (entry.time != null && end != null) {
    const size = data.bar_seconds;
    const from = Math.floor(entry.time / size) * size;
    const held = data.bars.filter((bar) => bar.time >= from && bar.time <= end);
    if (held.length) {
      const high = Math.max(...held.map((bar) => bar.high));
      const low = Math.min(...held.map((bar) => bar.low));
      mfe = Math.max(0, dir > 0 ? high - entry.price : entry.price - low);
      mae = Math.max(0, dir > 0 ? entry.price - low : high - entry.price);
    }
  }
  const realizedMove = pnl != null && closedQty > 0 ? pnl / closedQty : null;

  return {
    pnl,
    returnPct: pnl != null && closedQty > 0 ? finite((pnl / (entry.price * closedQty)) * 100) : null,
    rMultiple,
    plannedRR,
    slippage,
    holdSeconds,
    mfe,
    mae,
    mfeR: mfe != null && risk > 0 ? mfe / risk : null,
    maeR: mae != null && risk > 0 ? mae / risk : null,
    capture: realizedMove != null && mfe ? (realizedMove / mfe) * 100 : null,
  };
}

export function duration(seconds: number | null) {
  if (seconds == null) return "—";
  const m = Math.round(seconds / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  if (h < 48) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}
