import type { AlpacaHistoryPoint } from "./api";
import { cx } from "./classes";
import { pct, signedUsd } from "./format";
import { pnlCalendar } from "./performanceData";
import { Message } from "./ui";

const DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"];

/** One cell per session, shaded by the size of that day's gain or loss. */
export function PnlCalendar({ points }: { points: AlpacaHistoryPoint[] }) {
  const weeks = pnlCalendar(points);
  const scale = Math.max(...weeks.flatMap((w) => w.days.map((d) => Math.abs(d?.pnl ?? 0))), 0);
  if (!weeks.length) return <Message>At least two daily points are required.</Message>;
  return (
    <div className="overflow-x-auto">
      <div className="grid w-max grid-cols-[4.5rem_repeat(5,5.5rem)] gap-px text-[12px]">
        <span />
        {DAYS.map((d) => (
          <span key={d} className="pb-1 text-center text-dim">
            {d}
          </span>
        ))}
        {weeks.toReversed().map((week) => (
          <div key={week.monday} className="contents">
            <span className="num self-center text-dim">{week.monday.slice(5)}</span>
            {week.days.map((day, i) =>
              day ? (
                <div
                  key={i}
                  title={`${day.date}: ${signedUsd(day.pnl)} ${pct(day.pct)}`}
                  className="num relative h-10 px-1.5 py-0.5 text-right leading-[1.15rem]"
                >
                  <span
                    className={cx("absolute inset-0", day.pnl < 0 ? "bg-down" : "bg-up")}
                    style={{ opacity: scale ? 0.1 + 0.45 * (Math.abs(day.pnl) / scale) : 0.1 }}
                  />
                  <span className="relative block font-medium">{signedUsd(day.pnl, 0)}</span>
                  <span className="relative block text-[11px] text-dim">{pct(day.pct)}</span>
                </div>
              ) : (
                <div key={i} className="h-10 bg-hair/40" />
              ),
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
