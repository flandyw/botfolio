import { useState } from "react";
import { AiUsage } from "./AiUsage";
import { type Config, type JournalRow, type Live, send } from "./api";
import { cell, cx } from "./classes";
import { etDateTime, num } from "./format";
import { Button, Message, Section, SymbolLink } from "./ui";
import { useApi } from "./useApi";

const STRATEGY: Record<string, string> = { news: "News + AI", momentum: "Momentum", etf_trend: "ETF trend" };

export function Control({
  live,
  config,
  onChange,
  onOpen,
}: {
  live: Live | null;
  config: Config;
  onChange: () => void;
  onOpen: (symbol: string) => void;
}) {
  const journal = useApi<JournalRow[]>("orders", 15_000);
  const [dedicated, setDedicated] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const act = (action: "arm" | "pause" | "stop" | "clear-stop") => {
    setBusy(true);
    setError("");
    send("POST", `control/${action}`, { dedicated })
      .then(onChange, (e: Error) => setError(e.message))
      .finally(() => setBusy(false));
  };

  const on = Boolean(live?.enabled && live.armed);
  const needed = live
    ? [
        !live.enabled && "Set EVENT_PAPER_ENABLED=true and EVENT_EXCLUSIVE_ACCOUNT=true in .env, then restart.",
        !live.worker_ready && "The worker's execution monitor has not checked in within the last 20 seconds.",
        live.stopped && "The emergency stop is active.",
        !dedicated && "Tick the box above to confirm the dedicated paper account.",
      ].filter((item): item is string => Boolean(item))
    : [];

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-10">
      <div className="grid grid-cols-[minmax(0,1fr)] gap-10 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
        <div className="grid content-start gap-10">
          <Section title="New entries">
            {!live ? (
              <Message>Loading worker status…</Message>
            ) : (
              <div className="border-t border-rule pt-3">
                <p className="text-[15px]">
                  <span className={cx("font-semibold", !on && "text-warn")}>Entries are {on ? "on" : "off"}.</span>{" "}
                  {on
                    ? `The bot places a paper order when any strategy gives a valid signal during market hours.${
                        live.market_open === false ? " It is waiting because the market is closed." : ""
                      }`
                    : "The bot will not open new trades. Open positions are still managed and closed normally."}
                </p>
                {live.message && <Message error>{live.message}</Message>}
                {live.provider_failures && (
                  <Message error>A data provider failed its last check. Entries are blocked for that cycle.</Message>
                )}
                {live.issuer_feed_failures && <Message>One or more issuer news feeds failed their last check.</Message>}
                {live.strategy_config.news.enabled && !live.ai_ready && (
                  <Message>
                    No AI news reader is set up, so the news strategy will not trade. Momentum and ETF trend do not
                    need one.
                  </Message>
                )}
                {live.strategy_config.news.enabled && live.ai_ready && !live.news_fresh && (
                  <Message>No current news. Old articles cannot trigger news entries.</Message>
                )}
                {!live.armed && (
                  <>
                    <label className="mt-3 flex cursor-pointer items-start gap-2 text-[13px]">
                      <input
                        type="checkbox"
                        className="mt-1 size-3 accent-(--ink)"
                        checked={dedicated}
                        onChange={(e) => setDedicated(e.target.checked)}
                      />
                      This Alpaca paper account is used only by this bot: no manual trading and no other bot.
                    </label>
                    {needed.length > 0 && (
                      <ul className="mt-3 text-[13px] text-dim">
                        {needed.map((item) => (
                          <li key={item}>Still needed: {item}</li>
                        ))}
                      </ul>
                    )}
                  </>
                )}
                <div className="mt-4 flex flex-wrap gap-2">
                  <Button kind="primary" disabled={busy || live.armed || needed.length > 0} onClick={() => act("arm")}>
                    Enable entries
                  </Button>
                  <Button disabled={busy || !live.armed} onClick={() => act("pause")}>
                    Pause entries
                  </Button>
                </div>
                {error && <Message error>{error}</Message>}
              </div>
            )}
          </Section>

          <Section title="Emergency stop">
            <div className="border-t border-rule pt-3">
              <p className="text-[13px] text-dim">
                Pauses entries, cancels the bot's open orders and closes its paper positions during regular hours.
              </p>
              {live?.stopped && (
                <p className="mt-2 text-[13px] text-warn">
                  The emergency stop is active. Closing positions needs a running worker, a broker connection and an
                  open market.
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button kind="danger" disabled={busy || !live} onClick={() => act("stop")}>
                  Emergency stop
                </Button>
                {live?.stopped && (
                  <Button disabled={busy} onClick={() => act("clear-stop")}>
                    Clear the stop, keep entries paused
                  </Button>
                )}
              </div>
            </div>
          </Section>
        </div>

        <Watchlist config={config} onSaved={onChange} />
      </div>

      <AiUsage />

      <Section title="Order journal" note={journal.data ? String(journal.data.length) : undefined}>
        {journal.error && <Message error>{journal.error}</Message>}
        {journal.data?.length === 0 && (
          <Message>No orders recorded yet. The worker waits for a valid signal from any strategy.</Message>
        )}
        {journal.data && journal.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[760px] border-collapse text-[13px]">
              <thead>
                <tr>
                  <th className={cell.th}>Opened (ET)</th>
                  <th className={cell.th}>Strategy</th>
                  <th className={cell.th}>Symbol</th>
                  <th className={cell.th}>Side</th>
                  <th className={cx(cell.th, cell.right)}>Qty</th>
                  <th className={cx(cell.th, cell.right)}>Limit</th>
                  <th className={cx(cell.th, cell.right)}>Stop</th>
                  <th className={cell.th}>State</th>
                  <th className={cell.th}>Broker</th>
                  <th className={cell.th}>Note</th>
                </tr>
              </thead>
              <tbody>
                {journal.data.map((row) => (
                  <tr key={row.key} className={row.state === "closed" ? "text-dim" : ""}>
                    <td className={cx(cell.td, "num whitespace-nowrap")}>{etDateTime(row.opened)}</td>
                    <td className={cell.td}>{STRATEGY[row.strategy] ?? row.strategy}</td>
                    <td className={cell.td}>
                      <SymbolLink symbol={row.symbol} onOpen={onOpen} />
                    </td>
                    <td className={cell.td}>{row.side ?? "—"}</td>
                    <td className={cx(cell.td, cell.right)}>{row.qty ?? "—"}</td>
                    <td className={cx(cell.td, cell.right)}>{num(row.limit)}</td>
                    <td className={cx(cell.td, cell.right)}>{num(row.stop)}</td>
                    <td className={cell.td}>{row.state}</td>
                    <td className={cell.td}>{row.broker_status?.replace("_", " ") ?? "—"}</td>
                    <td className={cell.td}>{row.exit_requested ?? row.error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}

function Watchlist({ config, onSaved }: { config: Config; onSaved: () => void }) {
  const saved = config.symbols.join(", ");
  const [text, setText] = useState(saved);
  const [base, setBase] = useState(saved);
  if (base !== saved) {
    // The saved list changed (here or elsewhere): show it, keeping any save message.
    setBase(saved);
    setText(saved);
  }
  const [result, setResult] = useState<{ error?: boolean; text: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const symbols = [...new Set(text.split(/[\s,]+/).map((s) => s.trim().toUpperCase()).filter(Boolean))];

  return (
    <Section title="Watchlist" note={`${symbols.length} symbols, up to ${config.max_symbols}`}>
      <form
        className="border-t border-rule pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          send<{ symbols: string[] }>("PUT", "watchlist", { symbols })
            .then(
              (r) => {
                setResult({ text: `Saved ${r.symbols.length} symbols. The worker uses them on its next cycle.` });
                onSaved();
              },
              (err: Error) => setResult({ error: true, text: err.message }),
            )
            .finally(() => setBusy(false));
        }}
      >
        <textarea
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setResult(null);
          }}
          rows={12}
          spellCheck={false}
          aria-label="Watchlist symbols, separated by commas"
          className="num block w-full resize-y border border-rule bg-transparent p-2 text-[12px] leading-5 focus:border-ink focus:outline-none"
        />
        <p className="mt-2 text-[13px] text-dim">
          Every strategy trades from this list. The ETF trend strategy uses the ETFs in it ({config.etfs.length} now).
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={busy || text === saved || symbols.length === 0}>
            Save watchlist
          </Button>
          {text !== saved && (
            <button type="button" className="text-[13px] text-dim underline underline-offset-4" onClick={() => setText(saved)}>
              Discard changes
            </button>
          )}
        </div>
        {result && <Message error={result.error}>{result.text}</Message>}
      </form>
    </Section>
  );
}
