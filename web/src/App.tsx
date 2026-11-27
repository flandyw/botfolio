import { useEffect, useMemo, useState } from "react";
import { Activity } from "./Activity";
import { type AlpacaSnapshotsResponse, type AlpacaStatus, type Config, LOCKED, type Live, savedPassword } from "./api";
import { cx } from "./classes";
import { Control } from "./Control";
import { Desk } from "./Desk";
import { ago, etDayTime, etTime } from "./format";
import { Markets } from "./Markets";
import { NewsPage } from "./NewsReview";
import { Performance } from "./Performance";
import { Message } from "./ui";
import { Unlock } from "./Unlock";
import { useAlpacaStream } from "./useAlpacaStream";
import { useApi } from "./useApi";

type Theme = "dark" | "light";
const VIEWS = ["desk", "activity", "performance", "markets", "news", "control"] as const;
type View = (typeof VIEWS)[number];
type Route = { view: View; symbol: string; trade: string };

function initialTheme(): Theme {
  try {
    const saved = localStorage.getItem("botfolio-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // storage is unavailable
  }
  return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Markets accepts a symbol and Activity accepts a journal key. */
function readRoute(): Route {
  const [view, raw = ""] = location.hash.replace(/^#\/?/, "").split("/");
  let selection = "";
  try {
    selection = decodeURIComponent(raw);
  } catch {
    // a malformed link opens the page without a symbol
  }
  return {
    view: VIEWS.find((v) => v === view) ?? "desk",
    symbol: view === "markets" ? selection.toUpperCase() : "",
    trade: view === "activity" ? selection : "",
  };
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
      ? "Market hours unavailable"
      : marketOpen
        ? `Market open · closes ${clock?.nextClose ? `${etTime(clock.nextClose)} ET` : "soon"}`
        : `Market closed${clock?.nextOpen ? ` · opens ${etDayTime(clock.nextOpen)} ET` : ""}`;
  const age = live.data?.heartbeat_age_s;
  const workerOk = age != null && age >= 0 && age < 120;
  const armed = Boolean(live.data?.enabled && live.data.armed);
  const [unlocked, setUnlocked] = useState(() => Boolean(savedPassword()));
  useEffect(() => {
    const lock = () => setUnlocked(false);
    addEventListener(LOCKED, lock);
    return () => removeEventListener(LOCKED, lock);
  }, []);
  const locked = Boolean(config.data?.password_required) && !unlocked;

  return (
    <div className="mx-auto min-h-svh max-w-[1480px] px-4 md:px-8">
      <header className="top-0 z-20 flex flex-wrap items-baseline gap-x-8 gap-y-1 border-b border-ink bg-bg py-4 lg:sticky">
        <a href="#/desk" className="text-[17px] font-semibold tracking-tight">
          Botfolio
        </a>
        <nav className="flex flex-wrap gap-x-5 gap-y-1 text-[14px]" aria-label="Pages">
          {(
            [
              ["desk", "Desk", "#/desk"],
              ["activity", "Activity", "#/activity"],
              ["performance", "Performance", "#/performance"],
              ["markets", "Markets", "#/markets"],
              ["news", "News", "#/news"],
              ["control", "Control", "#/control"],
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
                {age == null ? "Worker not started" : workerOk ? "Worker running" : `Worker offline · last seen ${ago(age)}`}
              </span>
              <a href="#/control" className={cx("hover:underline", armed ? "text-dim" : "text-warn")}>
                {live.data.stopped ? "Emergency stop active" : armed ? "Entries on" : "Entries paused"}
              </a>
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
        {(route.view === "news" || route.view === "control" || route.view === "activity") && locked ? (
          <Unlock onUnlock={() => setUnlocked(true)} />
        ) : route.view === "activity" ? (
          <Activity trade={route.trade} />
        ) : route.view === "news" ? (
          <NewsPage onOpen={openSymbol} />
        ) : route.view === "control" ? (
          config.data ? (
            <Control
              live={live.data ?? null}
              config={config.data}
              onChange={() => {
                live.reload();
                config.reload();
              }}
            />
          ) : (
            <Message>Loading…</Message>
          )
        ) : route.view === "desk" ? (
          <Desk
            live={live.data ?? null}
            liveError={live.error ?? null}
            status={status.data ?? null}
            etfs={etfs.length}
            benchmarks={config.data?.benchmarks}
            onOpen={openSymbol}
          />
        ) : route.view === "performance" ? (
          <Performance status={status.data} statusError={status.error} onOpen={openSymbol} />
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
