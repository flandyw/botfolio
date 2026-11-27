import { useEffect, useMemo, useRef, useState } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createSeriesMarkers,
  type AutoscaleInfoProvider,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { AlpacaStreamBar, Bar, Point } from "./api";
import { cx } from "./classes";
import { compact, num, pct, tone } from "./format";
import { LINES, alpha, palette, useChart, useThemeKey } from "./chart";
import { calcAtr, calcBollinger, calcEma, calcMacd, calcRsi, calcSma, calcVwap } from "./indicators";

export const INDICATORS = [
  ["ema9", "EMA 9"],
  ["sma20", "SMA 20"],
  ["ema50", "EMA 50"],
  ["sma200", "SMA 200"],
  ["vwap", "VWAP"],
  ["bb", "Bollinger 20, 2"],
  ["rsi", "RSI 14"],
  ["macd", "MACD 12, 26, 9"],
  ["atr", "ATR 14"],
] as const;
export type IndicatorKey = (typeof INDICATORS)[number][0];
export type ChartOverlays = Record<IndicatorKey, boolean>;
export const NO_OVERLAYS = Object.fromEntries(INDICATORS.map(([key]) => [key, false])) as ChartOverlays;

type LegendLine = { label: string; color: string; digits: number; values: Map<number, number> };

export type ChartAnnotations = {
  levels: { price: number; label: string; kind: "stop" | "target" | "signal" | "limit" }[];
  markers: {
    time: number;
    price: number;
    label: string;
    kind: "entry" | "exit" | "signal" | "order";
    shape: "arrowUp" | "arrowDown" | "circle";
    /** Side of the candle the marker sits on, clear of the wicks. */
    position: "aboveBar" | "belowBar";
  }[];
};

type Props = {
  bars: Bar[];
  overlays: ChartOverlays;
  liveBar?: AlpacaStreamBar | null;
  className?: string;
  annotations?: ChartAnnotations;
  /** The prior session's close, drawn as a reference line. */
  prevClose?: number | null;
};

export function PriceChart({ bars, overlays, liveBar, className, annotations, prevClose }: Props) {
  const { ref, chart } = useChart();
  const themeKey = useThemeKey();
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lastBarTimeRef = useRef<number>(0);
  const [cursor, setCursor] = useState<number | null>(null);
  const [lines, setLines] = useState<LegendLine[]>([]);
  const [live, setLive] = useState<Bar | null>(null);

  useEffect(() => {
    if (!chart) return;
    const handles: ISeriesApi<"Candlestick" | "Histogram" | "Line">[] = [];
    const legend: LegendLine[] = [];
    const { up, down, dim, faint } = palette();
    const plain = { priceLineVisible: false, lastValueVisible: false, crosshairMarkerVisible: false } as const;
    /** A line on pane `pane`, listed in the legend when it has a label. */
    const line = (points: Point[], color: string, label?: string, pane = 0, style = LineStyle.Solid, digits = 2) => {
      const s = chart.addSeries(LineSeries, { ...plain, color, lineWidth: 1, lineStyle: style }, pane);
      s.setData(points.map((p) => ({ time: p.time as Time, value: p.value })));
      handles.push(s);
      if (label) legend.push({ label, color, digits, values: new Map(points.map((p) => [p.time, p.value])) });
      return s;
    };
    const levels = annotations?.levels ?? [];
    const prices = [...levels, ...(annotations?.markers ?? [])].map(({ price }) => price);
    const includeAnnotations: AutoscaleInfoProvider | undefined = prices.length === 0 ? undefined : (original) => {
      const range = original();
      return {
        ...range,
        priceRange: {
          minValue: Math.min(range?.priceRange?.minValue ?? Infinity, ...prices),
          maxValue: Math.max(range?.priceRange?.maxValue ?? -Infinity, ...prices),
        },
      };
    };

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: up,
      downColor: down,
      borderVisible: false,
      wickUpColor: up,
      wickDownColor: down,
      ...(annotations && { priceLineVisible: false, lastValueVisible: false }),
      ...(includeAnnotations && { autoscaleInfoProvider: includeAnnotations }),
    });
    candles.priceScale().applyOptions({ scaleMargins: { top: 0.08, bottom: 0.22 } });
    candles.setData(
      bars.map((b) => ({
        time: b.time as Time,
        open: b.open,
        high: b.high,
        low: b.low,
        close: b.close,
      })),
    );
    handles.push(candles);
    candleRef.current = candles;
    lastBarTimeRef.current = bars.length ? bars[bars.length - 1].time : 0;

    const vol = chart.addSeries(HistogramSeries, {
      priceFormat: { type: "volume" },
      priceScaleId: "vol",
      lastValueVisible: false,
      priceLineVisible: false,
    });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(
      bars.map((b) => ({
        time: b.time as Time,
        value: b.volume,
        color: alpha(b.close >= b.open ? up : down, 0.3),
      })),
    );
    handles.push(vol);
    volumeRef.current = vol;

    let levelSeries: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> = candles;
    if (annotations?.markers.length) {
      const markerColors = { entry: LINES.vwap, exit: dim, signal: LINES.ema50, order: dim };
      const sorted = [...annotations.markers].sort((a, b) => a.time - b.time);
      let target: ISeriesApi<"Candlestick"> | ISeriesApi<"Line"> = candles;
      if (!bars.length) {
        // No candles to sit beside: pin the markers to their fill prices on an invisible series.
        const marks = chart.addSeries(LineSeries, {
          lineVisible: false,
          lastValueVisible: false,
          priceLineVisible: false,
          crosshairMarkerVisible: false,
          ...(includeAnnotations && { autoscaleInfoProvider: includeAnnotations }),
        });
        marks.setData([...new Map(sorted.map((m) => [m.time, m.price]))].map(([time, value]) => ({ time: time as Time, value })));
        handles.push(marks);
        target = levelSeries = marks;
      }
      createSeriesMarkers(
        target,
        sorted.map((mark): SeriesMarker<Time> => ({
          time: mark.time as Time,
          text: mark.label,
          shape: mark.shape,
          color: markerColors[mark.kind],
          ...(bars.length
            ? { position: mark.position }
            : { position: mark.position === "belowBar" ? "atPriceBottom" : "atPriceTop", price: mark.price }),
        })),
        { zOrder: "top" },
      );
    }
    const levelColors = { stop: down, target: up, signal: LINES.ema50, limit: dim };
    for (const level of levels) {
      levelSeries.createPriceLine({
        price: level.price,
        title: level.label,
        color: levelColors[level.kind],
        lineStyle: level.kind === "signal" ? LineStyle.Dotted : LineStyle.Dashed,
        axisLabelVisible: true,
      });
    }

    if (prevClose != null && !annotations) {
      candles.createPriceLine({
        price: prevClose,
        title: "prev close",
        color: faint,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        axisLabelVisible: true,
      });
    }

    if (overlays.ema9) line(calcEma(bars, 9), LINES.ema9, "EMA 9");
    if (overlays.sma20) line(calcSma(bars, 20), LINES.sma20, "SMA 20");
    if (overlays.ema50) line(calcEma(bars, 50), LINES.ema50, "EMA 50");
    if (overlays.sma200) line(calcSma(bars, 200), LINES.sma200, "SMA 200");
    if (overlays.vwap) line(calcVwap(bars), LINES.vwap, "VWAP", 0, LineStyle.Dashed);
    if (overlays.bb) {
      const bb = calcBollinger(bars, 20, 2);
      line(bb.upper, LINES.bb, "BB upper", 0, LineStyle.Dotted);
      line(bb.lower, LINES.bb, "BB lower", 0, LineStyle.Dotted);
    }

    // Each oscillator gets its own pane below the price.
    let pane = 0;
    if (overlays.rsi) {
      const rsi = line(calcRsi(bars, 14), LINES.rsi, "RSI 14", ++pane, LineStyle.Solid, 1);
      rsi.applyOptions({ priceFormat: { type: "custom", formatter: (v: number) => v.toFixed(0) } });
      for (const [price, color] of [[70, down], [30, up]] as const) {
        rsi.createPriceLine({ price, color: alpha(color, 0.5), lineWidth: 1, lineStyle: LineStyle.Dashed, axisLabelVisible: true });
      }
    }
    if (overlays.macd) {
      const macd = calcMacd(bars);
      const at = ++pane;
      const histogram = chart.addSeries(HistogramSeries, { ...plain }, at);
      histogram.setData(
        macd.histogram.map((p) => ({ time: p.time as Time, value: p.value, color: alpha(p.value >= 0 ? up : down, 0.45) })),
      );
      handles.push(histogram);
      line(macd.macd, LINES.macd, "MACD", at, LineStyle.Solid, 3);
      line(macd.signal, LINES.sma20, "Signal", at, LineStyle.Solid, 3);
    }
    if (overlays.atr) line(calcAtr(bars, 14), LINES.atr, "ATR 14", ++pane);
    chart.panes().forEach((p, i) => p.setStretchFactor(i ? 1 : 4));
    setLines(legend);
    setLive(null);

    const onMove = ({ time }: MouseEventParams<Time>) => setCursor(typeof time === "number" ? time : null);
    chart.subscribeCrosshairMove(onMove);

    chart.timeScale().fitContent();

    return () => {
      chart.unsubscribeCrosshairMove(onMove);
      candleRef.current = null;
      volumeRef.current = null;
      for (const s of handles) {
        try {
          chart.removeSeries(s);
        } catch {
          // ignore on unmount
        }
      }
    };
  }, [chart, bars, overlays, themeKey, annotations, prevClose]);

  // Apply incoming live Alpaca WebSocket bar directly onto the chart
  useEffect(() => {
    if (!liveBar || !candleRef.current || !volumeRef.current) return;
    if (liveBar.time < lastBarTimeRef.current) return;
    lastBarTimeRef.current = liveBar.time;
    setLive(liveBar);
    candleRef.current.update({
      time: liveBar.time as Time,
      open: liveBar.open,
      high: liveBar.high,
      low: liveBar.low,
      close: liveBar.close,
    });
    volumeRef.current.update({
      time: liveBar.time as Time,
      value: liveBar.volume,
      color: alpha(liveBar.close >= liveBar.open ? palette().up : palette().down, 0.3),
    });
  }, [liveBar]);

  const byTime = useMemo(() => new Map(bars.map((bar, i) => [bar.time, i])), [bars]);
  const at = cursor != null ? byTime.get(cursor) : undefined;
  const latest = live && live.time >= (bars.at(-1)?.time ?? 0) ? live : bars.at(-1);
  const bar = at != null ? bars[at] : cursor == null ? latest : undefined;
  const before = at != null ? bars[at - 1]?.close : bar === live ? bars.at(live.time > (bars.at(-1)?.time ?? 0) ? -1 : -2)?.close : bars.at(-2)?.close;
  const change = bar && before ? (bar.close / before - 1) * 100 : null;

  return (
    <div className="relative">
      {bar && (
        <div className="num pointer-events-none absolute top-1 left-1 z-[3] flex max-w-[calc(100%-5rem)] flex-wrap gap-x-3 gap-y-0.5 bg-bg/85 px-1 text-[11px] leading-4 text-dim">
          <span>
            O <b className="font-medium text-ink">{num(bar.open)}</b> H <b className="font-medium text-ink">{num(bar.high)}</b>{" "}
            L <b className="font-medium text-ink">{num(bar.low)}</b> C{" "}
            <b className={cx("font-medium", bar.close >= bar.open ? "text-up" : "text-down")}>{num(bar.close)}</b>
          </span>
          {change != null && <span className={tone(change)}>{pct(change)}</span>}
          <span>
            Vol <b className="font-medium text-ink">{compact(bar.volume)}</b>
          </span>
          {lines.map(({ label, color, digits, values }) => {
            const value = values.get(bar.time) ?? (cursor == null ? [...values.values()].at(-1) : undefined);
            return (
              value != null && (
                <span key={label} style={{ color }}>
                  {label} {num(value, digits)}
                </span>
              )
            );
          })}
        </div>
      )}
      <div ref={ref} className={className ?? "h-[420px] w-full"} />
    </div>
  );
}
