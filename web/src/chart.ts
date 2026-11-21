import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  createChart,
  type ChartOptions,
  type DeepPartial,
  type IChartApi,
} from "lightweight-charts";

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/** "#rrggbb" with an alpha channel. */
export const alpha = (hex: string, a: number) =>
  `${hex}${Math.round(a * 255).toString(16).padStart(2, "0")}`;

/** Current theme colours for chart series. */
export const palette = () => ({ up: css("--up"), down: css("--down"), dim: css("--dim"), faint: css("--faint") });

/** Indicator lines, readable on both themes. */
export const LINES = { sma20: "#b07a1e", ema50: "#8a63b5", vwap: "#2b8591", bb: "#74829a", rsi: "#8d877b" };

export function themeOptions(): DeepPartial<ChartOptions> {
  return {
    layout: {
      background: { type: ColorType.Solid, color: css("--bg") },
      textColor: css("--dim"),
      fontFamily: "'IBM Plex Mono', ui-monospace, monospace",
      fontSize: 11,
      attributionLogo: false,
    },
    grid: { vertLines: { color: css("--hair") }, horzLines: { color: css("--hair") } },
    rightPriceScale: { borderColor: css("--rule") },
    timeScale: { borderColor: css("--rule"), timeVisible: true, secondsVisible: false },
    crosshair: {
      vertLine: { color: css("--faint"), labelBackgroundColor: css("--ink") },
      horzLine: { color: css("--faint"), labelBackgroundColor: css("--ink") },
    },
  };
}

/** Changes whenever the light or dark theme is toggled, so series can pick up new colours. */
export function useThemeKey() {
  const [key, setKey] = useState(0);
  useEffect(() => {
    const mo = new MutationObserver(() => setKey((k) => k + 1));
    mo.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
    return () => mo.disconnect();
  }, []);
  return key;
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
