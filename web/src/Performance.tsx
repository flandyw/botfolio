import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import type { AlpacaAccountResponse, AlpacaPortfolioHistory, AlpacaStatus } from "./api";
import { cx } from "./classes";
import { pct, signedUsd, tone, usd } from "./format";
import { PerformanceChart } from "./PerformanceChart";
import { portfolioPerformance } from "./performanceData";
import { PositionPerformance } from "./PositionPerformance";
import { Button, Choice, Figure, Message, Section } from "./ui";
import { useApi } from "./useApi";

const PERIODS = [
  ["1D", "1D", "5Min"],
  ["7D", "1W", "1D"],
  ["1M", "1M", "1D"],
  ["3M", "3M", "1D"],
  ["1A", "1Y", "1D"],
] as const;
const MODES = [["pnl", "P&L"], ["return", "Return"]] as const;
const EMPTY_HISTORY = "No funded portfolio history for this period.";

function Panel({
  title,
  aside,
  children,
}: {
  title: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="min-w-0 border border-rule px-3 py-3 sm:px-4">
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h3 className="font-semibold">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function Performance({
  status,
  statusError,
  onOpen,
}: {
  status?: AlpacaStatus;
  statusError?: string;
  onOpen: (symbol: string) => void;
}) {
  const connected = Boolean(status?.connected);
  const [period, setPeriod] = useState<(typeof PERIODS)[number][0]>("1M");
  const [mode, setMode] = useState<(typeof MODES)[number][0]>("pnl");
  const timeframe = PERIODS.find(([p]) => p === period)![2];
  const intraday = timeframe !== "1D";
  const history = useApi<AlpacaPortfolioHistory>(
    connected
      ? `alpaca/portfolio-history?period=${period}&timeframe=${timeframe}${intraday ? "&pnl_reset=no_reset" : ""}`
      : null,
    60_000,
  );
  const account = useApi<AlpacaAccountResponse>(connected ? "alpaca/account" : null, 30_000);
  const stats = useMemo(() => portfolioPerformance(history.data?.points ?? []), [history.data]);
  const equity = stats.latest?.equity;
  const pnl = stats.latest?.profitLoss;
  const returns = stats.latest?.profitLossPct;
  const pnlTitle = mode === "pnl" ? "Cumulative P&L" : "Cumulative return";
  const changeTitle = intraday ? "Intraday P&L changes" : "Session P&L changes";

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-8">
      <header className="flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
        <div>
          <h1 className="text-[29px] leading-tight font-semibold tracking-tight">Performance</h1>
          <p className="mt-0.5 text-[13px] text-dim">Alpaca Paper · {intraday ? "5-minute history, ET" : "Daily history"}</p>
        </div>
        {connected && (
          <Choice
            label="Performance period"
            value={period}
            onChange={setPeriod}
            options={PERIODS.map(([p, label]) => [p, label] as const)}
          />
        )}
      </header>

      {!status && !statusError && <Message>Connecting to Alpaca Paper…</Message>}
      {status && !connected && (
        <Message error={Boolean(status.error)}>{status.error ?? "Alpaca Paper is disconnected."}</Message>
      )}
      {connected && (
        <>
          <Section title="Portfolio history" note="P&L and return reported by Alpaca">
            {history.error && (
              <div className="flex flex-wrap items-center gap-3">
                <Message error>{history.error}</Message>
                <Button onClick={history.reload}>Retry history</Button>
              </div>
            )}
            {history.loading && <Message>Loading portfolio history…</Message>}
            {history.data && (
              <>
                <dl className="mb-4 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-rule pt-3 md:grid-cols-4">
                  <Figure label="Period P&L">
                    <span className={cx("num text-[21px] font-medium", tone(pnl))}>{signedUsd(pnl)}</span>
                  </Figure>
                  <Figure label="Period return">
                    <span className={cx("num text-[21px] font-medium", tone(returns))}>{pct(returns)}</span>
                  </Figure>
                  <Figure label="Closing equity">
                    <span className="num text-[21px] font-medium">{usd(equity)}</span>
                  </Figure>
                  <Figure label="Max drawdown">
                    <span className={cx("num text-[21px] font-medium", tone(stats.maxDrawdown))}>
                      {pct(stats.maxDrawdown)}
                    </span>
                  </Figure>
                </dl>
                {stats.latest ? (
                  <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
                    <Panel
                      title={pnlTitle}
                      aside={<Choice label="Cumulative chart metric" value={mode} onChange={setMode} options={MODES} />}
                    >
                      <PerformanceChart
                        title={pnlTitle}
                        metric={mode}
                        points={mode === "pnl" ? stats.pnl : stats.returns}
                        intraday={intraday}
                        empty="Alpaca returned no data for this metric."
                      />
                    </Panel>
                    <Panel title="Drawdown">
                      <PerformanceChart
                        title="Equity drawdown"
                        metric="drawdown"
                        points={stats.drawdown}
                        intraday={intraday}
                        empty="Drawdown requires at least one positive equity point."
                      />
                      <p className="mt-2 text-[12px] text-dim">From the period's running equity peak. Cash transfers may affect drawdown.</p>
                    </Panel>
                    <div className="lg:col-span-2">
                      <Panel title={changeTitle}>
                        <PerformanceChart
                          title={changeTitle}
                          metric="change"
                          points={stats.changes}
                          intraday={intraday}
                          empty="At least two consecutive P&L points are required."
                        />
                        <p className="mt-2 text-[12px] text-dim">Change in reported P&L between consecutive intervals.</p>
                      </Panel>
                    </div>
                  </div>
                ) : (
                  <Message>{EMPTY_HISTORY}</Message>
                )}
              </>
            )}
          </Section>
          <Section title="Open positions" note="Live snapshot · independent of the selected period">
            {account.loading && <Message>Loading open positions…</Message>}
            {account.error && (
              <div className="flex flex-wrap items-center gap-3">
                <Message error>{account.error}</Message>
                <Button onClick={account.reload}>Retry positions</Button>
              </div>
            )}
            {account.data && <PositionPerformance positions={account.data.positions} onOpen={onOpen} />}
          </Section>
        </>
      )}
    </div>
  );
}
