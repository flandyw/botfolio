import { useEffect, useMemo, useRef, useState } from "react";
import type { AlpacaSnapshot, AlpacaStreamBar, Bar } from "./api";
import { LINES } from "./chart";
import { cell, cx } from "./classes";
import { compact, num, pct, signed, tone } from "./format";
import { type ChartOverlays, PriceChart } from "./PriceChart";
import { Choice, Message } from "./ui";
import { useApi } from "./useApi";

const TIMEFRAMES = [
  ["1", "1m"],
  ["5", "5m"],
  ["15", "15m"],
  ["60", "1h"],
  ["D", "1d"],
] as const;
type Timeframe = (typeof TIMEFRAMES)[number][0];
const RANGES = [
  [1, "1D"],
  [5, "5D"],
  [30, "1M"],
  [90, "3M"],
  [365, "1Y"],
  [730, "2Y"],
] as const;
const INDICATORS: [keyof ChartOverlays, string][] = [
  ["sma20", "SMA 20"],
  ["ema50", "EMA 50"],
  ["vwap", "VWAP"],
  ["bb", "Bollinger 20, 2"],
  ["rsi", "RSI 14"],
];
const GROUPS = [
  ["all", "All"],
  ["etf", "ETFs"],
  ["stock", "Stocks"],
] as const;
type Group = (typeof GROUPS)[number][0];
type SortKey = "symbol" | "price" | "change" | "volume";
type Row = { symbol: string; etf: boolean; price: number | null; change: number | null; volume: number | null };

const COLUMNS: [SortKey, string, boolean][] = [
  ["symbol", "Symbol", false],
  ["price", "Last", true],
  ["change", "Day %", true],
  ["volume", "Volume", true],
];

export function Markets({
  symbols,
  etfs,
  symbol,
  onSymbol,
  connected,
  snapshots,
  latestBars,
  streaming,
}: {
  symbols: string[];
  etfs: string[];
  symbol: string;
  onSymbol: (s: string) => void;
  connected: boolean;
  snapshots: AlpacaSnapshot[];
  latestBars: Record<string, AlpacaStreamBar>;
  streaming: boolean;
}) {
  const [filter, setFilter] = useState("");
  const [group, setGroup] = useState<Group>("all");
  const [sort, setSort] = useState<{ key: SortKey; down: boolean } | null>(null);
  const [timeframe, setTimeframe] = useState<Timeframe>("5");
  const [days, setDays] = useState(30);
  const [overlays, setOverlays] = useState<ChartOverlays>({
    sma20: true,
    ema50: false,
    vwap: true,
    bb: false,
    rsi: false,
  });

  const rows = useMemo(() => {
    const bySymbol = new Map(snapshots.map((s) => [s.symbol, s]));
    const etfSet = new Set(etfs);
    const all: Row[] = symbols.map((s) => {
      const snap = bySymbol.get(s);
      const price = latestBars[s]?.close ?? snap?.price ?? null;
      const change =
        price != null && snap?.prevClose ? ((price - snap.prevClose) / snap.prevClose) * 100 : (snap?.changePct ?? null);
      return { symbol: s, etf: etfSet.has(s), price, change, volume: snap?.volume ?? null };
    });
    const query = filter.trim().toUpperCase();
    const shown = all.filter(
      (r) => (group === "all" || (group === "etf") === r.etf) && (!query || r.symbol.includes(query)),
    );
    if (sort) {
      const { key, down } = sort;
      shown.sort((a, b) => {
        const [x, y] = [a[key], b[key]];
        if (x == null || y == null) return x == null ? (y == null ? 0 : 1) : -1; // no data sorts last
        const order = typeof x === "string" ? x.localeCompare(y as string) : x - (y as number);
        return down ? -order : order;
      });
    }
    return shown;
  }, [symbols, etfs, snapshots, latestBars, filter, group, sort]);

  const selected = symbol || symbols[0] || "";
  const snap = snapshots.find((s) => s.symbol === selected);
  const effectiveDays = timeframe === "1" ? Math.min(days, 30) : days;
  const bars = useApi<Bar[]>(
    connected && selected
      ? `alpaca/bars?symbol=${encodeURIComponent(selected)}&tf=${timeframe}&days=${effectiveDays}`
      : null,
  );
  const liveBar = connected && timeframe === "1" ? (latestBars[selected] ?? null) : null;
  const last = latestBars[selected]?.close ?? snap?.price ?? null;
  const change = last != null && snap?.prevClose ? last - snap.prevClose : (snap?.change ?? null);
  const changePct = change != null && snap?.prevClose ? (change / snap.prevClose) * 100 : (snap?.changePct ?? null);

  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    // Keep the selected row visible by scrolling the list itself, never the page.
    const box = list.current;
    const row = box?.querySelector<HTMLElement>('[aria-selected="true"]');
    if (!box || !row) return;
    const top = row.offsetTop - 32; // clear the sticky column headings
    if (top < box.scrollTop) box.scrollTop = top;
    else if (row.offsetTop + row.offsetHeight > box.scrollTop + box.clientHeight)
      box.scrollTop = row.offsetTop + row.offsetHeight - box.clientHeight;
  }, [selected, symbols.length]);
  const step = (by: number) => {
    const at = rows.findIndex((r) => r.symbol === selected);
    const next = rows[Math.min(rows.length - 1, Math.max(0, at + by))];
    if (next) onSymbol(next.symbol);
  };

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-x-10 gap-y-8 lg:grid-cols-[minmax(0,1fr)_340px]">
      <section className="min-w-0">
        <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-rule pb-3">
          <h2 className="text-[22px] leading-7 font-semibold">{selected}</h2>
          <span className="num text-[22px] leading-7">{num(last)}</span>
          <span className={cx("num text-[15px]", tone(change))}>
            {signed(change)} {pct(changePct)}
          </span>
          {etfs.includes(selected) && <span className="text-[13px] text-dim">ETF</span>}
          {snap && (
            <dl className="num ml-auto flex flex-wrap gap-x-5 gap-y-1 text-[12px] text-dim">
              {(
                [
                  ["Open", num(snap.open)],
                  ["High", num(snap.high)],
                  ["Low", num(snap.low)],
                  ["VWAP", num(snap.vwap)],
                  ["Vol", compact(snap.volume)],
                  ["Bid", num(snap.bid)],
                  ["Ask", num(snap.ask)],
                ] as const
              ).map(([label, value]) => (
                <div key={label} className="flex gap-1.5">
                  <dt>{label}</dt>
                  <dd className="text-ink">{value}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-x-8 gap-y-2 py-3">
          <Choice label="Bar size" value={timeframe} onChange={setTimeframe} options={TIMEFRAMES} />
          <Choice
            label="Range"
            value={String(effectiveDays)}
            onChange={(d) => setDays(Number(d))}
            options={RANGES.map(([d, text]) => [String(d), text, timeframe === "1" && d > 30] as const)}
          />
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {INDICATORS.map(([key, text]) => (
              <label key={key} className="flex cursor-pointer items-center gap-1.5 text-[13px]">
                <input
                  type="checkbox"
                  className="size-3 accent-(--ink)"
                  checked={overlays[key]}
                  onChange={() => setOverlays((o) => ({ ...o, [key]: !o[key] }))}
                />
                <span className={overlays[key] ? "" : "text-dim"}>{text}</span>
                <span className="h-0.5 w-3" style={{ background: LINES[key] }} />
              </label>
            ))}
          </div>
          <span className="ml-auto text-[12px] text-dim">
            {bars.loading ? "Loading…" : bars.data ? `${bars.data.length} bars` : ""}
            {streaming && timeframe === "1" ? ", live" : ""}
          </span>
        </div>

        {!connected && <Message>Connect the Alpaca paper account to see charts.</Message>}
        {bars.error && <Message error>{bars.error}</Message>}
        {bars.data && !bars.error && (
          <PriceChart
            bars={bars.data}
            overlays={overlays}
            liveBar={liveBar}
            className="h-[min(64svh,640px)] min-h-[360px] w-full"
          />
        )}
      </section>

      <section className="flex min-w-0 flex-col lg:sticky lg:top-[57px] lg:h-[calc(100svh-81px)]">
        <div className="flex items-baseline gap-3 pb-2">
          <h2 className="text-[15px] font-semibold">Watchlist</h2>
          <span className="text-[13px] text-dim">
            {rows.length === symbols.length ? symbols.length : `${rows.length} of ${symbols.length}`}
          </span>
          <div className="ml-auto">
            <Choice label="Show" value={group} onChange={setGroup} options={GROUPS} />
          </div>
        </div>
        <input
          type="search"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && rows[0]) onSymbol(rows[0].symbol);
          }}
          placeholder="Filter symbols"
          aria-label="Filter symbols"
          className="mb-1 w-full border-b border-rule bg-transparent py-1.5 text-[13px] placeholder:text-faint focus:border-ink focus:outline-none"
        />
        <div
          ref={list}
          tabIndex={0}
          aria-label="Watchlist; use the arrow keys to change symbol"
          onKeyDown={(e) => {
            if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
            e.preventDefault();
            step(e.key === "ArrowDown" ? 1 : -1);
          }}
          className="relative max-h-[70svh] min-h-0 flex-1 overflow-y-auto lg:max-h-none"
        >
          <table className="w-full border-collapse text-[13px]">
            <thead className="sticky top-0 bg-bg">
              <tr>
                {COLUMNS.map(([key, text, right]) => (
                  <th
                    key={key}
                    className={cx(cell.th, "pr-3", right && "text-right")}
                    aria-sort={sort?.key === key ? (sort.down ? "descending" : "ascending") : undefined}
                  >
                    <button
                      type="button"
                      className={cx("hover:text-ink", sort?.key === key && "text-ink")}
                      title={`Sort by ${text.toLowerCase()}`}
                      onClick={() =>
                        setSort((s) =>
                          s?.key !== key
                            ? { key, down: key !== "symbol" }
                            : s.down === (key !== "symbol")
                              ? { key, down: !s.down }
                              : null,
                        )
                      }
                    >
                      {text}
                      {sort?.key === key ? (sort.down ? " ↓" : " ↑") : ""}
                    </button>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr
                  key={r.symbol}
                  aria-selected={r.symbol === selected}
                  onClick={() => {
                    onSymbol(r.symbol);
                    if (innerWidth < 1024) scrollTo({ top: 0 }); // the chart sits above the list on small screens
                  }}
                  className={cx("cursor-pointer", r.symbol === selected ? "bg-sel" : "hover:bg-sel/60")}
                >
                  <td className={cx(cell.td, "py-1 pr-3 pl-1.5", r.symbol === selected && "font-semibold")}>
                    {r.symbol}
                    {r.etf && <span className="pl-1.5 text-[11px] font-normal text-faint">ETF</span>}
                  </td>
                  <td className={cx(cell.td, cell.right, "py-1 pr-3")}>{num(r.price)}</td>
                  <td className={cx(cell.td, cell.right, "py-1 pr-3", tone(r.change))}>{pct(r.change)}</td>
                  <td className={cx(cell.td, cell.right, "py-1 !pr-1.5 text-dim")}>{compact(r.volume)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {rows.length === 0 && <Message>No symbols match.</Message>}
        </div>
      </section>
    </div>
  );
}
