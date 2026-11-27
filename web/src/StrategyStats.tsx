import type { StrategyPerformance } from "./api";
import { cell, cx } from "./classes";
import { num, pct, signedUsd, tone } from "./format";
import { returnBins, tradeQuality } from "./performanceData";
import { STRATEGIES } from "./strategyInfo";
import { Message } from "./ui";

/** Per-strategy results from broker fills: P&L bars, closed-trade quality and the spread of trade returns. */
export function StrategyStats({ data }: { data: StrategyPerformance }) {
  const rows = STRATEGIES.flatMap(({ name, title }) => (data[name] ? [{ title, result: data[name] }] : [])).sort(
    (a, b) => b.result.pnl - a.result.pnl,
  );
  if (!rows.length) return <Message>No filled bot trades yet.</Message>;
  const scale = Math.max(...rows.map((r) => Math.abs(r.result.pnl)));
  const bins = returnBins(rows.flatMap((r) => r.result.returns));
  const tallest = Math.max(...bins.map((b) => b.count), 1);
  const closed = bins.reduce((sum, b) => sum + b.count, 0);

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <div className="min-w-0 overflow-x-auto border border-rule px-3 py-2 sm:px-4">
        <table className="w-full min-w-[640px] border-collapse text-[13px]">
          <thead>
            <tr>
              <th className={cell.th}>Strategy</th>
              <th className={cx(cell.th, "w-[26%]")}>P&L</th>
              <th className={cx(cell.th, cell.right)}>Trades</th>
              <th className={cx(cell.th, cell.right)}>Win rate</th>
              <th className={cx(cell.th, cell.right)}>Avg win</th>
              <th className={cx(cell.th, cell.right)}>Avg loss</th>
              <th className={cx(cell.th, cell.right)} title="Average realized P&L per closed trade">
                Expectancy
              </th>
              <th className={cx(cell.th, cell.right)} title="Gross wins divided by gross losses">
                Profit factor
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ title, result }) => {
              const q = tradeQuality(result);
              return (
                <tr key={title}>
                  <td className={cx(cell.td, "font-medium whitespace-nowrap")}>{title}</td>
                  <td className={cell.td}>
                    <div className="flex items-center gap-2">
                      <div className="relative h-3 min-w-10 flex-1 bg-hair/60" aria-hidden="true">
                        <span className="absolute inset-y-0 left-1/2 border-l border-rule" />
                        <span
                          className={cx(
                            "absolute inset-y-0.5 transition-[width] duration-200 motion-reduce:transition-none",
                            result.pnl < 0 ? "right-1/2 bg-down/75" : "left-1/2 bg-up/75",
                          )}
                          style={{ width: `${scale ? (Math.abs(result.pnl) / scale) * 50 : 0}%` }}
                        />
                      </div>
                      <span className={cx("num w-20 text-right", tone(result.pnl))}>{signedUsd(result.pnl)}</span>
                    </div>
                  </td>
                  <td className={cx(cell.td, cell.right, "num")}>
                    {result.closed}
                    {result.trades > result.closed && (
                      <span className="text-dim"> +{result.trades - result.closed} open</span>
                    )}
                  </td>
                  <td className={cx(cell.td, cell.right, "num")}>{q.winRate == null ? "—" : `${num(q.winRate, 0)}%`}</td>
                  <td className={cx(cell.td, cell.right, "num", tone(q.avgWin))}>{signedUsd(q.avgWin)}</td>
                  <td className={cx(cell.td, cell.right, "num", tone(q.avgLoss))}>{signedUsd(q.avgLoss)}</td>
                  <td className={cx(cell.td, cell.right, "num", tone(q.expectancy))}>{signedUsd(q.expectancy)}</td>
                  <td className={cx(cell.td, cell.right, "num")}>{num(q.profitFactor)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <section className="min-w-0 border border-rule px-3 py-3 sm:px-4">
        <div className="mb-2 flex items-baseline justify-between gap-3">
          <h3 className="font-semibold">Trade returns</h3>
          <span className="text-[12px] text-dim">{closed} closed trades, % of cost</span>
        </div>
        {bins.length ? (
          <>
            <div className="flex h-36 items-end gap-px border-b border-rule" role="img" aria-label="Distribution of closed-trade returns">
              {bins.map((b) => (
                <div
                  key={b.from}
                  title={`${pct(b.from)} to ${pct(b.to)}: ${b.count} trades`}
                  className={cx(
                    "flex-1 transition-[height] duration-200 motion-reduce:transition-none",
                    b.to <= 0 ? "bg-down/75" : "bg-up/75",
                  )}
                  style={{ height: `${(b.count / tallest) * 100}%`, minHeight: b.count ? 2 : 0 }}
                />
              ))}
            </div>
            <div className="num mt-1 flex justify-between text-[11px] text-dim">
              <span>{pct(bins[0].from, 1)}</span>
              <span>0%</span>
              <span>{pct(bins.at(-1)!.to, 1)}</span>
            </div>
          </>
        ) : (
          <Message>No closed trades yet.</Message>
        )}
      </section>
    </div>
  );
}
