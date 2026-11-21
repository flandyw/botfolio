import { useEffect, useRef } from "react";
import {
  CandlestickSeries,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type ISeriesApi,
  type SeriesMarker,
  type Time,
} from "lightweight-charts";
import type { AlpacaStreamBar, Bar, Trade } from "./api";
import { floorIndex, useChart } from "./chart";
import { calcBollinger, calcEma, calcRsi, calcSma, calcVwap } from "./indicators";

export type ChartOverlays = {
  sma20: boolean;
  ema50: boolean;
  vwap: boolean;
  bb: boolean;
  rsi: boolean;
};

type Props = {
  bars: Bar[];
  trades: Trade[];
  overlays: ChartOverlays;
  liveBar?: AlpacaStreamBar | null;
};

export function PriceChart({ bars, trades, overlays, liveBar }: Props) {
  const { ref, chart } = useChart();
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lastBarTimeRef = useRef<number>(0);

  useEffect(() => {
    if (!chart) return;
    const handles: ISeriesApi<"Candlestick" | "Histogram" | "Line">[] = [];

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: "#10b981",
      downColor: "#f43f5e",
      borderVisible: false,
      wickUpColor: "#10b981",
      wickDownColor: "#f43f5e",
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
    });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(
      bars.map((b) => ({
        time: b.time as Time,
        value: b.volume,
        color: b.close >= b.open ? "rgba(16,185,129,0.28)" : "rgba(244,63,94,0.28)",
      })),
    );
    handles.push(vol);
    volumeRef.current = vol;

    if (overlays.sma20) {
      const s = chart.addSeries(LineSeries, {
        color: "#f59e0b",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      s.setData(calcSma(bars, 20).map((p) => ({ time: p.time as Time, value: p.value })));
      handles.push(s);
    }

    if (overlays.ema50) {
      const s = chart.addSeries(LineSeries, {
        color: "#a855f7",
        lineWidth: 2,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      s.setData(calcEma(bars, 50).map((p) => ({ time: p.time as Time, value: p.value })));
      handles.push(s);
    }

    if (overlays.vwap) {
      const s = chart.addSeries(LineSeries, {
        color: "#06b6d4",
        lineWidth: 2,
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
        color: "rgba(59,130,246,0.65)",
        lineWidth: 1,
        lineStyle: LineStyle.Dotted,
        priceLineVisible: false,
        lastValueVisible: false,
      });
      const l = chart.addSeries(LineSeries, {
        color: "rgba(59,130,246,0.65)",
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
          color: "#38bdf8",
          lineWidth: 2,
          priceLineVisible: false,
          priceFormat: { type: "custom", formatter: (v: number) => v.toFixed(0) },
        },
        1,
      );
      rsi.setData(calcRsi(bars, 14).map((p) => ({ time: p.time as Time, value: p.value })));
      rsi.createPriceLine({
        price: 70,
        color: "rgba(244,63,94,0.45)",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "70",
      });
      rsi.createPriceLine({
        price: 30,
        color: "rgba(16,185,129,0.45)",
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: "30",
      });
      handles.push(rsi);
      const panes = chart.panes();
      if (panes[1]) panes[1].setHeight(115);
    }

    const times = bars.map((b) => b.time);
    const snap = (t: number) => {
      const i = floorIndex(times, t);
      return i >= 0 ? times[i] : null;
    };
    const markers: SeriesMarker<Time>[] = [];
    for (const tr of trades) {
      const e = snap(tr.entry_time);
      const x = snap(tr.exit_time);
      const long = tr.side > 0;
      if (e !== null) {
        markers.push({
          time: e as Time,
          position: long ? "belowBar" : "aboveBar",
          color: long ? "#10b981" : "#f43f5e",
          shape: long ? "arrowUp" : "arrowDown",
          text: `${long ? "L" : "S"} ${tr.entry_price.toFixed(2)}`,
        });
      }
      if (x !== null) {
        markers.push({
          time: x as Time,
          position: long ? "aboveBar" : "belowBar",
          color: tr.net_pnl >= 0 ? "#10b981" : "#f43f5e",
          shape: "circle",
          text: `${tr.exit_reason} ${tr.net_pnl >= 0 ? "+" : ""}$${tr.net_pnl.toFixed(0)}`,
        });
      }
    }
    markers.sort((a, b) => Number(a.time) - Number(b.time));
    const plugin = createSeriesMarkers(candles, markers);
    chart.timeScale().fitContent();

    return () => {
      plugin.detach();
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
  }, [chart, bars, trades, overlays]);

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
      color: liveBar.close >= liveBar.open ? "rgba(16,185,129,0.35)" : "rgba(244,63,94,0.35)",
    });
  }, [liveBar]);

  return (
    <div className="chart-wrap">
      {overlays.rsi && <div className="chart-label bottom">RSI (14)</div>}
      <div ref={ref} className="chart tall" />
    </div>
  );
}
