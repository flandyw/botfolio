import { useEffect } from "react";
import { LineSeries, LineStyle, type Time } from "lightweight-charts";
import type { AlpacaHistoryPoint } from "./api";
import { palette, useChart, useThemeKey } from "./chart";
import { usd } from "./format";
import { Message } from "./ui";

export function EquityChart({ points, baseValue }: { points: AlpacaHistoryPoint[]; baseValue: number | null }) {
  const { ref, chart } = useChart({ rightPriceScale: { scaleMargins: { top: 0.12, bottom: 0.12 } } });
  const themeKey = useThemeKey();

  useEffect(() => {
    if (!chart || !points.length) return;
    const colors = palette();
    const first = baseValue ?? points[0].equity;
    const line = chart.addSeries(LineSeries, {
      color: points[points.length - 1].equity >= first ? colors.up : colors.down,
      lineWidth: 2,
      priceLineVisible: false,
      priceFormat: { type: "custom", formatter: (v: number) => usd(v, 0) },
    });
    line.setData(points.map((p) => ({ time: p.time as Time, value: p.equity })));
    if (baseValue != null) {
      line.createPriceLine({
        price: baseValue,
        color: colors.faint,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: false,
        title: "start",
      });
    }
    chart.timeScale().fitContent();
    return () => {
      try {
        chart.removeSeries(line);
      } catch {
        // the chart was already removed
      }
    };
  }, [chart, points, baseValue, themeKey]);

  return (
    <div className="relative h-[260px] w-full">
      <div ref={ref} className="h-full w-full" />
      {!points.length && (
        <div className="absolute inset-0 grid place-items-center bg-bg">
          <Message>No equity history for this period.</Message>
        </div>
      )}
    </div>
  );
}
