import { useMemo, useState } from "react";
import type { AlpacaPosition } from "./api";
import { cx } from "./classes";
import { pct, signedUsd, tone, usd } from "./format";
import { openPositionPerformance } from "./performanceData";
import { Choice, Figure, Message, SymbolLink } from "./ui";

const MODES = [["pnl", "Unrealized P&L"], ["exposure", "Market value"]] as const;

export function PositionPerformance({
  positions,
  onOpen,
}: {
  positions: AlpacaPosition[];
  onOpen: (symbol: string) => void;
}) {
  const [mode, setMode] = useState<(typeof MODES)[number][0]>("pnl");
  const stats = useMemo(() => openPositionPerformance(positions), [positions]);
  const rows = stats.rows.toSorted((a, b) => Math.abs(b[mode]) - Math.abs(a[mode]) || a.symbol.localeCompare(b.symbol));
  const scale = Math.abs(rows[0]?.[mode] ?? 0);
  const title = mode === "pnl" ? "Unrealized P&L by symbol" : "Market value by symbol";

  if (!rows.length) return <Message>No open positions.</Message>;

  return (
    <>
      <dl className="mb-4 grid grid-cols-2 gap-x-6 gap-y-3 border-t border-rule pt-3 md:grid-cols-4">
        <Figure label="Open P&L">
          <span className={cx("num text-[21px] font-medium", tone(stats.pnl))}>{signedUsd(stats.pnl)}</span>
        </Figure>
        <Figure label="Long exposure">
          <span className="num text-[21px] font-medium">{usd(stats.long)}</span>
        </Figure>
        <Figure label="Short exposure">
          <span className="num text-[21px] font-medium">{usd(stats.short)}</span>
        </Figure>
        <Figure label="Net exposure">
          <span className="num text-[21px] font-medium">{signedUsd(stats.long - stats.short)}</span>
        </Figure>
      </dl>
      <section className="min-w-0 border border-rule px-3 py-3 sm:px-4">
        <div className="mb-2 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
          <h3 className="font-semibold">{title}</h3>
          <Choice label="Open position chart metric" value={mode} onChange={setMode} options={MODES} />
        </div>
        <ul aria-label={title} className="divide-y divide-hair border-t border-hair">
          {rows.map((row) => {
            const value = row[mode];
            const width = scale ? (Math.abs(value) / scale) * 50 : 0;
            return (
              <li
                key={row.symbol}
                className="grid grid-cols-[minmax(3.25rem,auto)_minmax(0,1fr)_minmax(5.5rem,auto)] items-center gap-2 py-2 text-[13px] sm:grid-cols-[7rem_minmax(0,1fr)_11rem] sm:gap-4"
              >
                <div className="min-w-0 break-all">
                  <SymbolLink symbol={row.symbol} onOpen={onOpen} />
                  {mode === "exposure" && <span className="ml-2 hidden text-[12px] text-dim sm:inline">{row.side}</span>}
                </div>
                <div className="relative h-4 bg-hair/60" aria-hidden="true">
                  <span className="absolute inset-y-0 left-1/2 border-l border-rule" />
                  <span
                    className={cx(
                      "absolute inset-y-0.5 transition-[width] duration-150 motion-reduce:transition-none",
                      value < 0 ? "right-1/2 bg-down/75" : "left-1/2 bg-up/75",
                    )}
                    style={{ width: `${width}%` }}
                  />
                </div>
                <div className="flex min-w-0 flex-wrap items-baseline justify-end gap-x-2 text-right">
                  <span className={cx("num", tone(value))}>{signedUsd(value)}</span>
                  {mode === "pnl" && <span className="num text-[12px] text-dim">{pct(row.unrealizedPlpc)}</span>}
                </div>
              </li>
            );
          })}
        </ul>
      </section>
    </>
  );
}
