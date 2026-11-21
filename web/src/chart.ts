import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type DeepPartial,
  type IChartApi,
  type ChartOptions,
} from "lightweight-charts";

const css = (name: string) =>
  getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function themeOptions(): DeepPartial<ChartOptions> {
  const grid = css("--grid");
  return {
    layout: {
      background: { type: ColorType.Solid, color: "transparent" },
      textColor: css("--muted"),
      fontFamily: css("--font-mono"),
      fontSize: 11,
      panes: { separatorColor: css("--line"), separatorHoverColor: css("--line") },
      attributionLogo: false,
    },
    grid: { vertLines: { color: grid }, horzLines: { color: grid } },
    rightPriceScale: { borderColor: css("--line") },
    timeScale: { borderColor: css("--line"), timeVisible: true, secondsVisible: false },
    crosshair: { mode: CrosshairMode.Normal },
  };
}

/** Creates a lightweight-chart in the returned ref'd div, kept in sync with the light/dark theme. */
export function useChart(extra: DeepPartial<ChartOptions> = {}) {
  const ref = useRef<HTMLDivElement>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const c = createChart(ref.current, { autoSize: true, ...themeOptions(), ...extra });
    const mq = matchMedia("(prefers-color-scheme: dark)");
    const retheme = () => c.applyOptions(themeOptions());
    mq.addEventListener("change", retheme);
    setChart(c);
    return () => {
      mq.removeEventListener("change", retheme);
      c.remove();
      setChart(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- options are fixed per mount
  }, []);
  return { ref, chart };
}

/** Index of the last element of sorted `times` that is <= t (snaps a trade time onto its candle). */
export function floorIndex(times: number[], t: number): number {
  let lo = 0;
  let hi = times.length - 1;
  if (hi < 0 || t < times[0]) return -1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (times[mid] <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}
