import { useEffect, useRef, useState } from "react";
import {
  BaselineSeries,
  HistogramSeries,
  LineStyle,
  type AutoscaleInfo,
  type ISeriesApi,
  type MouseEventParams,
  type Time,
} from "lightweight-charts";
import { alpha, palette, useChart, useThemeKey } from "./chart";
import { pct, signedUsd, tone } from "./format";
import { cx } from "./classes";
import type { PerformancePoint } from "./performanceData";
import { Message } from "./ui";

export type PerformanceMetric = "pnl" | "return" | "change" | "drawdown";

const dateFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
  year: "numeric",
});
const timeFormat = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
  hour: "numeric",
  minute: "2-digit",
});

export function PerformanceChart({
  title,
  metric,
  points,
  intraday,
  empty,
}: {
  title: string;
  metric: PerformanceMetric;
  points: PerformancePoint[];
  intraday: boolean;
  empty: string;
}) {
  const { ref, chart } = useChart({
    handleScroll: { mouseWheel: false, vertTouchDrag: false },
    handleScale: { mouseWheel: false },
    rightPriceScale: { scaleMargins: { top: 0.16, bottom: 0.15 } },
  });
  const seriesRef = useRef<ISeriesApi<"Baseline"> | ISeriesApi<"Histogram"> | null>(null);
  const themeKey = useThemeKey();
  const [cursorTime, setCursorTime] = useState<Time | undefined>();
  const latest = points.findLast((point) => point.value != null);
  const current = points.find((point) => point.time === cursorTime) ?? latest;
  const format = metric === "return" || metric === "drawdown" ? pct : signedUsd;

  useEffect(() => {
    if (!chart) return;
    const colors = palette();
    const options = {
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: "custom" as const, formatter: format },
    };
    const series =
      metric === "change"
        ? chart.addSeries(HistogramSeries, options)
        : chart.addSeries(BaselineSeries, {
            ...options,
            baseValue: { type: "price", price: 0 },
            lineWidth: 2,
            topLineColor: metric === "drawdown" ? colors.down : colors.up,
            bottomLineColor: colors.down,
            topFillColor1: alpha(colors.up, 0.23),
            topFillColor2: alpha(colors.up, 0.03),
            bottomFillColor1: alpha(colors.down, 0.03),
            bottomFillColor2: alpha(colors.down, 0.23),
            autoscaleInfoProvider: (original: () => AutoscaleInfo | null) => {
              const info = original();
              if (info?.priceRange) {
                info.priceRange.minValue = Math.min(0, info.priceRange.minValue);
                info.priceRange.maxValue = Math.max(0, info.priceRange.maxValue);
              }
              return info;
            },
          });
    seriesRef.current = series;
    series.createPriceLine({
      price: 0,
      color: colors.faint,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: false,
    });
    const onMove = ({ time }: MouseEventParams<Time>) => setCursorTime(time);
    chart.subscribeCrosshairMove(onMove);
    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      try {
        chart.removeSeries(series);
      } catch {
        // useChart may have already removed the chart.
      }
      seriesRef.current = null;
    };
  }, [chart, format, metric, themeKey]);

  useEffect(() => {
    const series = seriesRef.current;
    if (!chart || !series) return;
    const colors = palette();
    const visibleRange = series.data().length ? chart.timeScale().getVisibleLogicalRange() : null;
    series.setData(
      points.map(({ time, value }) =>
        value == null
          ? { time: time as Time }
          : {
              time: time as Time,
              value,
              color: alpha(value === 0 ? colors.faint : value > 0 ? colors.up : colors.down, 0.75),
            },
      ),
    );
    if (visibleRange) chart.timeScale().setVisibleLogicalRange(visibleRange);
    else chart.timeScale().fitContent();
  }, [chart, points, metric, themeKey]);

  return (
    <div>
      <div className="flex min-h-7 flex-wrap items-baseline gap-x-3 gap-y-0.5">
        {current && (
          <>
            <span className={cx("num text-[19px] font-medium", tone(current.value))}>{format(current.value)}</span>
            <span className="text-[12px] text-dim">
              {(intraday ? timeFormat : dateFormat).format(current.time * 1000)}{intraday ? " ET" : ""}
            </span>
          </>
        )}
      </div>
      <div className="relative h-48 sm:h-56">
        <div ref={ref} className="h-full w-full" role="img" aria-label={title} aria-hidden={!latest} />
        {!latest && (
          <div className="absolute inset-0 grid place-items-center bg-bg px-3 text-center">
            <Message>{empty}</Message>
          </div>
        )}
      </div>
    </div>
  );
}
