import { useEffect, useMemo } from "react";
import { LineSeries, LineStyle, type Time } from "lightweight-charts";
import type { AlpacaPortfolioHistory, BenchmarkSettings } from "./api";
import { savingsBenchmark } from "./benchmarkData";
import { LINES, palette, useChart, useThemeKey } from "./chart";
import { usd } from "./format";
import { Message } from "./ui";

const priceFormat = { type: "custom" as const, formatter: (value: number) => usd(value, 0) };

export function EquityChart({ history, settings }: {
  history?: AlpacaPortfolioHistory;
  settings?: BenchmarkSettings;
}) {
  const { ref, chart } = useChart({ rightPriceScale: { scaleMargins: { top: 0.12, bottom: 0.12 } } });
  const themeKey = useThemeKey();
  const points = history?.points;
  const baseValue = history?.baseValue;
  const data = useMemo(
    () => points?.flatMap(({ time, equity }) => (equity == null ? [] : [{ time, value: equity }])) ?? [],
    [points],
  );
  const purchaseDate = settings?.purchase_date;
  const rate = settings?.annual_rate_pct ?? 0;
  const market = history?.benchmarks;
  const overlays = useMemo(() => purchaseDate ? [
    { title: "QQQ", color: LINES.vwap, points: market?.QQQ ?? [] },
    { title: "VGT", color: LINES.ema50, points: market?.VGT ?? [] },
    { title: `Savings (${rate}% p.a.)`, color: LINES.sma20,
      points: savingsBenchmark(data.map((p) => p.time), purchaseDate, rate) },
  ] : [], [data, market, purchaseDate, rate]);
  const last = data.at(-1)?.value;
  const rising = last != null && last >= (baseValue ?? data[0].value);

  useEffect(() => {
    if (!chart || !data.length) return;
    const colors = palette();
    const line = chart.addSeries(LineSeries, {
      color: rising ? colors.up : colors.down,
      lineWidth: 2,
      priceLineVisible: false,
      priceFormat,
    });
    line.setData(data.map(({ time, value }) => ({ time: time as Time, value })));
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
    const comparisons = overlays.filter((overlay) => overlay.points.length).map(({ color, points }) => {
      const series = chart.addSeries(LineSeries, {
        color,
        lineWidth: 2,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
        priceFormat,
      });
      series.setData(points.map(({ time, value }) => ({ time: time as Time, value })));
      return series;
    });
    chart.timeScale().fitContent();
    return () => {
      try {
        chart.removeSeries(line);
        comparisons.forEach((series) => chart.removeSeries(series));
      } catch {
        // the chart was already removed
      }
    };
  }, [chart, data, baseValue, overlays, rising, themeKey]);

  return (
    <>
      <div className="relative h-[260px] w-full">
        <div ref={ref} className="h-full w-full" />
        {!data.length && (
          <div className="absolute inset-0 grid place-items-center bg-bg">
            <Message>No equity history for this period.</Message>
          </div>
        )}
      </div>
      {last != null && overlays.length > 0 && (
        overlays.some((overlay) => overlay.points.length > 0) ? (
          <ul aria-label="Latest chart values" className="mt-2 flex flex-wrap gap-x-5 gap-y-1 text-[13px]">
            {[
              { title: "Paper equity", color: rising ? "var(--up)" : "var(--down)", value: last },
              ...overlays.map(({ title, color, points }) => ({ title, color, value: points.at(-1)?.value })),
            ].map(({ title, color, value }) => (
              <li key={title} className="flex items-center gap-1.5">
                <span aria-hidden="true" className="w-3 border-t-2" style={{ borderColor: color }} />
                {title} <span className="num text-dim">{value == null ? "Unavailable" : usd(value)}</span>
              </li>
            ))}
          </ul>
        ) : <Message>Benchmarks have no values for this period.</Message>
      )}
    </>
  );
}
