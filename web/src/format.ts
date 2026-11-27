const nf = (d: number) =>
  new Intl.NumberFormat("en-US", { maximumFractionDigits: d, minimumFractionDigits: d });
const missing = (v: number | null | undefined): v is null | undefined => v == null || Number.isNaN(v);

export const num = (v: number | null | undefined, d = 2) => (missing(v) ? "—" : nf(d).format(v));
export const usd = (v: number | null | undefined, d = 2) =>
  missing(v) ? "—" : `${v < 0 ? "−" : ""}$${nf(d).format(Math.abs(v))}`;
export const signedUsd = (v: number | null | undefined, d = 2) =>
  missing(v) ? "—" : `${v < 0 ? "−" : "+"}$${nf(d).format(Math.abs(v))}`;
export const signed = (v: number | null | undefined, d = 2) =>
  missing(v) ? "—" : `${v < 0 ? "−" : "+"}${nf(d).format(Math.abs(v))}`;
export const pct = (v: number | null | undefined, d = 2) => (missing(v) ? "—" : `${signed(v, d)}%`);

const compactFormat = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
export const compact = (v: number | null | undefined) => (missing(v) ? "—" : compactFormat.format(v));

/** Text color for a gain or loss; zero and missing values stay neutral. */
export const tone = (v: number | null | undefined) =>
  missing(v) || v === 0 ? "" : v > 0 ? "text-up" : "text-down";

export function ago(seconds: number | null | undefined) {
  if (missing(seconds) || seconds < 0) return "—";
  if (seconds < 90) return `${Math.round(seconds)}s ago`;
  if (seconds < 5400) return `${Math.round(seconds / 60)}m ago`;
  if (seconds < 172800) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

const eastern = (options: Intl.DateTimeFormatOptions) =>
  new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", hour12: false, ...options });
const clockTime = eastern({ hour: "2-digit", minute: "2-digit" });
const dayTime = eastern({ weekday: "short", hour: "2-digit", minute: "2-digit" });
const dateTime = eastern({ month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const executionTime = eastern({ month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });

/** New York time, the exchange's clock. */
export const etTime = (iso: string | null | undefined) => (iso ? clockTime.format(new Date(iso)) : "—");
export const etDayTime = (iso: string | null | undefined) => (iso ? dayTime.format(new Date(iso)).replace(",", "") : "—");
export const etDateTime = (iso: string | null | undefined) => (iso ? dateTime.format(new Date(iso)) : "—");
export const etExecutionTime = (iso: string) => executionTime.format(new Date(iso));
