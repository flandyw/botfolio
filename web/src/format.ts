import type { Group } from "./api";

export const GROUPS: Record<Group, string> = {
  pattern: "Strategy",
  inverse: "Inverse control",
  random: "Random control",
  benchmark: "Buy & hold",
};

const NAMES: Record<string, string> = {
  noise_mom: "Noise-band momentum",
  noise_qqq: "Noise-area momentum QQQ",
  noise_spy: "Noise-area momentum SPY",
  random: "Random entries",
};

/** inv_noise_mom_1m -> "Inverse · Noise-band momentum · 1m", trend_SPY -> "Trend hold · SPY". */
export function friendly(name: string): string {
  const inv = name.startsWith("inv_");
  const base = inv ? name.slice(4) : name;
  let label: string;
  if (base.startsWith("buy_hold_")) label = `Buy & hold · ${base.slice(9)}`;
  else if (base.startsWith("trend_")) label = `Trend hold · ${base.slice(6)}`;
  else {
    const m = base.match(/^(.+)_(\d+)m$/);
    label = m ? `${NAMES[m[1]] ?? m[1].replaceAll("_", " ")} · ${m[2]}m` : (NAMES[base] ?? base);
  }
  return inv ? `Inverse · ${label}` : label;
}

/** Distinguishable series colours, stable for a given ordering of names. */
const PALETTE = [
  "#4f8ff7",
  "#f5a524",
  "#17c964",
  "#f31260",
  "#9353d3",
  "#06b7db",
  "#ff7a45",
  "#a3e635",
  "#e879f9",
  "#94a3b8",
  "#facc15",
  "#2dd4bf",
];
/** One colour per ETF (so "Buy & hold · X" and "Trend hold · X" match); other portfolios get the rest. */
export function seriesColors(names: string[], symbols: string[]): Record<string, string> {
  const others = names.filter((n) => !symbols.some((s) => n.endsWith(`_${s}`)));
  return Object.fromEntries(
    names.map((n) => {
      const s = symbols.findIndex((sym) => n.endsWith(`_${sym}`));
      const i = s >= 0 ? s : symbols.length + others.indexOf(n);
      return [n, PALETTE[i % PALETTE.length]];
    }),
  );
}

export const compactUsd = (v: number) =>
  `${v < 0 ? "−" : "+"}$${new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(Math.abs(v))}`;

const nf = (d: number) =>
  new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
export const fmt = (v: number | null | undefined, d = 2) =>
  v == null || Number.isNaN(v) ? "—" : nf(d).format(v);
export const pct = (v: number | null | undefined, d = 1) =>
  v == null || Number.isNaN(v) ? "—" : `${v > 0 ? "+" : ""}${nf(d).format(v)}%`;
export const usd = (v: number | null | undefined, d = 0) =>
  v == null || Number.isNaN(v) ? "—" : `${v < 0 ? "−" : ""}$${nf(d).format(Math.abs(v))}`;
export const tone = (v: number | null | undefined) =>
  v == null || v === 0 ? "" : v > 0 ? "up" : "down";

/** Chart times are New York wall-clock encoded as UTC (see webapi.py), so format them in UTC. */
export const etTime = (t: number, withTime = true) =>
  new Date(t * 1000).toLocaleString("en-US", {
    timeZone: "UTC",
    year: "2-digit",
    month: "short",
    day: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit", hour12: false } : {}),
  });
