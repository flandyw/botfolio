import { useMemo, useState } from "react";
import type { Stat } from "./api";
import { GROUPS, fmt, friendly, pct, tone } from "./format";

type Col = {
  key: keyof Stat;
  label: string;
  render: (s: Stat) => string;
  cls?: (s: Stat) => string;
  title?: string;
};
const COLS: Col[] = [
  {
    key: "total_return_pct",
    label: "Return",
    render: (s) => pct(s.total_return_pct),
    cls: (s) => tone(s.total_return_pct),
    title: "After costs and tax owed so far",
  },
  {
    key: "max_drawdown_pct",
    label: "Max DD",
    render: (s) => pct(s.max_drawdown_pct == null ? null : -s.max_drawdown_pct),
  },
  { key: "sharpe", label: "Sharpe", render: (s) => fmt(s.sharpe) },
  { key: "n_trades", label: "Trades", render: (s) => fmt(s.n_trades, 0) },
  { key: "win_rate", label: "Win %", render: (s) => fmt(s.win_rate, 1) },
  { key: "profit_factor", label: "PF", render: (s) => fmt(s.profit_factor) },
  {
    key: "avg_cost_pct",
    label: "Cost/trade",
    render: (s) => pct(s.avg_cost_pct, 3),
    title: "Spread, slippage, commission and fees, % of notional",
  },
  {
    key: "q_mc",
    label: "q",
    render: (s) => fmt(s.q_mc, 3),
    title: "False-discovery-adjusted p-value vs random entries; < 0.05 counts",
  },
];

type Props = {
  stats: Record<string, Stat>;
  visible: Set<string>;
  colors: Record<string, string>;
  dashed: Set<string>;
  onToggle: (name: string) => void;
  onPick?: (name: string) => void;
};

export function RankingTable({ stats, visible, colors, dashed, onToggle, onPick }: Props) {
  const [sort, setSort] = useState<{ key: keyof Stat; dir: 1 | -1 }>({
    key: "total_return_pct",
    dir: -1,
  });
  const rows = useMemo(() => {
    const val = (s: Stat) => (s[sort.key] as number | null) ?? -Infinity;
    return Object.entries(stats).sort(([, a], [, b]) => sort.dir * (val(a) - val(b)));
  }, [stats, sort]);
  const head = (key: keyof Stat, label: string, title?: string) => (
    <th
      key={key}
      title={title}
      className="num sortable"
      onClick={() => setSort((s) => ({ key, dir: s.key === key ? (-s.dir as 1 | -1) : -1 }))}
    >
      {label}
      {sort.key === key ? (sort.dir < 0 ? " ↓" : " ↑") : ""}
    </th>
  );
  return (
    <div className="table-scroll">
      <table>
        <thead>
          <tr>
            <th aria-label="Show on chart" />
            <th>Portfolio</th>
            <th>Type</th>
            {COLS.map((c) => head(c.key, c.label, c.title))}
            <th>Verdict</th>
          </tr>
        </thead>
        <tbody>
          {rows.map(([name, s]) => (
            <tr key={name} className={visible.has(name) ? "" : "dim"}>
              <td>
                <label
                  className={`swatch ${dashed.has(name) ? "dashed" : ""}`}
                  style={{ "--c": colors[name] } as React.CSSProperties}
                >
                  <input
                    type="checkbox"
                    checked={visible.has(name)}
                    onChange={() => onToggle(name)}
                    aria-label={`Show ${friendly(name)}`}
                  />
                </label>
              </td>
              <td>
                {onPick ? (
                  <button
                    className="link"
                    onClick={() => onPick(name)}
                    title="Open on the price chart"
                  >
                    {friendly(name)}
                  </button>
                ) : (
                  friendly(name)
                )}
              </td>
              <td>
                <span className={`tag ${s.group}`}>{GROUPS[s.group]}</span>
              </td>
              {COLS.map((c) => (
                <td key={c.key} className={`num ${c.cls?.(s) ?? ""}`}>
                  {c.render(s)}
                </td>
              ))}
              <td className="muted small">{s.verdict}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
