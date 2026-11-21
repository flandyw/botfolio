const nf = (d: number) =>
  new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
export const fmt = (v: number | null | undefined, d = 2) =>
  v == null || Number.isNaN(v) ? "—" : nf(d).format(v);
export const pct = (v: number | null | undefined, d = 1) =>
  v == null || Number.isNaN(v) ? "—" : `${v > 0 ? "+" : ""}${nf(d).format(v)}%`;
export const usd = (v: number | null | undefined, d = 0) =>
  v == null || Number.isNaN(v) ? "—" : `${v < 0 ? "−" : ""}$${nf(d).format(Math.abs(v))}`;

export const money = usd;
