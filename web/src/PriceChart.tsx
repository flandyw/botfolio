import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createSeriesMarkers,
  type AutoscaleInfoProvider,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { AlpacaStreamBar, Bar } from "./api";
import { LINES, alpha, palette, useChart, useThemeKey } from "./chart";
import { calcBollinger, calcEma, calcRsi, calcSma, calcVwap } from "./indicators";

export type ChartOverlays = {
  sma20: boolean;
  ema50: boolean;
  vwap: boolean;
  bb: boolean;
  rsi: boolean;
};

export const INDICATORS: [keyof ChartOverlays, string][] = [
  ["sma20", "SMA 20"],
  ["ema50", "EMA 50"],
  ["vwap", "VWAP"],
  ["bb", "Bollinger 20, 2"],
  ["rsi", "RSI 14"],
];

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
};

export function PriceChart({ bars, overlays, liveBar, className, annotations }: Props) {
  const { ref, chart } = useChart();
  const themeKey = useThemeKey();
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lastBarTimeRef = useRef<number>(0);

  useEffect(() => {
    if (!chart) return;
    const handles: ISeriesApi<"Candlestick" | "Histogram" | "Line">[] = [];
    const { up, down, dim } = palette();
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

    if (overlays.sma20) {
      const s = chart.addSeries(LineSeries, {
        color: LINES.sma20,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      s.setData(calcSma(bars, 20).map((p) => ({ time: p.time as Time, value: p.value })));
      handles.push(s);
    }

    if (overlays.ema50) {
      const s = chart.addSeries(LineSeries, {
        color: LINES.ema50,
        lineWidth: 1,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      s.setData(calcEma(bars, 50).map((p) => ({ time: p.time as Time, value: p.value })));
      handles.push(s);
    }

    if (overlays.vwap) {
      const s = chart.addSeries(LineSeries, {
        color: LINES.vwap,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      s.setData(calcVwap(bars).map((p) => ({ time: p.time as Time, value: p.value })));
      handles.push(s);
    }

    if (overlays.bb) {
      const bb = calcBollinger(bars, 20, 2);
      const u = chart.addSeries(LineSeries, {
        color: LINES.bb,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      const l = chart.addSeries(LineSeries, {
        color: LINES.bb,
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      u.setData(bb.upper.map((p) => ({ time: p.time as Time, value: p.value })));
      l.setData(bb.lower.map((p) => ({ time: p.time as Time, value: p.value })));
      handles.push(u, l);
    }

    if (overlays.rsi) {
      const rsi = chart.addSeries(
        LineSeries,
        {
          color: LINES.rsi,
          lineWidth: 1,
          priceLineVisible: false,
          priceFormat: { type: "custom", formatter: (v: number) => v.toFixed(0) },
        },
        1,
      );
      rsi.setData(calcRsi(bars, 14).map((p) => ({ time: p.time as Time, value: p.value })));
      rsi.createPriceLine({
        price: 70,
        color: alpha(down, 0.5),
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "70",
      });
      rsi.createPriceLine({
        price: 30,
        color: alpha(up, 0.5),
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "30",
      });
      handles.push(rsi);
      const panes = chart.panes();
      if (panes[1]) panes[1].setHeight(115);
    }

    chart.timeScale().fitContent();

    return () => {
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
  }, [chart, bars, overlays, themeKey, annotations]);

  // Apply incoming live Alpaca WebSocket bar directly onto the chart
  useEffect(() => {
    if (!liveBar || !candleRef.current || !volumeRef.current) return;
    if (liveBar.time < lastBarTimeRef.current) return;
    lastBarTimeRef.current = liveBar.time;
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

  return (
    <div className="relative">
      {overlays.rsi && (
        <div className="pointer-events-none absolute bottom-[138px] left-0 z-[2] bg-bg pr-1.5 text-[11px] text-dim">
          RSI 14
        </div>
      )}
      <div ref={ref} className={className ?? "h-[420px] w-full"} />
    </div>
  );
}
