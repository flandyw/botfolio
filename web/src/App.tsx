import { useEffect, useMemo, useState } from "react";
import type { AlpacaSnapshotsResponse, AlpacaStatus, Config, Live } from "./api";
import { cx } from "./classes";
import { Desk } from "./Desk";
import { ago, etDayTime, etTime } from "./format";
import { Markets } from "./Markets";
import { Message } from "./ui";
import { useAlpacaStream } from "./useAlpacaStream";
import { useApi } from "./useApi";

type Theme = "dark" | "light";
type Route = { view: "desk" | "markets"; symbol: string };

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem("botfolio-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // storage is unavailable
  }
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** `#/markets/SPY` or `#/desk`; anything else is the desk. */
function readRoute(): Route {
  const [view, raw = ""] = location.hash.replace(/^#\/?/, "").split("/");
  let symbol = "";
  try {
    symbol = decodeURIComponent(raw).toUpperCase();
  } catch {
    // a malformed link opens the page without a symbol
  }
  return { view: view === "markets" ? "markets" : "desk", symbol };
}

export default function App() {
  const [theme, setTheme] = useState<Theme>(initialTheme);
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem("botfolio-theme", theme);
    } catch {
      // storage is unavailable
    }
  }, [theme]);

  const [route, setRoute] = useState<Route>(readRoute);
  useEffect(() => {
    const onHash = () => setRoute(readRoute());
    addEventListener("hashchange", onHash);
    return () => removeEventListener("hashchange", onHash);
  }, []);
  const openSymbol = (symbol: string) => {
    location.hash = `#/markets/${encodeURIComponent(symbol)}`;
  };

  const config = useApi<Config>("config", 30_000);
  const live = useApi<Live>("live", 5_000);
  const status = useApi<AlpacaStatus>("alpaca/status", 60_000);
  const connected = Boolean(status.data?.connected);

  const symbols = useMemo(() => config.data?.symbols ?? [], [config.data]);
  const etfs = useMemo(() => config.data?.etfs ?? [], [config.data]);
  const snapshots = useApi<AlpacaSnapshotsResponse>(
    connected && symbols.length && route.view === "markets"
      ? `alpaca/snapshots?symbols=${encodeURIComponent(symbols.join(","))}`
      : null,
    30_000,
  );
  const stream = useAlpacaStream(symbols, connected && route.view === "markets");

  const clock = status.data?.clock;
  const marketOpen = clock?.isOpen ?? live.data?.market_open ?? null;
  const market =
    marketOpen == null
      ? "Market hours unknown"
      : marketOpen
        ? `Market open${clock?.nextClose ? `, closes ${etTime(clock.nextClose)} ET` : ""}`
        : `Market closed${clock?.nextOpen ? `, opens ${etDayTime(clock.nextOpen)} ET` : ""}`;
  const age = live.data?.heartbeat_age_s;
  const workerOk = age != null && age >= 0 && age < 120;
  const armed = Boolean(live.data?.enabled && live.data.armed);

  return (
    <div className="mx-auto min-h-svh max-w-[1480px] px-4 md:px-8">
      <header className="top-0 z-20 flex flex-wrap items-baseline gap-x-8 gap-y-1 border-b border-ink bg-bg py-4 lg:sticky">
        <a href="#/desk" className="text-[17px] font-semibold tracking-tight">
          Botfolio
        </a>
        <nav className="flex gap-5 text-[14px]" aria-label="Pages">
          {(
            [
              ["desk", "Desk", "#/desk"],
              ["markets", "Markets", `#/markets${route.symbol ? `/${route.symbol}` : ""}`],
            ] as const
          ).map(([view, text, href]) => (
            <a
              key={view}
              href={href}
              aria-current={route.view === view ? "page" : undefined}
              className={cx(
                "border-b-2 pb-0.5",
                route.view === view ? "border-ink" : "border-transparent text-dim hover:text-ink",
              )}
            >
              {text}
            </a>
          ))}
        </nav>
        <div className="ml-auto flex flex-wrap items-baseline gap-x-6 gap-y-1 text-[13px]">
          <span className="text-dim">{market}</span>
          {live.data && (
            <>
              <span className={workerOk ? "text-dim" : "text-down"}>
                {age == null ? "Worker not seen yet" : workerOk ? "Worker running" : `Worker silent, last seen ${ago(age)}`}
              </span>
              <span className={armed ? "text-dim" : "text-warn"}>{armed ? "Entries armed" : "Entries paused"}</span>
            </>
          )}
          <button
            type="button"
            className="text-dim underline decoration-rule underline-offset-4 hover:text-ink"
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          >
            {theme === "dark" ? "Light" : "Dark"}
          </button>
        </div>
      </header>

      <main className="py-6">
        {config.error && <Message error>{config.error}</Message>}
        {status.error && <Message error>{status.error}</Message>}
        {route.view === "desk" ? (
          <Desk
            live={live.data ?? null}
            liveError={live.error ?? null}
            status={status.data ?? null}
            etfs={etfs.length}
            onOpen={openSymbol}
          />
        ) : (
          <Markets
            symbols={route.symbol && !symbols.includes(route.symbol) ? [route.symbol, ...symbols] : symbols}
            etfs={etfs}
            symbol={route.symbol}
            onSymbol={openSymbol}
            connected={connected}
            snapshots={snapshots.data?.snapshots ?? []}
            latestBars={stream.latestBars}
            streaming={stream.connected}
          />
        )}
      </main>
    </div>
  );
}
