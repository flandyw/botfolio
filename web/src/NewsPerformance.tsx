import type { NewsPerformance as Data } from "./api";
import { cell, cx } from "./classes";
import { pct, signedUsd, tone } from "./format";
import { Message, Section } from "./ui";
import { useApi } from "./useApi";

type Group = Data["sources"];

function Table({ title, groups }: { title: string; groups: Group }) {
  const rows = Object.entries(groups).sort(([, a], [, b]) => b.pnl - a.pnl);
  const reach = Math.max(...rows.map(([, g]) => Math.abs(g.pnl)), 1e-9);
  return (
    <table className="w-full text-[13px]">
      <thead>
        <tr>
          <th className={cell.th}>{title}</th>
          <th className={cx(cell.th, cell.right)}>Trades</th>
          <th className={cx(cell.th, cell.right)}>Win rate</th>
          <th className={cx(cell.th, cell.right)}>Return</th>
          <th className={cx(cell.th, cell.right)}>P&L</th>
          <th className={cx(cell.th, "w-1/4")} />
        </tr>
      </thead>
      <tbody>
        {rows.map(([name, g]) => (
          <tr key={name}>
            <td className={cell.td}>{name}</td>
            <td className={cx(cell.td, cell.right)}>{g.trades}</td>
            <td className={cx(cell.td, cell.right)}>{g.closed ? `${Math.round((g.wins / g.closed) * 100)}%` : "—"}</td>
            <td className={cx(cell.td, cell.right, tone(g.pnl_pct))}>{pct(g.pnl_pct)}</td>
            <td className={cx(cell.td, cell.right, tone(g.pnl))}>{signedUsd(g.pnl)}</td>
            <td className={cell.td}>
              <div className="relative mt-1.5 h-2 bg-hair">
                <div
                  className={cx("absolute inset-y-0", g.pnl < 0 ? "right-1/2 bg-down" : "left-1/2 bg-up")}
                  style={{ width: `${(Math.abs(g.pnl) / reach) * 50}%` }}
                />
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Which news sources and AI models the filled news trades made or lost money on. */
export function NewsPerformance() {
  const { data, error } = useApi<Data>("news/performance", 60_000);
  if (error || !data) return error ? <Message error>{error}</Message> : null;
  return (
    <Section title="News trades by source and model" note="Gross, from Alpaca Paper fills · all time">
      {Object.keys(data.sources).length ? (
        <div className="grid gap-x-8 gap-y-4 border-t border-rule pt-1 lg:grid-cols-2">
          <Table title="Source" groups={data.sources} />
          <Table title="AI model" groups={data.models} />
        </div>
      ) : (
        <Message>No filled news trades yet.</Message>
      )}
    </Section>
  );
}
