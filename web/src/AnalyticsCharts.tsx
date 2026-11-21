import { useEffect } from "react";
import { AreaSeries, LineStyle, type Time } from "lightweight-charts";
import type { AlpacaHistoryPoint } from "./api";
import { useChart } from "./chart";
import { money } from "./format";

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
    return <div className="text-xs text-muted-foreground">No Alpaca portfolio history points for this period.</div>;
  }
  return <div ref={ref} className="h-[360px] w-full" />;
}
