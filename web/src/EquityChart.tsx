import { useEffect, useMemo } from "react";
import { LineSeries, LineStyle, type Time } from "lightweight-charts";
import type { AlpacaHistoryPoint } from "./api";
import { palette, useChart, useThemeKey } from "./chart";
import { usd } from "./format";
import { Message } from "./ui";

export function EquityChart({ points, baseValue }: { points: AlpacaHistoryPoint[]; baseValue: number | null }) {
  const { ref, chart } = useChart({ rightPriceScale: { scaleMargins: { top: 0.12, bottom: 0.12 } } });
  const themeKey = useThemeKey();
  const data = useMemo(
    () => points.flatMap(({ time, equity }) => (equity == null ? [] : [{ time: time as Time, value: equity }])),
    [points],
  );

  useEffect(() => {
    if (!chart || !data.length) return;
    const colors = palette();
    const first = baseValue ?? data[0].value;
    const line = chart.addSeries(LineSeries, {
      color: data[data.length - 1].value >= first ? colors.up : colors.down,
      lineWidth: 2,
      priceLineVisible: false,
      priceFormat: { type: "custom", formatter: (v: number) => usd(v, 0) },
    });
    line.setData(data);
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
  }, [chart, data, baseValue, themeKey]);

  return (
    <div className="relative h-[260px] w-full">
      <div ref={ref} className="h-full w-full" />
      {!data.length && (
        <div className="absolute inset-0 grid place-items-center bg-bg">
          <Message>No equity history for this period.</Message>
        </div>
      )}
    </div>
  );
}
