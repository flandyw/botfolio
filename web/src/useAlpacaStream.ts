import { useEffect, useState } from "react";
import type { AlpacaStreamBar } from "./api";

/**
 * Subscribe to the server-side `@alpacahq/alpaca-trade-api` WebSocket -> SSE stream
 * for the given symbols. Stays idle when `enabled` is false (e.g. no paper keys).
 */
export function useAlpacaStream(symbols: string[], enabled: boolean) {
  const [connected, setConnected] = useState(false);
  const [latestBars, setLatestBars] = useState<Record<string, AlpacaStreamBar>>({});
  const key = symbols.join(",");

  useEffect(() => {
    if (!enabled || !key) return;
    const es = new EventSource(`/api/alpaca/stream?symbols=${encodeURIComponent(key)}`);
    const onStatus = (ev: MessageEvent) => {
      try {
        const data = JSON.parse(ev.data) as { connected?: boolean };
        setConnected(Boolean(data.connected));
      } catch {
        // ignore malformed status frame
      }
    };
    const onBar = (ev: MessageEvent) => {
      try {
        const bar = JSON.parse(ev.data) as AlpacaStreamBar;
        setLatestBars((prev) => ({ ...prev, [bar.symbol]: bar }));
      } catch {
        // ignore malformed bar frame
      }
    };
    es.addEventListener("status", onStatus);
    es.addEventListener("bar", onBar);
    es.onerror = () => setConnected(false);
    return () => {
      es.removeEventListener("status", onStatus);
      es.removeEventListener("bar", onBar);
      es.close();
      setConnected(false);
    };
  }, [key, enabled]);

  return { connected: enabled && Boolean(key) && connected, latestBars };
}
