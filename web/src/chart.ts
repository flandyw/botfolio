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
      background: { type: ColorType.Solid, color: v("--card") || "#0e121a" },
      textColor: v("--muted-foreground") || "#8a94a8",
      fontFamily: "'Geist Mono Variable', ui-monospace, monospace",
      fontSize: 11,
      attributionLogo: false,
    },
    grid: {
      vertLines: { color: v("--grid") || "#131926" },
      horzLines: { color: v("--grid") || "#131926" },
    },
    rightPriceScale: { borderColor: v("--border") || "#1d2433" },
    timeScale: { borderColor: v("--border") || "#1d2433", timeVisible: true, secondsVisible: false },
    crosshair: {
      vertLine: { color: v("--ring") || "#3a4761", labelBackgroundColor: v("--secondary") || "#171d2a" },
      horzLine: { color: v("--ring") || "#3a4761", labelBackgroundColor: v("--secondary") || "#171d2a" },
    },
  };
}

/**
 * Mount a lightweight-charts instance that auto-resizes and re-themes
 * whenever the `dark` class on <html> is toggled.
 */
export function useChart(extra: DeepPartial<ChartOptions> = {}) {
  const ref = useRef<HTMLDivElement>(null);
  const [chart, setChart] = useState<IChartApi | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    const c = createChart(ref.current, { autoSize: true, ...themeOptions(), ...extra });
    const retheme = () => c.applyOptions(themeOptions());
    const mo = new MutationObserver(retheme);
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    setChart(c);
    return () => {
      mo.disconnect();
      c.remove();
      setChart(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- options are fixed per mount
  }, []);
  return { ref, chart };
}
