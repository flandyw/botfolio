import { useState } from "react";
import type { News } from "./api";
import { etDateTime } from "./format";
import { Message, Section, SymbolLink } from "./ui";
import { useApi } from "./useApi";

const ACTIONS: Record<string, string> = {
  WAIT: "Wait, no trade suggested",
  HOLD: "Hold, no new trade suggested",
  BUY: "Buy",
  SELL: "Sell",
};

export function NewsAudit({ onOpen }: { onOpen: (symbol: string) => void }) {
  const [ticker, setTicker] = useState("");
  const news = useApi<News>(`news${ticker ? `?ticker=${encodeURIComponent(ticker)}` : ""}`, 60_000);
  const items = news.data?.items ?? [];

  return (
    <Section
      title="News and AI assessments"
      note={news.data ? `showing ${items.length} of ${news.data.total}, times in ET` : undefined}
      aside={
        <select
          value={ticker}
          onChange={(e) => setTicker(e.target.value)}
          aria-label="Company or fund"
          className="border border-rule bg-bg px-2 py-1 text-[13px]"
        >
          <option value="">All symbols</option>
          {(news.data?.tickers ?? (ticker ? [ticker] : [])).map((t) => (
            <option key={t}>{t}</option>
          ))}
        </select>
      }
    >
      {news.error && <Message error>{news.error}</Message>}
      {news.data && items.length === 0 && (
        <Message>No news saved yet. The worker fills this page as it receives articles.</Message>
      )}
      {news.data?.archived && (
        <p className="border-t border-rule py-2 text-[13px] text-warn">
          These are archived articles. The newest is more than a day old, so none of these is a current signal.
        </p>
      )}
      <div className="border-t border-rule">
        {items.map((item) => (
          <article key={item.key} className="grid gap-x-8 gap-y-2 border-b border-hair py-4 lg:grid-cols-[220px_minmax(0,1fr)]">
            <div className="text-[13px]">
              <div className="text-[15px]">
                <SymbolLink symbol={item.ticker} onOpen={onOpen} />
              </div>
              <div className="mt-1 font-medium">
                {item.decision ? (ACTIONS[item.decision.action] ?? item.decision.action) : "Not assessed"}
              </div>
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
                    {item.decision.model}, {item.decision.experiment}
                  </p>
                </>
              )}
            </div>
          </article>
        ))}
      </div>
    </Section>
  );
}
