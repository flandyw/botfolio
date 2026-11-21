import { useEffect, useState } from "react";
import type { Config, Live, Run } from "./api";
import { useApi } from "./useApi";
import { ChartView, LiveView, Overview, TradesView } from "./views";

const TABS = [
  ["overview", "Overview"],
  ["chart", "Chart"],
  ["trades", "Trades"],
  ["live", "Live"],
] as const;
type Tab = (typeof TABS)[number][0];

/** Tab, run and portfolio live in the URL hash so views can be bookmarked and shared. */
function useHashState() {
  const read = () => new URLSearchParams(location.hash.slice(1));
  const [params, setParams] = useState(read);
  useEffect(() => {
    const on = () => setParams(read());
    addEventListener("hashchange", on);
    return () => removeEventListener("hashchange", on);
  }, []);
  const set = (patch: Record<string, string>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) {
      if (v) next.set(k, v);
      else next.delete(k);
    }
    location.hash = next.toString();
  };
  return [params, set] as const;
}

function runLabel(r: Run) {
  if (r.mode === "forward") return `Live paper trading · since ${r.start}`;
  return `${r.run_id} · ${r.start} → ${r.end}`;
}

export default function App() {
  const [params, set] = useHashState();
  const runs = useApi<Run[]>("runs");
  const config = useApi<Config>("config");
  const live = useApi<Live>("live", 60_000);
  const tab = (params.get("tab") as Tab) || "overview";
  const visibleRuns = (runs.data ?? []).filter(
    (r) => !r.run_id.startsWith("demo-") && r.status !== "failed",
  );
  const run =
    params.get("run") ||
    visibleRuns.find((r) => r.mode === "backtest")?.run_id ||
    visibleRuns[0]?.run_id ||
    "";
  const healthy = live.data?.heartbeat_age_s != null && live.data.heartbeat_age_s < 300;

  return (
    <>
      <header className="top">
        <div className="brand">
          <img src="/favicon.svg" alt="" width={22} height={22} />
          <span>Botfolio</span>
        </div>
        <nav className="tabs" aria-label="Views">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              className={tab === id ? "on" : ""}
              aria-current={tab === id ? "page" : undefined}
              onClick={() => set({ tab: id })}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="spacer" />
        {tab !== "live" && (
          <select
            className="run"
            value={run}
            onChange={(e) => set({ run: e.target.value, portfolio: "" })}
            aria-label="Run"
          >
            {visibleRuns.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {runLabel(r)}
              </option>
            ))}
          </select>
        )}
        <button
          className={`pill ${healthy ? "ok" : "bad"}`}
          onClick={() => set({ tab: "live" })}
          title="Forward paper-trading bot status"
        >
          <span className="dot" />
          {healthy ? (live.data?.market_open ? "Trading" : "Bot idle") : "Bot stale"}
        </button>
      </header>
      <main>
        {runs.error || config.error ? (
          <div className="notice error">
            Can’t reach the Botfolio API: {runs.error || config.error}
          </div>
        ) : !config.data || !runs.data ? (
          <div className="notice">Loading…</div>
        ) : tab === "overview" ? (
          <Overview
            run={run}
            config={config.data}
            onPick={(p) => set({ tab: "chart", portfolio: p })}
          />
        ) : tab === "chart" ? (
          <ChartView
            run={run}
            config={config.data}
            portfolio={params.get("portfolio") ?? ""}
            setPortfolio={(p) => set({ portfolio: p })}
          />
        ) : tab === "trades" ? (
          <TradesView run={run} />
        ) : (
          <LiveView config={config.data} />
        )}
      </main>
      <footer className="muted small">
        Paper trading only. Simulated results for an Australian resident (US$ accounts): costs, US
        withholding and Australian tax included; FX not modelled.
      </footer>
    </>
  );
}
