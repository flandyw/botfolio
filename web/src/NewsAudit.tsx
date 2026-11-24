import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";
import { send, type Live, type News } from "./api";
import { ago, etDateTime } from "./format";
import { Message, Section, SymbolLink } from "./ui";
import { useApi } from "./useApi";

const ACTIONS: Record<string, string> = {
  WAIT: "Wait (no trade)",
  HOLD: "Hold (no new trade)",
  BUY: "Buy",
  SELL: "Sell",
};

function Stat({ label, ok, children }: { label: string; ok: boolean; children: ReactNode }) {
  return (
    <div>
      <dt className="inline text-dim">{label}: </dt>
      <dd className={ok ? "inline" : "inline text-warn"}>{children}</dd>
    </div>
  );
}

const CTL = "border border-rule bg-bg px-2 py-1";

export function NewsAudit({ onOpen }: { onOpen: (symbol: string) => void }) {
  const [f, setF] = useState({ ticker: "", status: "", action: "", source: "", hours: "", sort: "published", q: "" });
  const [limit, setLimit] = useState(50);
  const [typed, setTyped] = useState("");
  useEffect(() => {
    const id = setTimeout(() => setF((x) => ({ ...x, q: typed })), 300);
    return () => clearTimeout(id);
  }, [typed]);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => {
    setLimit(50);
    setF((x) => ({ ...x, [k]: e.target.value }));
  };
  const filters = Object.fromEntries(Object.entries(f).filter(([k, v]) => v && k !== "sort"));
  const query = new URLSearchParams({ ...filters, sort: f.sort, limit: String(limit) }).toString();
  const news = useApi<News>(`news?${query}`, 10_000);
  const [sending, setSending] = useState<string[]>([]);
  const items = news.data?.items ?? [];
  const [error, setError] = useState("");
  const queued = new Set(news.data?.queued ?? []);
  // Queue progress: the batch grows when more is queued and ends when the queue empties.
  const [batch, setBatch] = useState({ total: 0, left: 0 });
  const prev = useRef({ left: 0, total: 0 });
  const left = news.data?.queued.length;
  useEffect(() => {
    if (left === undefined) return;
    // Computed here, not inside the state updater: React runs updaters later, after the ref has moved on.
    const total = left === 0 ? 0 : prev.current.total + Math.max(0, left - prev.current.left);
    prev.current = { left, total };
    setBatch({ total, left });
  }, [left]);
  const live = useApi<Live>("live", 5_000);
  const l = live.data;
  const workerOk = l?.heartbeat_age_s != null && l.heartbeat_age_s >= 0 && l.heartbeat_age_s < 120;
  const busy = (key: string) => queued.has(key) || sending.includes(key);
  const assess = (body: object) => {
    setError("");
    const id = "key" in body ? String(body.key) : "all";
    setSending((x) => [...x, id]);
    send("POST", "news/assess", body)
      .then(() => news.reload(), (e: Error) => setError(e.message))
      .finally(() => setSending((x) => x.filter((y) => y !== id)));
  };

  return (
    <Section
      title="News and AI assessments"
      note={news.data ? `Showing ${items.length} of ${news.data.total} · ${news.data.total - news.data.unassessed} assessed · ET` : undefined}
      aside={
        <button
          disabled={!news.data?.unassessed || sending.includes("all") || batch.left > 0}
          onClick={() => assess({ all: true, filters })}
          className="border border-rule px-2 py-1 text-[13px] disabled:opacity-60"
        >
          {sending.includes("all") || batch.left > 0
            ? `Working…${batch.left > 0 ? ` ${batch.left} left` : ""}`
            : `Assess all unassessed${news.data ? ` (${news.data.unassessed})` : ""}`}
        </button>
      }
    >
      <div className="mb-2 flex flex-wrap items-center gap-2 border-t border-rule pt-2 text-[13px]">
        <input
          value={typed}
          onChange={(e) => setTyped(e.target.value)}
          placeholder="Search text"
          aria-label="Search article text"
          className={`${CTL} w-40`}
        />
        <select value={f.ticker} onChange={set("ticker")} aria-label="Company or fund" className={CTL}>
          <option value="">All symbols</option>
          {(news.data?.tickers ?? (f.ticker ? [f.ticker] : [])).map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <select value={f.status} onChange={set("status")} aria-label="Assessment status" className={CTL}>
          <option value="">Any status</option>
          <option value="assessed">Assessed</option>
          <option value="unassessed">Unassessed</option>
        </select>
        <select value={f.action} onChange={set("action")} aria-label="Suggested action" className={CTL}>
          <option value="">Any action</option>
          {Object.entries(ACTIONS).map(([k, v]) => (
            <option key={k} value={k}>{v}</option>
          ))}
        </select>
        <select value={f.source} onChange={set("source")} aria-label="Source" className={CTL}>
          <option value="">All sources</option>
          {(news.data?.sources ?? []).map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
        <select value={f.hours} onChange={set("hours")} aria-label="Published within" className={CTL}>
          <option value="">Any age</option>
          <option value="1">Last hour</option>
          <option value="6">Last 6 hours</option>
          <option value="24">Last day</option>
          <option value="168">Last week</option>
        </select>
        <select value={f.sort} onChange={set("sort")} aria-label="Sort by" className={CTL}>
          <option value="published">Newest published</option>
          <option value="saved">Newest saved</option>
          <option value="assessed">Latest assessed</option>
          <option value="ticker">Symbol A–Z</option>
        </select>
      </div>
      {(news.error || error) && <Message error>{news.error || error}</Message>}
      {news.data && items.length === 0 && (
        <Message>
          {Object.keys(filters).length ? "No articles match the current filters." : "No articles saved yet. Incoming articles appear here as the worker polls."}
        </Message>
      )}
      {news.data?.archived && (
        <p className="border-t border-rule py-2 text-[13px] text-warn">
          Archived articles only. The newest item is over a day old, so none are live signals.
        </p>
      )}
      {l && (
        <dl className="mb-2 flex flex-wrap gap-x-6 gap-y-1 border-t border-rule py-2 text-[13px]" role="status">
          <Stat label="Worker" ok={workerOk}>
            {workerOk ? `running (${ago(l.heartbeat_age_s)})` : "offline (assessments paused)"}
          </Stat>
          <Stat label="AI analyst" ok={l.ai_ready}>{l.ai_ready ? "configured" : "not configured"}</Stat>
          <Stat label="News feed" ok={!l.provider_failures && !l.issuer_feed_failures}>
            {l.provider_failures ? "last check failed" : l.issuer_feed_failures ? "issuer feed error" : "healthy"}
            {l.news_fresh ? " · fresh articles (<5m)" : " · no new articles in 5m"}
          </Stat>
          <Stat label="Queue" ok={batch.left === 0 || workerOk}>
            {batch.left > 0
              ? `${batch.left} queued`
              : news.data?.last_assessed
                ? `idle · last ${ago((Date.now() - new Date(news.data.last_assessed).getTime()) / 1000)}`
                : "idle"}
          </Stat>
        </dl>
      )}
      {batch.left > 0 && (
        <div className="border-t border-rule py-2 text-[13px]" role="status">
          <div className="flex justify-between text-dim">
            <span>Assessing articles in the background…</span>
            <span className="num">{Math.max(0, batch.total - batch.left)} of {Math.max(batch.total, batch.left)} done</span>
          </div>
          <progress className="mt-1 h-1.5 w-full" max={Math.max(batch.total, batch.left)} value={Math.max(0, batch.total - batch.left)} />
        </div>
      )}
      <div className="border-t border-rule">
        {items.map((item) => (
          <article key={item.key} className="grid gap-x-8 gap-y-2 border-b border-hair py-4 lg:grid-cols-[220px_minmax(0,1fr)]">
            <div className="text-[13px]">
              <div className="text-[15px]">
                <SymbolLink symbol={item.ticker} onOpen={onOpen} />
              </div>
              <div className="mt-1 font-medium">
                {item.decision ? (ACTIONS[item.decision.action] ?? item.decision.action) : "Unassessed"}
              </div>
              {!item.decision && (
                <button
                  disabled={busy(item.key)}
                  onClick={() => assess({ key: item.key })}
                  className="mt-1 border border-rule px-2 py-0.5 text-[12px] disabled:opacity-60"
                >
                  {busy(item.key) ? "Working…" : "Assess"}
                </button>
              )}
              <div className="num mt-1 text-[12px] text-dim">Published {etDateTime(item.published_at)}</div>
              <div className="num text-[12px] text-dim">Saved {etDateTime(item.received_at)}</div>
            </div>
            <div className="min-w-0 max-w-[80ch] text-[13px]">
              <p className="whitespace-pre-line">
                {item.text}
                {item.truncated ? "…" : ""}{" "}
                <a href={item.url} target="_blank" rel="noreferrer" className="underline decoration-rule underline-offset-4 hover:decoration-ink">
                  Source
                </a>
              </p>
              {item.decision && (
                <>
                  <p className="mt-3 whitespace-pre-line">
                    <span className="text-dim">Why: </span>
                    {item.decision.reasoning}
                  </p>
                  {item.decision.evidence.length + item.decision.risks.length > 0 && (
                    <details className="mt-2 text-dim">
                      <summary className="cursor-pointer">Evidence and limitations</summary>
                      <ul className="mt-1 list-disc pl-5">
                        {[...item.decision.evidence, ...item.decision.risks].map((line, i) => (
                          <li key={i}>{line}</li>
                        ))}
                      </ul>
                    </details>
                  )}
                  <p className="mt-2 text-[12px] text-faint">
                    {item.decision.model}, {item.decision.experiment}, assessed {etDateTime(item.decision.at)}
                  </p>
                </>
              )}
            </div>
          </article>
        ))}
      </div>
      {news.data && items.length < news.data.total && (
        <button onClick={() => setLimit(limit + 50)} className="mt-3 border border-rule px-3 py-1 text-[13px]">
          Show 50 more ({news.data.total - items.length} left)
        </button>
      )}
    </Section>
  );
}
