import { useMemo, useState } from "react";
import type { JournalRow, TradeActivity, TradeFill } from "./api";
import { cx } from "./classes";
import { LINES } from "./chart";
import { etDateTime, etExecutionTime, num, pct, signed, signedUsd, tone, usd } from "./format";
import { INDICATORS, NO_OVERLAYS, PriceChart, type ChartAnnotations, type ChartOverlays } from "./PriceChart";
import { duration, tradeStats } from "./tradeStats";
import { STRATEGY_TITLES } from "./strategyInfo";
import { Figure, Message } from "./ui";
import { useApi } from "./useApi";

const ratio = (v: number | null) => (v == null ? "—" : `${num(v)}R`);
const selectClass = "min-w-0 rounded border border-rule bg-bg px-2 py-1.5 text-[13px]";

const SETUPS: Record<string, string> = {
  news: "A fresh news catalyst and a directional AI call triggered the entry. The bracket sets a stop and a profit target.",
  momentum:
    "A 30-minute close broke the session’s noise band and VWAP in the trade direction. A move back inside can trigger an exit.",
  orb: "The price broke the first 15 minutes’ range on elevated opening volume. The opposite edge of the range sets the stop.",
  vwap_reversion:
    "Price stretched beyond a volume-weighted band. A return to session VWAP is the strategy’s exit signal.",
  pullback:
    "RSI(2) fell below the entry threshold while price was above its long moving average. A close above the short average can trigger an exit.",
  etf_trend:
    "The ETF closed above its moving average and the upper threshold. A close below the lower threshold can trigger an exit.",
  etf_rotation:
    "The ETF qualified by trend and ranked by its average 1-, 3-, 6- and 12-month returns.",
  cash_park:
    "The Treasury-bill ETF holds cash available after the other allocations and the reserve.",
};

const title = (name: JournalRow["strategy"]) => STRATEGY_TITLES[name] ?? name;
const sideText = (side?: string) => ({ long: "Long", short: "Short" })[side ?? ""] ?? "—";
const human = (value: string) => value.replaceAll("_", " ");
const qty = (value?: number) => value?.toLocaleString("en-US", { maximumFractionDigits: 9 }) ?? "—";

/** A strategy exit with no recorded reason at or after the order's deadline is the timed exit. */
const timedOut = (row: JournalRow, fill: TradeFill) =>
  !row.exit_requested && !!row.deadline && !!fill.at && Date.parse(fill.at) >= Date.parse(row.deadline) - 1000;

function annotate(row: JournalRow, data?: TradeActivity): ChartAnnotations {
  const levels: ChartAnnotations["levels"] = [];
  const markers: ChartAnnotations["markers"] = [];
  if (row.stop != null) levels.push({ price: row.stop, label: "Stop loss", kind: "stop" });
  if (row.target != null) levels.push({ price: row.target, label: "Take profit", kind: "target" });
  if (!data?.entry && row.limit != null)
    levels.push({ price: row.limit, label: "Entry limit", kind: "limit" });
  if (!data) return { levels, markers };
  const barSeconds = data.bar_seconds;
  const bucket = (time: number) => Math.floor(time / barSeconds) * barSeconds;
  for (const [label, price] of Object.entries(data.context.levels ?? {})) {
    if (levels.every((other) => Math.abs(other.price - price) >= 0.01)) {
      levels.push({ price, label, kind: "signal" });
    }
  }

  const long = row.side !== "short";
  const [under, over] = long ? (["belowBar", "aboveBar"] as const) : (["aboveBar", "belowBar"] as const);
  const entry = data.entry;
  if (entry?.time != null) {
    markers.push({
      time: bucket(entry.time),
      price: entry.price,
      kind: "entry",
      label: `${sideText(row.side)} entry`,
      shape: long ? "arrowUp" : "arrowDown",
      position: under,
    });
  }
  if (!entry && data.submitted_time != null && row.limit != null) {
    markers.push({
      time: bucket(data.submitted_time),
      price: row.limit,
      kind: "order",
      label: "Entry limit",
      shape: "circle",
      position: under,
    });
  }
  for (const exit of data.exits) {
    if (exit.time != null) {
      markers.push({
        time: bucket(exit.time),
        price: exit.price,
        kind: "exit",
        label: exit.kind !== "Strategy exit" ? exit.kind : timedOut(row, exit) ? "Time exit" : "Exit",
        shape: long ? "arrowDown" : "arrowUp",
        position: over,
      });
    }
  }
  const { time } = data.context;
  // News context has no price; candle markers only need the bar, so fall back to its close.
  const price = data.context.price ?? data.bars.find((bar) => time != null && bucket(bar.time) === bucket(time))?.close;
  if (time != null && price != null) {
    const label = row.strategy === "news" ? "News" : "Signal";
    markers.push({ time: bucket(time), price, kind: "signal", label, shape: "circle", position: over });
  }
  return { levels, markers };
}

function FillDetails({ fill, exit, row }: { fill?: TradeFill | null; exit?: boolean; row?: JournalRow }) {
  if (!fill) return "—";
  const kind = row && fill.kind === "Strategy exit" && timedOut(row, fill) ? "Timed exit (hold limit)" : fill.kind;
  return (
    <div className="text-[13px]">
      <span className="num">{usd(fill.price)} · {qty(fill.qty)} sh</span>
      {exit && <span className="text-dim"> · {kind}</span>}
      {fill.at && (
        <time dateTime={fill.at} className="num block whitespace-nowrap text-[12px] text-dim">
          {etExecutionTime(fill.at)} ET
        </time>
      )}
    </div>
  );
}

function TradeDetail({ row, data }: { row: JournalRow; data?: TradeActivity }) {
  const annotations = useMemo(() => annotate(row, data), [row, data]);
  const entry = data?.entry;
  const exits = data?.exits ?? [];
  const closedQty = exits.reduce((sum, fill) => sum + fill.qty, 0);
  const stats = useMemo(() => tradeStats(row, data), [row, data]);
  const pnl = stats.pnl;
  const [picked, setOverlays] = useState<ChartOverlays | null>(null);
  const daily = data?.bar_seconds === 86_400;
  const overlays = picked ?? { ...NO_OVERLAYS, [daily ? "sma20" : "vwap"]: true };
  const context = data?.context;
  const levels = Object.entries(context?.levels ?? {});
  const metrics = Object.entries(context?.metrics ?? {});
  const sourceUrl = context?.url && /^https?:\/\//i.test(context.url) ? context.url : null;
  const orderStatus = data?.status ?? row.broker_status ?? row.state;
  const chartReady = data && (data.bars.length > 0 || annotations.markers.length > 0);
  const brokerExitMissing = data && entry && row.state === "closed" && closedQty + 1e-8 < entry.qty;

  return (
    <article id="selected-trade" className="min-w-0">
      <header className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-rule pb-3">
        <a
          href={`#/markets/${encodeURIComponent(row.symbol)}`}
          className="text-[25px] font-semibold hover:underline"
        >
          {row.symbol}
        </a>
        <span
          className={cx(
            "rounded px-2 py-0.5 text-[12px] font-medium",
            row.side === "short"
              ? "bg-down/10 text-down"
              : row.side === "long"
                ? "bg-up/10 text-up"
                : "bg-sel text-dim",
          )}
        >
          {sideText(row.side)}
        </span>
        <span className="text-[15px]">{title(row.strategy)}</span>
        <span className="ml-auto text-[13px] capitalize text-dim">
          {human(row.state)}
          {orderStatus !== row.state && ` · ${human(orderStatus)}`}
        </span>
      </header>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-rule py-3 lg:grid-cols-[2fr_2fr_1fr_1fr_1fr]">
        <Figure label="Entry fill">
          <FillDetails fill={entry} />
        </Figure>
        <Figure label={exits.length > 1 ? "Exit fills" : "Exit fill"}>
          {exits.length ? exits.map((exit, i) => <FillDetails key={i} fill={exit} exit row={row} />) : "—"}
        </Figure>
        <Figure label="Stop loss">
          <span className="num text-down">{usd(row.stop)}</span>
        </Figure>
        <Figure label="Take profit">
          <span className="num text-up">{usd(row.target)}</span>
        </Figure>
        <Figure label="Gross realized P&L">
          <span className={cx("num", tone(pnl))}>{signedUsd(pnl)}</span>
        </Figure>
      </dl>

      {entry && (
        <dl
          aria-label="Trade analytics"
          className="grid grid-cols-2 gap-x-4 gap-y-3 border-b border-rule py-3 sm:grid-cols-4 lg:grid-cols-7"
        >
          <Figure label="Return">
            <span className={cx("num", tone(stats.returnPct))}>{pct(stats.returnPct)}</span>
          </Figure>
          <Figure label="R multiple">
            <span className={cx("num", tone(stats.rMultiple))}>
              {stats.rMultiple == null ? "—" : `${signed(stats.rMultiple)}R`}
            </span>
          </Figure>
          <Figure label="Planned reward:risk">
            <span className="num">{ratio(stats.plannedRR)}</span>
          </Figure>
          <Figure label="Best excursion (MFE)">
            <span className="num text-up">{usd(stats.mfe)}</span>
            {stats.mfeR != null && <span className="num text-[12px] text-dim"> · {ratio(stats.mfeR)}</span>}
          </Figure>
          <Figure label="Worst excursion (MAE)">
            <span className="num text-down">{usd(stats.mae)}</span>
            {stats.maeR != null && <span className="num text-[12px] text-dim"> · {ratio(stats.maeR)}</span>}
          </Figure>
          <Figure label="MFE captured">
            <span className="num">{stats.capture == null ? "—" : `${num(stats.capture, 0)}%`}</span>
          </Figure>
          <Figure label={closedQty + 1e-8 >= entry.qty ? "Held" : "Held so far"}>
            <span className="num">{duration(stats.holdSeconds)}</span>
            {stats.slippage != null && (
              <span
                className={cx("num block text-[12px]", stats.slippage > 0.005 ? "text-down" : "text-dim")}
                title="Entry fill versus limit, per share. Positive is worse."
              >
                Slippage {signedUsd(stats.slippage)}/sh
              </span>
            )}
          </Figure>
        </dl>
      )}

      <div className="py-3">
        {data?.error && <Message error>{data.error}</Message>}
        {data?.fill_error && <Message error>{data.fill_error}</Message>}
        {brokerExitMissing && (
          <Message>
            Part or all of the exit is missing from the orders linked to this trade.
          </Message>
        )}
        {entry && !entry.at && (
          <Message>
            The broker reports an entry average and quantity. The fill time is unavailable.
          </Message>
        )}
        {exits.some((exit) => !exit.at) && (
          <Message>The broker reports an exit without a fill time.</Message>
        )}
        {chartReady ? (
          <figure
            aria-label={`${row.symbol} price chart with order levels, fills, and strategy signals`}
          >
            <figcaption className="flex flex-wrap items-center gap-x-4 gap-y-1 pb-2 text-[12px] text-dim">
              <span>{data.bar_seconds === 86_400 ? "Daily" : "5-minute"} · ET</span>
              {annotations.markers.some((mark) => mark.kind === "entry") && <span>▲▼ Entry</span>}
              {annotations.markers.some((mark) => mark.kind === "exit") && <span>▲▼ Exit</span>}
              {annotations.levels.some((level) => level.kind === "signal") && (
                <span>··· Strategy levels at signal</span>
              )}
              {!data.bars.length && <span>Market candles unavailable</span>}
              <span className="ml-auto flex flex-wrap gap-x-3">
                {INDICATORS.filter(([key]) => !daily || key !== "vwap").map(([key, text]) => (
                  <label key={key} className="flex cursor-pointer items-center gap-1.5 text-[12px]">
                    <input
                      type="checkbox"
                      className="size-3 accent-(--ink)"
                      checked={overlays[key]}
                      onChange={() => setOverlays({ ...overlays, [key]: !overlays[key] })}
                    />
                    <span className={overlays[key] ? "text-ink" : ""}>{text}</span>
                    <span className="h-0.5 w-3" style={{ background: LINES[key] }} />
                  </label>
                ))}
              </span>
            </figcaption>
            <PriceChart
              bars={data.bars}
              overlays={daily ? { ...overlays, vwap: false } : overlays}
              annotations={annotations}
              className="h-[min(53svh,540px)] min-h-[300px] w-full"
            />
          </figure>
        ) : (
          data &&
          !data.error && <Message>No candles or timed fills are available for this order.</Message>
        )}
      </div>

      <div className="grid gap-x-8 gap-y-4 border-t border-rule pt-4 sm:grid-cols-2">
        <section>
          <h3 className="font-semibold">Strategy setup</h3>
          {SETUPS[row.strategy] && (
            <p className="mt-1 text-[13px] text-dim">{SETUPS[row.strategy]}</p>
          )}
          {context?.at && (
            <p className="mt-2 text-[13px]">
              {row.strategy === "news" ? "Catalyst" : "Signal"} · {etDateTime(context.at)} ET
              {context.price != null && ` · ${usd(context.price)}`}
            </p>
          )}
          {context?.article && <p className="mt-1 text-[13px]">{context.article}</p>}
          {sourceUrl && (
            <a
              className="text-[13px] underline underline-offset-4"
              href={sourceUrl}
              target="_blank"
              rel="noreferrer"
            >
              Open source article
            </a>
          )}
          {context?.note && <p className="mt-2 text-[13px] text-dim">{context.note}</p>}
          {(levels.length > 0 || metrics.length > 0) && (
            <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-[13px]">
              {[...levels.map(([label, price]) => [label, usd(price)] as const), ...metrics].map(
                ([label, value]) => (
                  <div key={label} className="flex flex-wrap gap-x-2">
                    <dt className="text-dim">{label}</dt>
                    <dd className="num">{value}</dd>
                  </div>
                ),
              )}
            </dl>
          )}
          {data && !context?.at && !levels.length && !metrics.length && (
            <p className="mt-2 text-[13px] text-dim">Strategy values unavailable for this order.</p>
          )}
        </section>
        <section>
          <h3 className="font-semibold">Order details</h3>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[13px]">
            <dt className="text-dim">{row.state === "reserved" ? "Recorded" : "Submitted"}</dt>
            <dd>
              {etDateTime(row.opened)}
              {row.opened && " ET"}
            </dd>
            <dt className="text-dim">Entry limit</dt>
            <dd className="num">{usd(row.limit)}</dd>
            <dt className="text-dim">Quantity requested</dt>
            <dd className="num">{qty(row.qty)}</dd>
            {row.deadline && (
              <>
                <dt className="text-dim">Scheduled exit</dt>
                <dd>{etDateTime(row.deadline)} ET</dd>
              </>
            )}
            {row.exit_requested && (
              <>
                <dt className="text-dim">Exit requested</dt>
                <dd>{row.exit_requested}</dd>
              </>
            )}
          </dl>
          {row.error && <Message error>{row.error}</Message>}
        </section>
      </div>
    </article>
  );
}

export function Activity({ trade }: { trade: string }) {
  const journal = useApi<JournalRow[]>("orders", 15_000);
  const [query, setQuery] = useState("");
  const [strategy, setStrategy] = useState("");
  const [side, setSide] = useState("");
  const rows = journal.data ?? [];
  const filter = query.trim().toUpperCase();
  const shown = rows.filter(
    (row) =>
      row.symbol.includes(filter) &&
      (!strategy || row.strategy === strategy) &&
      (!side || row.side === side),
  );
  const selected = trade || shown[0]?.key;
  const listed = rows.find((row) => row.key === selected);
  const detail = useApi<TradeActivity>(
    selected ? `orders/detail?key=${encodeURIComponent(selected)}` : null,
    listed && listed.state !== "closed" ? 60_000 : 0,
  );
  const row = detail.data?.order ?? listed;

  return (
    <div>
      <div className="mb-5 flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-ink pb-3">
        <h1 className="text-[23px] font-semibold">Activity log</h1>
        <span className="text-[13px] text-dim">Paper trades</span>
      </div>
      {journal.error && <Message error>{journal.error}</Message>}
      <div className="grid min-w-0 gap-6 lg:grid-cols-[310px_minmax(0,1fr)] xl:grid-cols-[350px_minmax(0,1fr)]">
        <aside aria-label="Order journal" className="min-w-0">
          <div className="grid grid-cols-2 gap-2">
            <input
              type="search"
              value={query}
              aria-label="Filter by symbol"
              placeholder="Filter by symbol"
              onChange={(e) => setQuery(e.target.value)}
              className={cx(selectClass, "col-span-2")}
            />
            <select
              aria-label="Filter by strategy"
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              className={selectClass}
            >
              <option value="">All strategies</option>
              {Object.entries(STRATEGY_TITLES).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>
            <select
              aria-label="Filter by direction"
              value={side}
              onChange={(e) => setSide(e.target.value)}
              className={selectClass}
            >
              <option value="">Both directions</option>
              <option value="long">Long</option>
              <option value="short">Short</option>
            </select>
          </div>
          <p className="mt-3 border-b border-rule pb-1 text-[12px] text-dim">
            {journal.loading
              ? "Loading orders…"
              : journal.data && `${shown.length} of ${rows.length} recent orders · ET`}
          </p>
          {journal.data && !shown.length && (
            <Message>{rows.length ? "No matching orders." : "No orders recorded yet."}</Message>
          )}
          <div className="max-h-[34svh] overflow-y-auto lg:max-h-[68svh]">
            {shown.map((item) => (
              <a
                key={item.key}
                href={`#/activity/${encodeURIComponent(item.key)}`}
                aria-current={item.key === selected ? "true" : undefined}
                className={cx(
                  "grid grid-cols-[1fr_auto] gap-x-2 border-b border-hair border-l-2 px-2 py-2 text-[13px]",
                  item.key === selected
                    ? "border-l-ink bg-sel"
                    : "border-l-transparent hover:bg-sel/60",
                )}
              >
                <span className="font-semibold">
                  {item.symbol}{" "}
                  <span className="ml-1 font-normal text-dim">{sideText(item.side)}</span>
                </span>
                <span className="num text-[12px] text-dim">{etDateTime(item.opened)}</span>
                <span className="text-dim">{title(item.strategy)}</span>
                <span className="text-right text-[12px] capitalize text-dim">
                  {human(item.state)}
                </span>
              </a>
            ))}
          </div>
          {trade && shown.length > 0 && !shown.some((item) => item.key === trade) && (
            <a
              href="#/activity"
              className="mt-2 inline-block text-[13px] underline underline-offset-4"
            >
              Select first matching order
            </a>
          )}
        </aside>

        <div className="min-w-0">
          {detail.loading && <Message>Loading trade chart and fills…</Message>}
          {detail.error && <Message error>{detail.error}</Message>}
          {row && <TradeDetail row={row} data={detail.data} />}
          {(detail.error || detail.data?.error || detail.data?.fill_error) && (
            <button
              type="button"
              className="mt-3 text-[13px] underline underline-offset-4"
              onClick={detail.reload}
            >
              Retry trade data
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
