import { useState } from "react";
import type {
  AlpacaAccountResponse,
  AlpacaPortfolioHistory,
  AlpacaStatus,
  Live,
  StrategyConfig,
  StrategyName,
} from "./api";
import { EquityChart } from "./EquityChart";
import { ago, etDateTime, num, pct, signedUsd, tone, usd } from "./format";
import { cell, cx } from "./classes";
import { Choice, Figure, Message, Section, SymbolLink } from "./ui";
import { useApi } from "./useApi";

const PERIODS = [
  ["1D", "5Min"],
  ["1W", "15Min"],
  ["1M", "1D"],
  ["3M", "1D"],
  ["1A", "1D"],
] as const;
type Period = (typeof PERIODS)[number][0];

const STRATEGIES: { name: StrategyName; title: string; trades: (c?: StrategyConfig) => string }[] = [
  { name: "news", title: "News + AI", trades: () => "Fresh watchlist news with an AI buy or sell call" },
  {
    name: "momentum",
    title: "Intraday momentum",
    trades: () => "30-minute closes outside the usual range, long or short, flat by the close",
  },
  {
    name: "etf_trend",
    title: "ETF trend",
    trades: (c) => `Watchlist ETFs above their ${c?.sma_days ?? 200}-day average, held overnight`,
  },
];
const TITLES = Object.fromEntries(STRATEGIES.map((s) => [s.name, s.title])) as Record<StrategyName, string>;

function limits(name: StrategyName, c: StrategyConfig | undefined, etfs: number) {
  if (!c) return "—";
  if (name === "etf_trend") return `${num(c.allocation_pct, 0)}% of equity across ${etfs} ETFs`;
  return `${c.max_positions} positions, ${num(c.position_pct, 0)}% of equity each, ${num(c.risk_pct, 1)}% at risk`;
}

const secondsSince = (iso: string | null | undefined) => (iso ? (Date.now() - Date.parse(iso)) / 1000 : null);

export function Desk({
  live,
  liveError,
  status,
  etfs,
  onOpen,
}: {
  live: Live | null;
  liveError: string | null;
  status: AlpacaStatus | null;
  etfs: number;
  onOpen: (symbol: string) => void;
}) {
  const connected = Boolean(status?.connected);
  const [period, setPeriod] = useState<Period>("1M");
  const timeframe = PERIODS.find(([p]) => p === period)![1];
  const account = useApi<AlpacaAccountResponse>(connected ? "alpaca/account" : null, 30_000);
  const history = useApi<AlpacaPortfolioHistory>(
    connected ? `alpaca/portfolio-history?period=${period}&timeframe=${timeframe}` : null,
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
            {status?.error ?? "Add ALPACA_KEY and ALPACA_SECRET for the paper account to .env to see the account."}
          </Message>
        )}
        {account.error && <Message error>{account.error}</Message>}
        {connected && !acct && !account.error && <Message>Loading the paper account…</Message>}
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
            <div className="mt-4">
              {history.error ? (
                <Message error>{history.error}</Message>
              ) : (
                <EquityChart points={history.data?.points ?? []} baseValue={history.data?.baseValue ?? null} />
              )}
            </div>
          </>
        )}
      </Section>

      <Section title="Bot" note="Every strategy places real orders in the paper account. One strategy holds a symbol at a time.">
        {liveError && <Message error>{liveError}</Message>}
        {!live && !liveError && <Message>Loading worker status…</Message>}
        {live && (
          <>
            <dl className="grid grid-cols-2 gap-x-8 gap-y-3 border-t border-rule pt-3 lg:grid-cols-4">
              <Figure label="Worker">
                <span className={workerOk ? "" : "text-down"}>
                  {live.heartbeat == null ? "Not seen yet" : workerOk ? "Running" : "Silent"}
                </span>
                {live.heartbeat != null && <span className="text-[13px] text-dim"> · {ago(live.heartbeat_age_s)}</span>}
              </Figure>
              <Figure label="Execution monitor">
                <span className={monitorOk ? "" : "text-down"}>
                  {monitorOk ? (live.phase ?? "unknown") : live.execution_age_s == null ? "Not seen yet" : "Silent"}
                </span>
                {live.execution_age_s != null && (
                  <span className="text-[13px] text-dim"> · {ago(live.execution_age_s)}</span>
                )}
              </Figure>
              <Figure label="New entries">
                <span className={live.enabled && live.armed ? "" : "text-warn"}>
                  {live.stopped ? "Emergency stop" : !live.enabled ? "Disabled in .env" : live.armed ? "On" : "Off"}
                </span>
              </Figure>
              <Figure label="Working orders and positions">
                <span className="num">{monitorOk ? (live.open_intents ?? "—") : "—"}</span>
              </Figure>
            </dl>

            <div className="mt-5 overflow-x-auto">
              <table className="w-full min-w-[760px] border-collapse text-[13px]">
                <thead>
                  <tr>
                    <th className={cx(cell.th, "w-[150px]")}>Strategy</th>
                    <th className={cell.th}>Trades</th>
                    <th className={cell.th}>Limits</th>
                    <th className={cx(cell.th, "w-[90px]")}>Last run</th>
                    <th className={cx(cell.th, "w-[30%]")}>Latest</th>
                  </tr>
                </thead>
                <tbody>
                  {STRATEGIES.map(({ name, title, trades }) => {
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
                        <td className={cx(cell.td, "font-medium")}>
                          {title}
                          {off && <span className="font-normal"> (off)</span>}
                        </td>
                        <td className={cell.td}>{trades(config)}</td>
                        <td className={cell.td}>{limits(name, config, etfs)}</td>
                        <td className={cx(cell.td, "whitespace-nowrap")}>{off ? "—" : ago(lastRun)}</td>
                        <td className={cell.td}>
                          {off ? "Disabled in config" : (latest ?? "Nothing yet")}
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
                      <th className={cx(cell.th, cell.right)}>Unrealised</th>
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
                            {intent ? TITLES[intent.strategy] : <span className="text-faint">not the bot's</span>}
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

          <Section title="Recent orders" note={orders.length ? `latest ${Math.min(orders.length, 20)}` : "0"}>
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
