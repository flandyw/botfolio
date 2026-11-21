import { useEffect, useMemo, useState } from "react";
import { AreaSeries, LineSeries, LineStyle, type Time } from "lightweight-charts";
import type { AlpacaHistoryPoint, Point, Stat, Trade } from "./api";
import { useChart } from "./chart";
import { money, num, pct } from "./format";

/* -------------------------------------------------------------------------- */
/* 1. Risk vs. Return Scatter Plot (Max Drawdown % vs. Total Return %)        */
/* -------------------------------------------------------------------------- */

export type StatRow = Stat & { portfolio: string };

export function RiskReturnScatter({
  stats,
  colors,
  onSelect,
}: {
  stats: StatRow[];
  colors: Record<string, string>;
  onSelect?: (portfolio: string) => void;
}) {
  const [hover, setHover] = useState<StatRow | null>(null);
  const pts = useMemo(
    () => stats.filter((s) => s.total_return_pct != null && s.max_drawdown_pct != null),
    [stats],
  );

  if (!pts.length) {
    return <div className="muted small">Not enough portfolio statistics for scatter plot.</div>;
  }

  const W = 540;
  const H = 260;
  const pad = { l: 50, r: 22, t: 18, b: 36 };
  const xs = pts.map((p) => Math.abs(p.max_drawdown_pct ?? 0));
  const ys = pts.map((p) => p.total_return_pct ?? 0);
  const maxX = Math.max(2, ...xs) * 1.15;
  const minY = Math.min(-1, ...ys) * 1.15;
  const maxY = Math.max(1, ...ys) * 1.15;
  const xPos = (x: number) => pad.l + (x / maxX) * (W - pad.l - pad.r);
  const yPos = (y: number) => pad.t + ((maxY - y) / (maxY - minY || 1)) * (H - pad.t - pad.b);

  const zeroY = yPos(0);
  const xTicks = [0, maxX * 0.33, maxX * 0.66, maxX];
  const yTicks = [minY, 0, maxY * 0.5, maxY];

  return (
    <div style={{ position: "relative" }}>
      <svg viewBox={`0 0 ${W} ${H}`} className="svg-chart" style={{ height: 260 }}>
        {yTicks.map((t, idx) => (
          <g key={`y-${idx}`}>
            <line
              x1={pad.l}
              x2={W - pad.r}
              y1={yPos(t)}
              y2={yPos(t)}
              stroke={t === 0 ? "var(--line-2)" : "var(--grid)"}
              strokeDasharray={t === 0 ? "4 3" : undefined}
            />
            <text
              x={pad.l - 7}
              y={yPos(t) + 3}
              textAnchor="end"
              fill="var(--muted)"
              fontSize="10"
              className="mono"
            >
              {t >= 0 ? `+${t.toFixed(1)}%` : `${t.toFixed(1)}%`}
            </text>
          </g>
        ))}
        {xTicks.map((t, idx) => (
          <g key={`x-${idx}`}>
            <line x1={xPos(t)} x2={xPos(t)} y1={pad.t} y2={H - pad.b} stroke="var(--grid)" />
            <text
              x={xPos(t)}
              y={H - pad.b + 14}
              textAnchor="middle"
              fill="var(--muted)"
              fontSize="10"
              className="mono"
            >
              -{t.toFixed(1)}%
            </text>
          </g>
        ))}

        {/* Optimal quadrant hint */}
        <text x={pad.l + 6} y={pad.t + 11} fill="var(--up)" fontSize="9.5" opacity={0.75}>
          ↑ High Return · Low Drawdown
        </text>
        <line x1={pad.l} x2={W - pad.r} y1={zeroY} y2={zeroY} stroke="var(--line-2)" strokeWidth={1} />

        {/* Axis labels */}
        <text x={(pad.l + W - pad.r) / 2} y={H - 4} textAnchor="middle" fill="var(--muted)" fontSize="10.5">
          Max Drawdown (%)
        </text>

        {pts.map((s) => {
          const cx = xPos(Math.abs(s.max_drawdown_pct ?? 0));
          const cy = yPos(s.total_return_pct ?? 0);
          const color = colors[s.portfolio] ?? "var(--accent)";
          const r = Math.max(5, Math.min(12, 4 + Math.sqrt(Math.max(1, s.n_trades)) * 0.45));
          const active = hover?.portfolio === s.portfolio;
          return (
            <g
              key={s.portfolio}
              style={{ cursor: onSelect ? "pointer" : "default" }}
              onMouseEnter={() => setHover(s)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelect?.(s.portfolio)}
            >
              <circle
                cx={cx}
                cy={cy}
                r={active ? r + 2.5 : r}
                fill={color}
                fillOpacity={active ? 0.95 : 0.72}
                stroke={active ? "var(--text)" : color}
                strokeWidth={active ? 2 : 1}
              />
              {pts.length <= 14 && (
                <text
                  x={cx + r + 4}
                  y={cy + 3}
                  fill="var(--text-2)"
                  fontSize="9.5"
                  className="mono"
                >
                  {s.portfolio.length > 14 ? `${s.portfolio.slice(0, 13)}…` : s.portfolio}
                </text>
              )}
            </g>
          );
        })}
      </svg>

      {hover && (
        <div className="svg-tooltip" style={{ right: 8, top: 8 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>{hover.portfolio}</div>
          <div className="mono small">
            Return: <span className={(hover.total_return_pct ?? 0) >= 0 ? "up" : "down"}>{pct(hover.total_return_pct)}</span>
          </div>
          <div className="mono small">Max DD: {pct(hover.max_drawdown_pct)}</div>
          <div className="mono small">Sharpe: {num(hover.sharpe)} · Trades: {hover.n_trades}</div>
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 2. Strategy Comparison Horizontal Bar Chart                                */
/* -------------------------------------------------------------------------- */

type MetricKey = "sharpe" | "total_return_pct" | "win_rate" | "profit_factor" | "avg_return_pct";

const METRICS: [MetricKey, string][] = [
  ["sharpe", "Sharpe"],
  ["total_return_pct", "Return %"],
  ["win_rate", "Win %"],
  ["profit_factor", "PF"],
  ["avg_return_pct", "Avg Ret %"],
];

export function StrategyComparisonBars({
  stats,
  colors,
  onSelect,
}: {
  stats: StatRow[];
  colors: Record<string, string>;
  onSelect?: (portfolio: string) => void;
}) {
  const [metric, setMetric] = useState<MetricKey>("sharpe");

  const rows = useMemo(() => {
    return stats
      .filter((s) => s[metric] != null)
      .map((s) => ({
        name: s.portfolio,
        value: s[metric] ?? 0,
        color: colors[s.portfolio] ?? "var(--accent)",
      }))
      .sort((a, b) => b.value - a.value)
      .slice(0, 10);
  }, [stats, metric, colors]);

  const maxAbs = Math.max(0.01, ...rows.map((r) => Math.abs(r.value)));
  const hasNeg = rows.some((r) => r.value < 0);

  return (
    <div>
      <div className="toolbar" style={{ marginBottom: 10 }}>
        <div className="seg">
          {METRICS.map(([k, label]) => (
            <button key={k} className={metric === k ? "on" : ""} onClick={() => setMetric(k)}>
              {label}
            </button>
          ))}
        </div>
      </div>
      {rows.length === 0 ? (
        <div className="muted small">No data for this metric.</div>
      ) : (
        <div className="hbar-list">
          {rows.map((r) => {
            const pctWidth = Math.min(100, (Math.abs(r.value) / maxAbs) * (hasNeg ? 48 : 96));
            const left = hasNeg ? (r.value >= 0 ? 50 : 50 - pctWidth) : 0;
            const formatted =
              metric === "total_return_pct" || metric === "avg_return_pct"
                ? pct(r.value, metric === "avg_return_pct" ? 2 : 1)
                : metric === "win_rate"
                  ? `${r.value.toFixed(1)}%`
                  : r.value.toFixed(2);
            return (
              <div
                key={r.name}
                className="hbar-row"
                style={{ cursor: onSelect ? "pointer" : "default" }}
                onClick={() => onSelect?.(r.name)}
              >
                <span className="hbar-label" title={r.name}>
                  {r.name}
                </span>
                <div className="hbar-track">
                  {hasNeg && (
                    <div
                      style={{
                        position: "absolute",
                        left: "50%",
                        top: 0,
                        bottom: 0,
                        width: 1,
                        background: "var(--line-2)",
                      }}
                    />
                  )}
                  <div
                    className="hbar-fill"
                    style={{
                      left: `${left}%`,
                      width: `${pctWidth}%`,
                      background: r.value >= 0 ? r.color : "var(--down)",
                    }}
                  />
                </div>
                <span className={`hbar-val ${r.value >= 0 ? "up" : "down"}`}>{formatted}</span>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 3. Monthly Returns Heatmap                                                 */
/* -------------------------------------------------------------------------- */

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function MonthlyReturnsHeatmap({ points }: { points: Point[] }) {
  const { years, grid, ytd } = useMemo(() => {
    const byYm = new Map<string, { first: number; last: number }>();
    for (const p of points) {
      const d = new Date(p.time * 1000);
      const y = d.getUTCFullYear();
      const m = d.getUTCMonth();
      const key = `${y}-${m}`;
      const cur = byYm.get(key);
      if (!cur) byYm.set(key, { first: p.value, last: p.value });
      else cur.last = p.value;
    }
    const yearSet = new Set<number>();
    const map = new Map<string, number>();
    let prevLast: number | null = null;
    const sortedKeys = [...byYm.keys()].sort((a, b) => {
      const [ya, ma] = a.split("-").map(Number);
      const [yb, mb] = b.split("-").map(Number);
      return ya !== yb ? ya - yb : ma - mb;
    });
    for (const key of sortedKeys) {
      const [y] = key.split("-").map(Number);
      yearSet.add(y);
      const { first, last } = byYm.get(key)!;
      const base = prevLast ?? first;
      const ret = base > 0 ? ((last - base) / base) * 100 : 0;
      map.set(key, ret);
      prevLast = last;
    }
    const ys = [...yearSet].sort((a, b) => b - a);
    const ytdMap = new Map<number, number>();
    for (const y of ys) {
      let comp = 1;
      let any = false;
      for (let m = 0; m < 12; m++) {
        const r = map.get(`${y}-${m}`);
        if (r != null) {
          comp *= 1 + r / 100;
          any = true;
        }
      }
      if (any) ytdMap.set(y, (comp - 1) * 100);
    }
    return { years: ys, grid: map, ytd: ytdMap };
  }, [points]);

  if (!years.length) {
    return <div className="muted small">Not enough equity points for monthly heatmap.</div>;
  }

  const cellStyle = (v: number | undefined) => {
    if (v == null) return { background: "var(--panel-2)", color: "var(--muted)" };
    const intensity = Math.min(0.62, 0.1 + Math.abs(v) * 0.06);
    const bg = v >= 0 ? `rgba(16, 185, 129, ${intensity})` : `rgba(244, 63, 94, ${intensity})`;
    return { background: bg, color: "var(--text)" };
  };

  return (
    <div className="heatmap-wrap">
      <table className="heatmap-table">
        <thead>
          <tr>
            <th>Year</th>
            {MONTHS.map((m) => (
              <th key={m}>{m}</th>
            ))}
            <th>YTD</th>
          </tr>
        </thead>
        <tbody>
          {years.map((y) => {
            const yr = ytd.get(y);
            return (
              <tr key={y}>
                <td className="heatmap-year">{y}</td>
                {MONTHS.map((_, m) => {
                  const v = grid.get(`${y}-${m}`);
                  return (
                    <td key={m} style={cellStyle(v)}>
                      {v != null ? `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` : "—"}
                    </td>
                  );
                })}
                <td style={cellStyle(yr)}>
                  {yr != null ? `${yr >= 0 ? "+" : ""}${yr.toFixed(1)}%` : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 4. Cumulative Net vs. Gross P&L Chart (Trades tab)                         */
/* -------------------------------------------------------------------------- */

export function CumulativePnlChart({ trades }: { trades: Trade[] }) {
  const { ref, chart } = useChart({
    rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } },
  });

  useEffect(() => {
    if (!chart) return;
    const sorted = [...trades].sort((a, b) => a.exit_time - b.exit_time);
    const netPts: { time: Time; value: number }[] = [];
    const grossPts: { time: Time; value: number }[] = [];
    let cumNet = 0;
    let cumGross = 0;
    let lastT = 0;
    for (const t of sorted) {
      cumNet += t.net_pnl;
      cumGross += t.gross_pnl;
      // ensure strictly increasing timestamps for lightweight-charts
      const ts = t.exit_time > lastT ? t.exit_time : lastT + 1;
      lastT = ts;
      netPts.push({ time: ts as Time, value: cumNet });
      grossPts.push({ time: ts as Time, value: cumGross });
    }

    const gross = chart.addSeries(LineSeries, {
      color: "#94a3b8",
      lineWidth: 2,
      lineStyle: LineStyle.Dashed,
      priceFormat: { type: "custom", formatter: (v: number) => money(v) },
      priceLineVisible: false,
    });
    const net = chart.addSeries(AreaSeries, {
      lineColor: cumNet >= 0 ? "#10b981" : "#f43f5e",
      topColor: cumNet >= 0 ? "rgba(16,185,129,0.28)" : "rgba(244,63,94,0.28)",
      bottomColor: "transparent",
      lineWidth: 2,
      priceFormat: { type: "custom", formatter: (v: number) => money(v) },
      priceLineVisible: false,
    });
    gross.setData(grossPts);
    net.setData(netPts);
    chart.timeScale().fitContent();

    return () => {
      try {
        chart.removeSeries(gross);
        chart.removeSeries(net);
      } catch {
        // ignore
      }
    };
  }, [chart, trades]);

  if (!trades.length) return <div className="muted small">No trades to chart.</div>;
  return (
    <div className="chart-wrap">
      <div className="chart-label">Solid: Net P&amp;L · Dashed: Gross P&amp;L</div>
      <div ref={ref} className="chart" />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 5. Trade Return Distribution Histogram                                     */
/* -------------------------------------------------------------------------- */

export function ReturnDistributionChart({ trades }: { trades: Trade[] }) {
  const { bins, maxCount, mean, winPct } = useMemo(() => {
    if (!trades.length) return { bins: [], maxCount: 1, mean: 0, winPct: 0 };
    const vals = trades.map((t) => t.return_pct);
    const min = Math.min(...vals);
    const max = Math.max(...vals);
    const span = Math.max(0.2, max - min);
    const nBins = Math.min(20, Math.max(8, Math.round(Math.sqrt(vals.length))));
    const step = span / nBins;
    const arr = Array.from({ length: nBins }, (_, i) => ({
      lo: min + i * step,
      hi: min + (i + 1) * step,
      count: 0,
    }));
    for (const v of vals) {
      const idx = Math.min(nBins - 1, Math.max(0, Math.floor((v - min) / step)));
      arr[idx].count += 1;
    }
    const meanVal = vals.reduce((a, b) => a + b, 0) / vals.length;
    const wins = vals.filter((v) => v > 0).length;
    return {
      bins: arr,
      maxCount: Math.max(1, ...arr.map((b) => b.count)),
      mean: meanVal,
      winPct: (wins / vals.length) * 100,
    };
  }, [trades]);

  if (!bins.length) return <div className="muted small">No trades available.</div>;

  const W = 520;
  const H = 220;
  const pad = { l: 34, r: 14, t: 24, b: 30 };
  const plotW = W - pad.l - pad.r;
  const plotH = H - pad.t - pad.b;
  const barW = plotW / bins.length;

  return (
    <div>
      <div className="small muted mono" style={{ marginBottom: 6 }}>
        Mean: <span className={mean >= 0 ? "up" : "down"}>{pct(mean, 2)}</span> · Win rate:{" "}
        <span className="up">{winPct.toFixed(1)}%</span> · N={trades.length}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} className="svg-chart" style={{ height: 220 }}>
        {[0, 0.5, 1].map((frac) => {
          const y = pad.t + plotH * (1 - frac);
          return (
            <g key={frac}>
              <line x1={pad.l} x2={W - pad.r} y1={y} y2={y} stroke="var(--grid)" />
              <text x={pad.l - 6} y={y + 3} textAnchor="end" fill="var(--muted)" fontSize="10" className="mono">
                {Math.round(maxCount * frac)}
              </text>
            </g>
          );
        })}
        {bins.map((b, i) => {
          const h = (b.count / maxCount) * plotH;
          const x = pad.l + i * barW + 2;
          const y = pad.t + plotH - h;
          const mid = (b.lo + b.hi) / 2;
          return (
            <g key={i}>
              <rect
                x={x}
                y={y}
                width={Math.max(2, barW - 4)}
                height={h}
                rx={3}
                fill={mid >= 0 ? "var(--up)" : "var(--down)"}
                fillOpacity={0.82}
              >
                <title>
                  {b.lo.toFixed(2)}% to {b.hi.toFixed(2)}%: {b.count} trades
                </title>
              </rect>
              {(i === 0 || i === bins.length - 1 || i === Math.floor(bins.length / 2)) && (
                <text
                  x={x + barW / 2}
                  y={H - 8}
                  textAnchor="middle"
                  fill="var(--muted)"
                  fontSize="10"
                  className="mono"
                >
                  {mid >= 0 ? `+${mid.toFixed(1)}%` : `${mid.toFixed(1)}%`}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 6. Breakdown Horizontal P&L Bar Chart (Symbol / Exit Reason / Hour ET)     */
/* -------------------------------------------------------------------------- */

export function BreakdownBarChart({
  items,
  onSelect,
}: {
  items: { label: string; value: number; count?: number }[];
  onSelect?: (label: string) => void;
}) {
  if (!items.length) return <div className="muted small">No breakdown data.</div>;
  const maxAbs = Math.max(1, ...items.map((i) => Math.abs(i.value)));
  const hasNeg = items.some((i) => i.value < 0);

  return (
    <div className="hbar-list">
      {items.map((item) => {
        const pctWidth = Math.min(100, (Math.abs(item.value) / maxAbs) * (hasNeg ? 48 : 96));
        const left = hasNeg ? (item.value >= 0 ? 50 : 50 - pctWidth) : 0;
        return (
          <div
            key={item.label}
            className="hbar-row"
            style={{ cursor: onSelect ? "pointer" : "default" }}
            onClick={() => onSelect?.(item.label)}
          >
            <span className="hbar-label">
              {item.label}
              {item.count != null && <span className="muted small"> ({item.count})</span>}
            </span>
            <div className="hbar-track">
              {hasNeg && (
                <div
                  style={{
                    position: "absolute",
                    left: "50%",
                    top: 0,
                    bottom: 0,
                    width: 1,
                    background: "var(--line-2)",
                  }}
                />
              )}
              <div
                className="hbar-fill"
                style={{
                  left: `${left}%`,
                  width: `${pctWidth}%`,
                  background: item.value >= 0 ? "var(--up)" : "var(--down)",
                }}
              />
            </div>
            <span className={`hbar-val ${item.value >= 0 ? "up" : "down"}`}>
              {item.value >= 0 ? `+${money(item.value)}` : money(item.value)}
            </span>
          </div>
        );
      })}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* 7. Alpaca Paper Account Portfolio History Chart                            */
/* -------------------------------------------------------------------------- */

export function AlpacaAccountChart({
  points,
  baseValue,
}: {
  points: AlpacaHistoryPoint[];
  baseValue: number | null;
}) {
  const { ref, chart } = useChart({
    rightPriceScale: { scaleMargins: { top: 0.1, bottom: 0.1 } },
  });

  useEffect(() => {
    if (!chart || !points.length) return;
    const first = baseValue ?? points[0].equity;
    const last = points[points.length - 1].equity;
    const up = last >= first;
    const area = chart.addSeries(AreaSeries, {
      lineColor: up ? "#10b981" : "#f43f5e",
      topColor: up ? "rgba(16,185,129,0.32)" : "rgba(244,63,94,0.32)",
      bottomColor: "transparent",
      lineWidth: 2,
      priceFormat: { type: "custom", formatter: (v: number) => money(v) },
    });
    area.setData(points.map((p) => ({ time: p.time as Time, value: p.equity })));
    if (baseValue != null) {
      area.createPriceLine({
        price: baseValue,
        color: "#64748b",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "Base",
      });
    }
    chart.timeScale().fitContent();
    return () => {
      try {
        chart.removeSeries(area);
      } catch {
        // ignore
      }
    };
  }, [chart, points, baseValue]);

  if (!points.length) {
    return <div className="muted small">No Alpaca portfolio history points for this period.</div>;
  }
  return <div ref={ref} className="chart med" />;
}
