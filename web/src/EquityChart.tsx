import { useEffect, useState } from "react";
import {
  AreaSeries,
  LineSeries,
  LineStyle,
  type ISeriesApi,
  type MouseEventParams,
  type Time,
} from "lightweight-charts";
import type { Point } from "./api";
import { useChart } from "./chart";
import { fmtTime, money, pct } from "./format";

type Props = {
  series: Record<string, Point[]>;
  names: string[];
  colors: Record<string, string>;
  dashed?: Set<string>;
  start: number;
  mode?: "pct" | "usd";
};

function drawdown(pts: Point[]): Point[] {
  let peak = 0;
  return pts.map((p) => {
    if (p.value > peak) peak = p.value;
    return { time: p.time, value: peak ? ((p.value - peak) / peak) * 100 : 0 };
  });
}

type HudRow = { name: string; value: number; color: string };

export function EquityChart({ series, names, colors, dashed, start, mode = "pct" }: Props) {
  const { ref, chart } = useChart({
    rightPriceScale: { scaleMargins: { top: 0.08, bottom: 0.08 } },
  });
  const [hud, setHud] = useState<{ time: number | null; rows: HudRow[] }>({
    time: null,
    rows: [],
  });

  useEffect(() => {
    if (!chart) return;
    const handles: ISeriesApi<"Line" | "Area">[] = [];
    const meta = new Map<ISeriesApi<"Line" | "Area">, { name: string; color: string }>();

    for (const name of names) {
      const pts = series[name];
      if (!pts?.length) continue;
      const color = colors[name] ?? "#3b82f6";
      const isDashed = dashed?.has(name);
      const eq = chart.addSeries(LineSeries, {
        color,
        lineWidth: isDashed ? 2 : 2,
        lineStyle: isDashed ? LineStyle.Dashed : LineStyle.Solid,
        priceFormat:
          mode === "pct"
            ? { type: "custom", formatter: (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(1)}%` }
            : { type: "custom", formatter: (v: number) => money(v) },
        lastValueVisible: true,
        priceLineVisible: false,
      });
      const base = pts[0].value || start;
      eq.setData(
        pts.map((p) => ({
          time: p.time as Time,
          value: mode === "pct" ? (p.value / base - 1) * 100 : p.value,
        })),
      );
      handles.push(eq);
      meta.set(eq, { name, color });

      const dd = chart.addSeries(
        AreaSeries,
        {
          lineColor: color,
          topColor: "transparent",
          bottomColor: `${color}28`,
          lineWidth: 1,
          lineStyle: isDashed ? LineStyle.Dashed : LineStyle.Solid,
          priceFormat: { type: "custom", formatter: (v: number) => `${v.toFixed(1)}%` },
          lastValueVisible: false,
          priceLineVisible: false,
        },
        1,
      );
      dd.setData(drawdown(pts).map((p) => ({ time: p.time as Time, value: p.value })));
      handles.push(dd);
    }

    const panes = chart.panes();
    if (panes[1]) panes[1].setHeight(120);
    chart.timeScale().fitContent();

    const onCrosshair = (param: MouseEventParams<Time>) => {
      if (!param.time || typeof param.time !== "number") {
        setHud({ time: null, rows: [] });
        return;
      }
      const rows: HudRow[] = [];
      for (const [s, info] of meta.entries()) {
        const pt = param.seriesData.get(s) as { value?: number } | undefined;
        if (pt?.value != null) rows.push({ ...info, value: pt.value });
      }
      rows.sort((a, b) => b.value - a.value);
      setHud({ time: param.time, rows });
    };
    chart.subscribeCrosshairMove(onCrosshair);

    return () => {
      chart.unsubscribeCrosshairMove(onCrosshair);
      for (const s of handles) {
        try {
          chart.removeSeries(s);
        } catch {
          // chart may already be torn down
        }
      }
    };
  }, [chart, series, names, colors, dashed, start, mode]);

  return (
    <div className="chart-wrap">
      {hud.rows.length > 0 && (
        <div className="chart-hud">
          <span className="mono muted">{hud.time ? fmtTime(hud.time) : ""}</span>
          {hud.rows.slice(0, 6).map((r) => (
            <span key={r.name} className="mono" style={{ display: "inline-flex", gap: 5, alignItems: "center" }}>
              <span className="chip-dot" style={{ background: r.color }} />
              <span className="muted">{r.name}:</span>
              <span className={r.value >= (mode === "pct" ? 0 : start) ? "up" : "down"}>
                {mode === "pct" ? pct(r.value, 2) : money(r.value)}
              </span>
            </span>
          ))}
        </div>
      )}
      <div className="chart-wrap">
        <div className="chart-label">{mode === "pct" ? "Cumulative Return %" : "Portfolio Equity ($)"}</div>
        <div className="chart-label bottom">Drawdown %</div>
        <div ref={ref} className="chart tall" />
      </div>
    </div>
  );
}
