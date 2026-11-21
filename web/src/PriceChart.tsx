import { useEffect } from "react";
import {
  CandlestickSeries,
  HistogramSeries,
  createSeriesMarkers,
  type SeriesMarker,
  type Time,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Bar, Trade } from "./api";
import { floorIndex, useChart } from "./chart";
import { compactUsd } from "./format";

const EXIT: Record<string, string> = {
  stop: "Stop",
  stop_gap: "Stop",
  target: "Target",
  target_gap: "Target",
  time: "Time",
  eod: "Close",
};
const UP = "#17c964";
const DOWN = "#f31260";

/** Candles + volume, with each trade's entry and exit marked on the candle it happened in. */
export function PriceChart({ bars, trades }: { bars: Bar[]; trades: Trade[] }) {
  const { ref, chart } = useChart();
  useEffect(() => {
    if (!chart || !bars.length) return;
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: UP,
      downColor: DOWN,
      borderVisible: false,
      wickUpColor: UP,
      wickDownColor: DOWN,
    });
    candles.setData(bars.map((b) => ({ ...b, time: b.time as UTCTimestamp })));
    const vol = chart.addSeries(HistogramSeries, {
      priceScaleId: "vol",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    vol.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    vol.setData(
      bars.map((b) => ({
        time: b.time as UTCTimestamp,
        value: b.volume,
        color: b.close >= b.open ? `${UP}55` : `${DOWN}55`,
      })),
    );

    const times = bars.map((b) => b.time);
    const last =
      times[times.length - 1] +
      (times.length > 1 ? times[times.length - 1] - times[times.length - 2] : 0);
    const at = (t: number) => (t > last ? -1 : floorIndex(times, t)); // -1 = outside the loaded candles
    const markers: SeriesMarker<Time>[] = [];
    const labels = trades.length <= 40; // text on hundreds of markers is unreadable
    for (const t of trades) {
      const i = at(t.entry_time);
      const j = at(t.exit_time);
      const long = t.side > 0;
      if (i >= 0)
        markers.push({
          time: times[i] as UTCTimestamp,
          position: long ? "belowBar" : "aboveBar",
          shape: long ? "arrowUp" : "arrowDown",
          color: long ? UP : DOWN,
          text: labels ? `${long ? "Buy" : "Short"} ${t.qty}` : "",
        });
      if (j >= 0)
        markers.push({
          time: times[j] as UTCTimestamp,
          position: long ? "aboveBar" : "belowBar",
          shape: "circle",
          color: t.net_pnl >= 0 ? UP : DOWN,
          text: labels ? `${EXIT[t.exit_reason] ?? "Exit"} ${compactUsd(t.net_pnl)}` : "",
        });
    }
    markers.sort((a, b) => (a.time as number) - (b.time as number));
    const plugin = createSeriesMarkers(candles, markers);
    chart.timeScale().fitContent();
    return () => {
      plugin.detach();
      chart.removeSeries(vol);
      chart.removeSeries(candles);
    };
  }, [chart, bars, trades]);
  return <div ref={ref} className="chart tall" />;
}
