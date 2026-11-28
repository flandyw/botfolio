import { useState } from "react";
import type {
  AlpacaAccountResponse,
  AlpacaPortfolioHistory,
  AlpacaPosition,
  AlpacaStatus,
  BenchmarkSettings,
  Live,
  StrategyConfig,
  StrategyName,
  StrategyPerformance,
} from "./api";
import { EquityChart } from "./EquityChart";
import { ago, etDateTime, num, pct, signedUsd, tone, usd } from "./format";
import { cell, cx } from "./classes";
import { Choice, Figure, Message, Section, SymbolLink } from "./ui";
import { useApi } from "./useApi";
import { STRATEGIES, STRATEGY_TITLES } from "./strategyInfo";

const PERIODS = [
  ["1D", "5Min"],
  ["1W", "15Min"],
  ["1M", "1D"],
  ["3M", "1D"],
  ["1A", "1D"],
] as const;
type Period = (typeof PERIODS)[number][0];

function limits(name: StrategyName, c: StrategyConfig | undefined, etfs: number) {
  if (!c) return "—";
  if (name === "etf_trend") return `${num(c.allocation_pct, 0)}% of equity across ${etfs} ETFs`;
  if (name === "etf_rotation") return `${num(c.allocation_pct, 0)}% of equity across ${c.max_positions} ETFs`;
  if (name === "cash_park") return `Up to ${num(c.position_pct, 0)}% of equity`;
  if (name === "pullback") return `Up to ${c.max_positions} positions · ${num(c.position_pct, 0)}% of equity each`;
  return `Up to ${c.max_positions} positions · ${num(c.position_pct, 0)}% of equity each · ${num(c.risk_pct, 1)}% risk`;
}

const SHADES = ["bg-ink", "bg-dim", "bg-faint"];

/** Share of gross exposure plus cash held in each position, largest first. */
function Allocation({
  positions,
  cash,
  onOpen,
}: {
  positions: AlpacaPosition[];
  cash: number;
  onOpen: (symbol: string) => void;
}) {
  const parts = positions
    .map((p) => ({ symbol: p.symbol, value: Math.abs(p.marketValue), pnl: p.unrealizedPlpc }))
    .sort((a, b) => b.value - a.value);
  const total = parts.reduce((sum, p) => sum + p.value, 0) + Math.max(cash, 0);
  if (!parts.length || !total) return null;
  return (
    <div className="mt-4">
      <div className="flex h-2 gap-px" role="img" aria-label="Allocation by position">
        {parts.map((p, i) => (
          <span
            key={p.symbol}
            title={`${p.symbol} ${num((p.value / total) * 100, 1)}%`}
            className={cx("transition-[width] duration-200 motion-reduce:transition-none", SHADES[i % SHADES.length])}
            style={{ width: `${(p.value / total) * 100}%` }}
          />
        ))}
        <span className="flex-1 bg-hair" title="Cash" />
      </div>
      <ul className="num mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 text-[12px] text-dim">
        {parts.map((p, i) => (
          <li key={p.symbol} className="flex items-center gap-1.5">
            <span className={cx("size-2", SHADES[i % SHADES.length])} />
            <button type="button" className="font-medium text-ink hover:underline" onClick={() => onOpen(p.symbol)}>
              {p.symbol}
            </button>
            {num((p.value / total) * 100, 1)}%<span className={tone(p.pnl)}>{pct(p.pnl)}</span>
          </li>
        ))}
        <li className="flex items-center gap-1.5">
          <span className="size-2 bg-hair" />
          Cash {num((Math.max(cash, 0) / total) * 100, 1)}%
        </li>
      </ul>
    </div>
  );
}

const secondsSince = (iso: string | null | undefined) => (iso ? (Date.now() - Date.parse(iso)) / 1000 : null);

export function Desk({
  live,
  liveError,
  status,
  etfs,
  benchmarks,
  onOpen,
}: {
  live: Live | null;
  liveError: string | null;
  status: AlpacaStatus | null;
  etfs: number;
  benchmarks?: BenchmarkSettings;
  onOpen: (symbol: string) => void;
}) {
  const connected = Boolean(status?.connected);
  const [period, setPeriod] = useState<Period>("1M");
  const timeframe = PERIODS.find(([p]) => p === period)![1];
  const purchaseDate = benchmarks?.purchase_date;
  const performance = useApi<StrategyPerformance>("strategies/performance", 60_000);
  // Best performer first; strategies with no filled trades keep their usual order at the bottom.
  const ranked = [...STRATEGIES].sort(
    (a, b) => (performance.data?.[b.name]?.pnl ?? -Infinity) - (performance.data?.[a.name]?.pnl ?? -Infinity),
  );
  const account = useApi<AlpacaAccountResponse>(connected ? "alpaca/account" : null, 30_000);
  const history = useApi<AlpacaPortfolioHistory>(
    connected
      ? `alpaca/portfolio-history?period=${period}&timeframe=${timeframe}${purchaseDate ? `&purchase_date=${encodeURIComponent(purchaseDate)}` : ""}`
      : null,
    60_000,
  );
  const acct = account.data?.account;
  const positions = account.data?.positions ?? [];
  const orders = account.data?.orders ?? [];
  const owner = new Map((live?.intents ?? []).map((i) => [i.symbol, i]));

  const workerOk = live?.heartbeat_age_s != null && live.heartbeat_age_s >= 0 && live.heartbeat_age_s < 120;
  const monitorOk = live?.execution_age_s != null && live.execution_age_s >= 0 && live.execution_age_s <= 20;

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-10">
      <Section
        title="Account"
        note={acct ? `Alpaca Paper ${acct.accountNumber}, ${acct.status.toLowerCase()}` : undefined}
        aside={
          connected && (
            <Choice
              label="Equity history period"
              value={period}
              onChange={setPeriod}
              options={PERIODS.map(([p]) => [p, p === "1A" ? "1Y" : p] as const)}
            />
          )
        }
      >
        {!connected && (
          <Message error={Boolean(status?.error)}>
            {status?.error ?? "Add ALPACA_KEY and ALPACA_SECRET to .env to connect your Alpaca Paper account."}
          </Message>
        )}
        {account.error && <Message error>{account.error}</Message>}
        {connected && !acct && !account.error && <Message>Loading paper account…</Message>}
        {acct && (
          <>
            <dl className="grid grid-cols-2 items-end gap-x-8 gap-y-4 border-t border-rule pt-3 sm:grid-cols-3 lg:grid-cols-[auto_repeat(5,minmax(0,1fr))]">
              <div className="col-span-2 sm:col-span-3 lg:col-span-1 lg:pr-6">
                <Figure label="Equity" large>
                  {usd(acct.equity)}
                </Figure>
              </div>
              <Figure label="Today">
                <span className={cx("num", tone(acct.dayPnl))}>
                  {signedUsd(acct.dayPnl)} <span className="text-[13px]">{pct(acct.dayPnlPct)}</span>
                </span>
              </Figure>
              <Figure label="Cash">
                <span className="num">{usd(acct.cash)}</span>
              </Figure>
              <Figure label="Buying power">
                <span className="num">{usd(acct.buyingPower)}</span>
              </Figure>
              <Figure label="Long">
                <span className="num">{usd(acct.longMarketValue ?? 0)}</span>
              </Figure>
              <Figure label="Short">
                <span className="num">{usd(Math.abs(acct.shortMarketValue ?? 0))}</span>
              </Figure>
            </dl>
            <Allocation positions={positions} cash={acct.cash ?? 0} onOpen={onOpen} />
            <div className="mt-4">
              {history.error ? (
                <Message error>{history.error}</Message>
              ) : (
                <EquityChart history={history.data} settings={benchmarks} />
              )}
              {history.data?.benchmarkError && <Message error>{history.data.benchmarkError}</Message>}
              <p className="mt-2 text-[13px] text-dim">
                <a href="#/control" className="underline underline-offset-4">
                  {purchaseDate ? "Benchmark settings" : "Set a benchmark start date"}
                </a>
                {purchaseDate && ` · Started ${purchaseDate} · $10,000 per benchmark`}
              </p>
            </div>
          </>
        )}
      </Section>

      <Section title="Bot" note="Each strategy places real orders on Alpaca Paper. Only one strategy holds a symbol at a time.">
        {liveError && <Message error>{liveError}</Message>}
        {!live && !liveError && <Message>Loading worker status…</Message>}
        {live && (
          <>
            <dl className="grid grid-cols-2 gap-x-8 gap-y-3 border-t border-rule pt-3 lg:grid-cols-4">
              <Figure label="Worker">
                <span className={workerOk ? "" : "text-down"}>
                  {live.heartbeat == null ? "Not started" : workerOk ? "Running" : "Offline"}
                </span>
                {live.heartbeat != null && <span className="text-[13px] text-dim"> · {ago(live.heartbeat_age_s)}</span>}
              </Figure>
              <Figure label="Execution monitor">
                <span className={monitorOk ? "" : "text-down"}>
                  {monitorOk ? (live.phase ?? "unknown") : live.execution_age_s == null ? "Not started" : "Offline"}
                </span>
                {live.execution_age_s != null && (
                  <span className="text-[13px] text-dim"> · {ago(live.execution_age_s)}</span>
                )}
              </Figure>
              <Figure label="New entries">
                <span className={live.enabled && live.armed ? "" : "text-warn"}>
                  {live.stopped ? "Emergency stop" : !live.enabled ? "Disabled in .env" : live.armed ? "On" : "Paused"}
                </span>
              </Figure>
              <Figure label="Active orders and positions">
                <span className="num">{monitorOk ? (live.open_intents ?? "—") : "—"}</span>
              </Figure>
            </dl>

            {performance.error && <Message error>Strategy P&amp;L: {performance.error}</Message>}
            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[900px] border-collapse text-[13px]">
                <thead>
                  <tr>
                    <th className={cx(cell.th, "w-[28px]")}>#</th>
                    <th className={cx(cell.th, "w-[150px]")}>Strategy</th>
                    <th className={cx(cell.th, cell.right, "w-[150px]")}>P&amp;L</th>
                    <th className={cell.th}>Trades</th>
                    <th className={cell.th}>Limits</th>
                    <th className={cx(cell.th, "w-[90px]")}>Last run</th>
                    <th className={cx(cell.th, "w-[30%]")}>Latest</th>
                  </tr>
                </thead>
                <tbody>
                  {ranked.map(({ name, title, trades }, rank) => {
                    const result = performance.data?.[name];
                    const config = live.strategy_config?.[name];
                    const state = live.strategies?.[name];
                    const off = config ? !config.enabled : false;
                    const lastRun = name === "news" ? live.heartbeat_age_s : secondsSince(state?.at);
                    const latest =
                      name === "news"
                        ? live.news_check
                          ? `${live.news_check.ticker}: ${live.news_check.result}`
                          : null
                        : state?.note;
                    return (
                      <tr key={name} className={off ? "text-faint" : ""}>
                        <td className={cx(cell.td, "num text-dim")}>{result ? rank + 1 : "—"}</td>
                        <td className={cx(cell.td, "font-medium")}>
                          {title}
                          {off && <span className="font-normal"> (off)</span>}
                        </td>
                        <td className={cx(cell.td, cell.right, "whitespace-nowrap")}>
                          {result ? (
                            <>
                              <span className={cx("num", tone(result.pnl))}>
                                {signedUsd(result.pnl)} {pct(result.pnl_pct)}
                              </span>
                              <div
                                className="num text-[12px] text-dim"
                                title="Realized from closed shares, open marked at the current price. Return is on capital deployed."
                              >
                                {result.unrealized ? `${signedUsd(result.unrealized)} open · ` : ""}
                                {result.closed ? `${result.wins}/${result.closed} won` : `${result.trades} open`}
                              </div>
                            </>
                          ) : (
                            <span className="text-dim">{performance.loading ? "…" : "No fills"}</span>
                          )}
                        </td>
                        <td className={cell.td}>{trades(config)}</td>
                        <td className={cell.td}>{limits(name, config, etfs)}</td>
                        <td className={cx(cell.td, "whitespace-nowrap")}>{off ? "—" : ago(lastRun)}</td>
                        <td className={cell.td}>
                          {off ? "Disabled in config" : (latest ?? "No activity yet")}
                          {!off && state?.orders?.length ? (
                            <div className="mt-1 text-dim">
                              {state.orders
                                .slice(0, 8)
                                .map((o) => `${o.symbol} ${o.side}: ${o.result}`)
                                .join("; ")}
                            </div>
                          ) : null}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Section>

      {connected && account.data && (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-10 xl:grid-cols-[repeat(2,minmax(0,1fr))]">
          <Section title="Positions" note={String(positions.length)}>
            {positions.length === 0 ? (
              <Message>No open positions.</Message>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse text-[13px]">
                  <thead>
                    <tr>
                      <th className={cell.th}>Symbol</th>
                      <th className={cell.th}>Strategy</th>
                      <th className={cell.th}>Side</th>
                      <th className={cx(cell.th, cell.right)}>Qty</th>
                      <th className={cx(cell.th, cell.right)}>Entry</th>
                      <th className={cx(cell.th, cell.right)}>Last</th>
                      <th className={cx(cell.th, cell.right)}>Unrealized</th>
                    </tr>
                  </thead>
                  <tbody>
                    {positions.map((p) => {
                      const intent = owner.get(p.symbol);
                      return (
                        <tr key={p.symbol}>
                          <td className={cell.td}>
                            <SymbolLink symbol={p.symbol} onOpen={onOpen} />
                          </td>
                          <td className={cell.td}>
                            {intent ? STRATEGY_TITLES[intent.strategy] : <span className="text-faint">External</span>}
                            {intent?.exit_requested && <span className="text-dim"> · closing</span>}
                          </td>
                          <td className={cell.td}>{p.side}</td>
                          <td className={cx(cell.td, cell.right)}>{p.qty}</td>
                          <td className={cx(cell.td, cell.right)}>{num(p.avgEntryPrice)}</td>
                          <td className={cx(cell.td, cell.right)}>{num(p.currentPrice)}</td>
                          <td className={cx(cell.td, cell.right, tone(p.unrealizedPl))}>
                            {signedUsd(p.unrealizedPl)} {pct(p.unrealizedPlpc)}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Section>

          <Section
            title="Recent orders"
            note={orders.length ? `Latest ${Math.min(orders.length, 20)}` : "0"}
            aside={<a className="text-[13px] underline underline-offset-4" href="#/activity">Activity log</a>}
          >
            {orders.length === 0 ? (
              <Message>No orders yet.</Message>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[560px] border-collapse text-[13px]">
                  <thead>
                    <tr>
                      <th className={cell.th}>Submitted (ET)</th>
                      <th className={cell.th}>Symbol</th>
                      <th className={cell.th}>Side</th>
                      <th className={cell.th}>Type</th>
                      <th className={cx(cell.th, cell.right)}>Qty</th>
                      <th className={cx(cell.th, cell.right)}>Fill</th>
                      <th className={cell.th}>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {orders.slice(0, 20).map((o) => (
                      <tr key={o.id}>
                        <td className={cx(cell.td, "num whitespace-nowrap text-dim")}>{etDateTime(o.submittedAt)}</td>
                        <td className={cell.td}>
                          <SymbolLink symbol={o.symbol} onOpen={onOpen} />
                        </td>
                        <td className={cell.td}>{o.side}</td>
                        <td className={cell.td}>{o.type.replace("_", " ")}</td>
                        <td className={cx(cell.td, cell.right)}>{o.qty}</td>
                        <td className={cx(cell.td, cell.right)}>{num(o.filledAvgPrice)}</td>
                        <td className={cx(cell.td, o.status === "filled" ? "" : "text-dim")}>
                          {o.status.replace("_", " ")}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Section>
        </div>
      )}

    </div>
  );
}
