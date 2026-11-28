import { useEffect, useState } from "react";
import { LineSeries, LineStyle, createSeriesMarkers, type SeriesMarker, type Time } from "lightweight-charts";
import type { NewsReview as Review, ReviewCall } from "./api";
import { LINES, palette, useChart, useThemeKey } from "./chart";
import { cx } from "./classes";
import { etDateTime, etTime, num, pct, tone } from "./format";
import { NewsPerformance } from "./NewsPerformance";
import { NewsAudit } from "./NewsAudit";
import { Choice, Figure, Message, Section, SymbolLink } from "./ui";
import { useApi } from "./useApi";

const CTL = "border border-rule bg-bg px-2 py-1";
const NEWS = LINES.ema50; // violet: distinct from price, up and down in both themes
const KIND = { buy: "▲ Buy", sell: "▼ Sell (short)", held: "Held back · priced in" } as const;
const kind = (c: ReviewCall) => (c.held ? "held" : c.side > 0 ? "buy" : "sell");
const verdictText = (c: ReviewCall) =>
  c.held
    ? { right: "✓ Right to skip", wrong: `✗ Missed ${pct(c.m60 * 100)}`, flat: "– Flat" }[c.verdict]
    : { right: `✓ Right ${pct(c.m60 * 100)}`, wrong: `✗ Wrong ${pct(c.m60 * 100)}`, flat: `– Flat ${pct(c.m60 * 100)}` }[c.verdict];

/** The News page: saved articles, or how the AI's calls played out. */
export function NewsPage({ onOpen }: { onOpen: (symbol: string) => void }) {
  const [view, setView] = useState<"articles" | "review">(() => (location.hash.endsWith("/review") ? "review" : "articles"));
  useEffect(() => {
    history.replaceState(null, "", view === "review" ? "#/news/review" : "#/news");
  }, [view]);
  return (
    <>
      <div className="mb-4">
        <Choice label="News view" value={view} onChange={setView} options={[["articles", "Articles"], ["review", "Call review"]]} />
      </div>
      {view === "review" ? (
        <div className="grid gap-6">
          <NewsPerformance />
          <NewsReview onOpen={onOpen} />
        </div>
      ) : (
        <NewsAudit onOpen={onOpen} />
      )}
    </>
  );
}

function NewsReview({ onOpen }: { onOpen: (symbol: string) => void }) {
  const [day, setDay] = useState("");
  const [f, setF] = useState({ kind: "", verdict: "", traded: "", sort: "newest" });
  const [limit, setLimit] = useState(12);
  const review = useApi<Review>(`news/review${day ? `?day=${day}` : ""}`, 60_000);
  const r = review.data;
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => {
    setLimit(12);
    setF((x) => ({ ...x, [k]: e.target.value }));
  };
  const calls = (r?.calls ?? [])
    .filter((c) => (!f.kind || kind(c) === f.kind) && (!f.verdict || c.verdict === f.verdict)
      && (!f.traded || (f.traded === "yes") === c.traded))
    .sort({
      newest: (a: ReviewCall, b: ReviewCall) => b.entry_time - a.entry_time,
      oldest: (a: ReviewCall, b: ReviewCall) => a.entry_time - b.entry_time,
      best: (a: ReviewCall, b: ReviewCall) => (b.held ? -b.m60 : b.m60) - (a.held ? -a.m60 : a.m60),
      worst: (a: ReviewCall, b: ReviewCall) => (a.held ? -a.m60 : a.m60) - (b.held ? -b.m60 : b.m60),
    }[f.sort as "newest"]);
  const acted = calls.filter((c) => !c.held), held = calls.filter((c) => c.held);
  const right = acted.filter((c) => c.verdict === "right").length, wrong = acted.filter((c) => c.verdict === "wrong").length;
  const avg = acted.length ? (acted.reduce((s, c) => s + c.m60, 0) / acted.length) * 100 : null;
  const tradedCalls = acted.filter((c) => c.traded);
  const tradedAvg = tradedCalls.length ? (tradedCalls.reduce((s, c) => s + c.m60, 0) / tradedCalls.length) * 100 : null;

  return (
    <Section
      title="Were the AI's news calls right?"
      note="Each call is judged on the hour after the bot saw the article; news trades themselves close sooner · ET"
    >
      <div className="mb-2 flex flex-wrap items-center gap-2 border-t border-rule pt-2 text-[13px]">
        <select value={r?.day ?? day} onChange={(e) => { setLimit(12); setDay(e.target.value); }} aria-label="Trading day" className={CTL}>
          {(r?.days ?? (day ? [day] : [])).map((d) => (
            <option key={d} value={d}>
              {new Date(`${d}T12:00:00Z`).toLocaleDateString("en-US", { weekday: "short", month: "short", day: "numeric" })}
            </option>
          ))}
        </select>
        <select value={f.kind} onChange={set("kind")} aria-label="Call" className={CTL}>
          <option value="">All calls</option>
          <option value="buy">Buy</option>
          <option value="sell">Sell (short)</option>
          <option value="held">Held back (priced in)</option>
        </select>
        <select value={f.verdict} onChange={set("verdict")} aria-label="Verdict" className={CTL}>
          <option value="">Any verdict</option>
          <option value="right">Right</option>
          <option value="wrong">Wrong</option>
          <option value="flat">Flat (under 0.1%)</option>
        </select>
        <select value={f.traded} onChange={set("traded")} aria-label="Traded" className={CTL}>
          <option value="">Traded or not</option>
          <option value="yes">Bot placed a trade</option>
          <option value="no">No trade</option>
        </select>
        <select value={f.sort} onChange={set("sort")} aria-label="Sort by" className={CTL}>
          <option value="newest">Newest first</option>
          <option value="oldest">Oldest first</option>
          <option value="best">Best call first</option>
          <option value="worst">Worst call first</option>
        </select>
      </div>
      {review.error && <Message error>{review.error}</Message>}
      {review.loading && <Message>Loading prices for every call…</Message>}
      {r && (
        <dl className="mb-3 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-rule py-3 sm:grid-cols-3 lg:grid-cols-5">
          <Figure label="Buy/sell calls right">
            <span className="num">{acted.length ? `${Math.round((100 * right) / acted.length)}%` : "—"}</span>
            <span className="text-[13px] text-dim"> · {right} right, {wrong} wrong, {acted.length - right - wrong} flat</span>
          </Figure>
          <Figure label="Average move after 60 min">
            <span className={cx("num", tone(avg))}>{pct(avg)}</span>
            <span className="text-[13px] text-dim"> in the call's direction</span>
          </Figure>
          <Figure label="Calls the bot traded">
            <span className={cx("num", tone(tradedAvg))}>{pct(tradedAvg)}</span>
            <span className="text-[13px] text-dim"> avg · {tradedCalls.length} trades</span>
          </Figure>
          <Figure label="2% stop hit">
            <span className="num">
              {acted.filter((c) => c.bracket === "stop").length} of {acted.length}
            </span>
            <span className="text-[13px] text-dim"> within the hour</span>
          </Figure>
          <Figure label="Held back correctly">
            <span className="num">{held.length ? `${held.filter((c) => c.verdict === "right").length} of ${held.length}` : "—"}</span>
            <span className="text-[13px] text-dim"> would have lost</span>
          </Figure>
        </dl>
      )}
      {r && (
        <p className="mb-2 flex flex-wrap gap-x-5 gap-y-1 border-t border-rule pt-2 text-[12px] text-dim">
          <span><span style={{ color: NEWS }}>●</span> News published</span>
          <span><span className="text-up">▲</span> Buy entry · <span className="text-down">▼</span> Sell entry (first price after the bot saw it)</span>
          <span>○ Price after 60 min</span>
          <span>Dashed lines: previous close, 2% stop</span>
        </p>
      )}
      {r && calls.length === 0 && <Message>{r.calls.length ? "No calls match these filters." : "The AI made no buy, sell or priced-in calls this day."}</Message>}
      <div className="border-t border-rule">
        {calls.slice(0, limit).map((c) => (
          <ReviewCard key={c.key} call={c} prices={r!.prices[c.ticker]} onOpen={onOpen} />
        ))}
      </div>
      {calls.length > limit && (
        <button onClick={() => setLimit(limit + 12)} className="mt-3 border border-rule px-3 py-1 text-[13px]">
          Show 12 more ({calls.length - limit} left)
        </button>
      )}
    </Section>
  );
}

function ReviewCard({ call: c, prices, onOpen }: { call: ReviewCall; prices: Review["prices"][string]; onOpen: (s: string) => void }) {
  const lag = Math.round((Date.parse(c.received) - Date.parse(c.published)) / 60_000);
  const brk = c.held ? "—" : { stop: "✗ Stop hit", target: "✓ Target hit", time: "Timed exit" }[c.bracket];
  return (
    <article className="grid gap-x-8 gap-y-2 border-b border-hair py-4 lg:grid-cols-[260px_minmax(0,1fr)]">
      <div className="text-[13px]">
        <div className="flex items-baseline gap-2 text-[15px]">
          <SymbolLink symbol={c.ticker} onOpen={onOpen} />
          <span className={cx("font-medium", c.held ? "text-dim" : c.side > 0 ? "text-up" : "text-down")}>{KIND[kind(c)]}</span>
        </div>
        <div className={cx("mt-1 text-[15px] font-medium", c.verdict === "right" ? "text-up" : c.verdict === "wrong" ? "text-down" : "text-dim")}>
          {verdictText(c)}
        </div>
        <dl className="num mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-[12px]">
          <dt className="text-dim">Published</dt><dd>{etDateTime(c.published)}</dd>
          <dt className="text-dim">Bot saw it</dt><dd>{etTime(c.received)}{lag > 0 ? ` (+${lag} min)` : ""}</dd>
          <dt className="text-dim">Entry</dt><dd>{num(c.entry)}</dd>
          <dt className="text-dim">After 15 min</dt><dd className={tone(c.m15)}>{pct(c.m15 * 100)}</dd>
          <dt className="text-dim">{c.full_hour ? "After 60 min" : "At the close"}</dt><dd className={tone(c.m60)}>{pct(c.m60 * 100)}</dd>
          <dt className="text-dim">To the close</dt><dd className={tone(c.m_close)}>{pct(c.m_close * 100)}</dd>
          <dt className="text-dim">Bracket</dt><dd>{brk}</dd>
          <dt className="text-dim">Impact</dt><dd>{c.materiality}{c.traded ? " · bot traded" : ""}</dd>
        </dl>
      </div>
      <div className="min-w-0 text-[13px]">
        <p className="line-clamp-2">{c.text}</p>
        <ReviewChart call={c} prices={prices} />
        <details className="mt-2 text-dim">
          <summary className="cursor-pointer">Why the AI made this call</summary>
          {c.evidence[0] && <p className="mt-1">Quoted: “{c.evidence[0]}”</p>}
          <p className="mt-1 whitespace-pre-line">{c.reasoning}</p>
          <p className="mt-1 text-[12px] text-faint">
            {c.action} · {c.model} · {c.source} ·{" "}
            <a href={c.url} target="_blank" rel="noreferrer" className="underline decoration-rule underline-offset-4 hover:decoration-ink">Source</a>
          </p>
        </details>
      </div>
    </article>
  );
}

function ReviewChart({ call: c, prices }: { call: ReviewCall; prices: Review["prices"][string] }) {
  const { ref, chart } = useChart({ handleScroll: false, handleScale: false });
  const themeKey = useThemeKey();
  useEffect(() => {
    if (!chart || !prices) return;
    const { up, down, faint, dim } = palette();
    const line = chart.addSeries(LineSeries, { color: dim, lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
    line.setData(prices.time.map((t, i) => ({ time: t as Time, value: prices.close[i] })));
    const dashed = { lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true } as const;
    if (prices.prev_close != null) line.createPriceLine({ price: prices.prev_close, color: faint, title: "prev close", ...dashed });
    if (!c.held) line.createPriceLine({ price: c.entry * (1 - 0.02 * c.side), color: down, title: "stop", ...dashed });
    // Markers must sit on bar times; the article can predate the first bar shown.
    const bar = (t: number) => prices.time.find((x) => x >= t) ?? prices.time[prices.time.length - 1];
    const early = c.published_time < prices.time[0];
    const colour = c.side > 0 ? up : down;
    const markers: SeriesMarker<Time>[] = [
      { time: bar(c.published_time) as Time, position: "aboveBar", shape: "circle", color: NEWS,
        text: `News ${etTime(c.published)}${early ? " (earlier)" : ""}` },
      { time: c.entry_time as Time, position: c.side > 0 ? "belowBar" : "aboveBar", shape: c.side > 0 ? "arrowUp" : "arrowDown",
        color: c.held ? faint : colour, text: `${c.held ? "would " : ""}${c.side > 0 ? "BUY" : "SELL"} ${num(c.entry)}` },
      { time: c.after60_time as Time, position: "inBar", shape: "circle", color: c.m60 >= 0 ? up : down,
        text: `${c.full_hour ? "+60m" : "close"} ${pct(c.m60 * 100)}` },
    ];
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    const handle = createSeriesMarkers(line, markers);
    chart.timeScale().fitContent();
    return () => {
      try {
        handle.detach();
        chart.removeSeries(line);
      } catch {
        // on unmount useChart has already removed the chart
      }
    };
  }, [chart, prices, c, themeKey]);
  if (!prices) return <Message>No prices for {c.ticker} this day.</Message>;
  return <div ref={ref} className="mt-2 h-[220px] w-full" role="img" aria-label={`${c.ticker} minute prices with the news and entry marked`} />;
}
