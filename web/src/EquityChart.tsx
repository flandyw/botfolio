import { useEffect } from "react";
import { LineSeries, LineStyle, type ISeriesApi, type UTCTimestamp } from "lightweight-charts";
import type { Point } from "./api";
import { useChart } from "./chart";

type Props = {
  equity: Record<string, Point[]>;
  names: string[];
  colors: Record<string, string>;
  dashed: Set<string>;
  start: number;
};

/** Return (%) of each visible portfolio on top, drawdown (%) below, sharing one time axis. */
export function EquityChart({ equity, names, colors, dashed, start }: Props) {
  const { ref, chart } = useChart({
    localization: { priceFormatter: (v: number) => `${v.toFixed(1)}%` },
  });
  useEffect(() => {
    if (!chart) return;
    const series: ISeriesApi<"Line">[] = [];
    for (const name of names) {
      const pts = equity[name];
      if (!pts?.length) continue;
      let peak = -Infinity;
      const ret = pts.map((p) => ({
        time: p.time as UTCTimestamp,
        value: (p.value / start - 1) * 100,
      }));
      const dd = pts.map((p) => {
        peak = Math.max(peak, p.value);
        return { time: p.time as UTCTimestamp, value: (p.value / peak - 1) * 100 };
      });
      const opts = {
        color: colors[name],
        lineWidth: 2 as const,
        lineStyle: dashed.has(name) ? LineStyle.Dashed : LineStyle.Solid,
        priceLineVisible: false,
        lastValueVisible: false,
      };
      const a = chart.addSeries(LineSeries, { ...opts, title: "" }, 0);
      a.setData(ret);
      const b = chart.addSeries(LineSeries, { ...opts, lineWidth: 1 }, 1);
      b.setData(dd);
      series.push(a, b);
    }
    const panes = chart.panes();
    if (panes[1]) panes[1].setHeight(110);
    chart.timeScale().fitContent();
    return () => series.forEach((s) => chart.removeSeries(s));
  }, [chart, equity, names, colors, dashed, start]);
  return (
    <div className="chart-wrap">
      <div className="chart-label">Return · dashed = buy &amp; hold</div>
      <div className="chart-label bottom">Drawdown</div>
      <div ref={ref} className="chart tall" />
    </div>
  );
}
