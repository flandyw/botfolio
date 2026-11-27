import { useState } from "react";
import { AiUsage } from "./AiUsage";
import { type BenchmarkSettings, type Config, type Live, send } from "./api";
import { cx } from "./classes";
import { Button, Message, Section } from "./ui";

export function Control({
  live,
  config,
  onChange,
}: {
  live: Live | null;
  config: Config;
  onChange: () => void;
}) {
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
        !live.worker_ready && "Execution monitor has not checked in within the last 20 seconds.",
        live.stopped && "Emergency stop is active.",
        !dedicated && "Confirm the dedicated paper account above.",
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
                  <span className={cx("font-semibold", !on && "text-warn")}>Entries are {on ? "on" : "paused"}.</span>{" "}
                  {on
                    ? `The bot submits paper orders when any strategy signals during regular market hours.${
                        live.market_open === false ? " Waiting for the market to open." : ""
                      }`
                    : "New trades are paused. Open positions are still managed and closed normally."}
                </p>
                {live.message && <Message error>{live.message}</Message>}
                {live.provider_failures && (
                  <Message error>A data provider failed its last check. New entries are blocked for this cycle.</Message>
                )}
                {live.issuer_feed_failures && <Message>One or more issuer news feeds failed their last check.</Message>}
                {live.strategy_config.news.enabled && !live.ai_ready && (
                  <Message>
                    No AI news analyst is configured, so News + AI will not trade. Rule-based strategies run without one.
                  </Message>
                )}
                {live.strategy_config.news.enabled && live.ai_ready && !live.news_fresh && (
                  <Message>No recent news. Articles outside the freshness window cannot trigger trades.</Message>
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
                      This Alpaca Paper account is dedicated to Botfolio (no manual trading or other bots).
                    </label>
                    {needed.length > 0 && (
                      <ul className="mt-3 text-[13px] text-dim">
                        {needed.map((item) => (
                          <li key={item}>Required: {item}</li>
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
                Pauses new entries, cancels open bot orders, and closes bot positions during regular market hours.
              </p>
              {live?.stopped && (
                <p className="mt-2 text-[13px] text-warn">
                  Emergency stop is active. Closing positions requires a running worker, a broker connection, and an
                  open market.
                </p>
              )}
              <div className="mt-3 flex flex-wrap gap-2">
                <Button kind="danger" disabled={busy || !live} onClick={() => act("stop")}>
                  Emergency stop
                </Button>
                {live?.stopped && (
                  <Button disabled={busy} onClick={() => act("clear-stop")}>
                    Clear stop (keep entries paused)
                  </Button>
                )}
              </div>
            </div>
          </Section>
        </div>

        <Watchlist config={config} onSaved={onChange} />
      </div>

      <BenchmarkSettingsForm settings={config.benchmarks} onSaved={onChange} />

      <AiUsage />
    </div>
  );
}

function BenchmarkSettingsForm({ settings, onSaved }: { settings: BenchmarkSettings; onSaved: () => void }) {
  const [purchaseDate, setPurchaseDate] = useState(settings.purchase_date ?? "");
  const [rate, setRate] = useState(String(settings.annual_rate_pct));
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ error: boolean; text: string } | null>(null);
  const inputClass = "num border border-rule bg-transparent px-2 py-1 text-[13px] text-ink";

  return (
    <Section title="Desk benchmarks">
      <form
        className="border-t border-rule pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setResult(null);
          send<BenchmarkSettings>("PUT", "desk-benchmarks", {
            purchase_date: purchaseDate || null,
            annual_rate_pct: Number(rate),
          })
            .then(
              () => {
                setResult({ error: false, text: "Benchmark settings saved." });
                onSaved();
              },
              (err: Error) => setResult({ error: true, text: err.message }),
            )
            .finally(() => setBusy(false));
        }}
      >
        <div className="flex flex-wrap items-end gap-3">
          <label className="grid gap-1 text-[13px] text-dim">
            Start date
            <input
              type="date"
              className={inputClass}
              disabled={busy}
              value={purchaseDate}
              onChange={(e) => setPurchaseDate(e.target.value)}
            />
          </label>
          <label className="grid gap-1 text-[13px] text-dim">
            Savings rate (% p.a.)
            <input
              type="number"
              min="0"
              step="any"
              required
              disabled={busy}
              className={`w-28 ${inputClass}`}
              value={rate}
              onChange={(e) => setRate(e.target.value)}
            />
          </label>
          <Button type="submit" disabled={busy}>
            Save benchmarks
          </Button>
        </div>
        <p className="mt-2 text-[13px] text-dim">
          QQQ and VGT use the first available session’s opening price and adjust for dividends and splits.
          Savings uses an effective annual rate from the start date. Leave the date blank to hide benchmarks.
        </p>
        {result && <div role="status"><Message error={result.error}>{result.text}</Message></div>}
      </form>
    </Section>
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
    <Section title="Watchlist" note={`${symbols.length} of ${config.max_symbols} symbols`}>
      <form
        className="border-t border-rule pt-3"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          send<{ symbols: string[] }>("PUT", "watchlist", { symbols })
            .then(
              (r) => {
                setResult({ text: `Saved ${r.symbols.length} symbols. The worker applies changes on its next cycle.` });
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
          All strategies trade from this watchlist. ETF trend and ETF rotation use the {config.etfs.length} eligible
          ETFs in it.
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
