import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  createChart,
  type ChartOptions,
  type DeepPartial,
  type IChartApi,
} from "lightweight-charts";

export function themeOptions(): DeepPartial<ChartOptions> {
  const s = getComputedStyle(document.documentElement);
  const v = (k: string) => s.getPropertyValue(k).trim();
  return {
    layout: {
      background: { type: ColorType.Solid, color: v("--panel") || "#111622" },
      textColor: v("--muted") || "#8b93a7",
      fontFamily: v("--font-mono") || "monospace",
      fontSize: 11,
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: v("--grid") || "#1a2234" },
      horzLines: { color: v("--grid") || "#1a2234" },
    },
    rightPriceScale: { borderColor: v("--line") || "#232d42" },
    timeScale: { borderColor: v("--line") || "#232d42", timeVisible: true, secondsVisible: false },
    crosshair: {
      vertLine: { color: v("--line-2") || "#33415c", labelBackgroundColor: v("--panel-2") || "#1a2234" },
      horzLine: { color: v("--line-2") || "#33415c", labelBackgroundColor: v("--panel-2") || "#1a2234" },
    },
  };
}

/**
 * Mount a lightweight-charts instance that auto-resizes and re-themes
 * on both system color-scheme changes and explicit `data-theme` toggles.
 */
export function useChart(extra: DeepPartial<ChartOptions> = {}) {
  const ref = useRef<HTMLDivElement>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const c = createChart(ref.current, { autoSize: true, ...themeOptions(), ...extra });
    const retheme = () => c.applyOptions(themeOptions());
    const mq = matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", retheme);
    const mo = new MutationObserver(retheme);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
    setChart(c);
    return () => {
      mq.removeEventListener("change", retheme);
      mo.disconnect();
      c.remove();
      setChart(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- options are fixed per mount
  }, []);
  return { ref, chart };
}

/** Index of the last element in sorted `times` <= `t`, or -1. */
export function floorIndex(times: number[], t: number): number {
  let lo = 0;
  let hi = times.length - 1;
  let ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) {
      ans = mid;
      lo = mid + 1;
    } else hi = mid - 1;
  }
  return ans;
}
