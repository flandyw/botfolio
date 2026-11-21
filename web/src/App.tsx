import { useEffect, useMemo, useState } from "react";
import type { AlpacaSnapshotsResponse, AlpacaStatus, Config, Live, Run } from "./api";
import "./App.css";
import { pct } from "./format";
import { useAlpacaStream } from "./useAlpacaStream";
import { useApi } from "./useApi";
import { ChartView, LiveView, Overview, TradesView } from "./views";

const TABS = [
  ["overview", "Overview"],
  ["chart", "Chart"],
  ["trades", "Trades"],
  ["live", "Live"],
] as const;
type Tab = (typeof TABS)[number][0];

function initialTheme(): "dark" | "light" {
  try {
    const saved = localStorage.getItem("botfolio-theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch {
    // ignore storage errors
  }
  return matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export default function App() {
  const [theme, setTheme] = useState<"dark" | "light">(initialTheme);
  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem("botfolio-theme", theme);
    } catch {
      // ignore storage errors
    }
  }, [theme]);

  const runs = useApi<Run[]>("runs");
  const cfg = useApi<Config>("config");
  const live = useApi<Live>("live", 60_000);
  const alpacaStatus = useApi<AlpacaStatus>("alpaca/status", 60_000);

  const symbols = useMemo(() => cfg.data?.symbols ?? ["QQQ", "SPY", "VGT", "XLK", "SOXX", "VUG"], [cfg.data]);
  const symbolsQuery = symbols.join(",");
  const alpacaConnected = Boolean(alpacaStatus.data?.connected);

  const snapshots = useApi<AlpacaSnapshotsResponse>(
    alpacaConnected ? `alpaca/snapshots?symbols=${encodeURIComponent(symbolsQuery)}` : null,
    30_000,
  );
  const stream = useAlpacaStream(symbols, alpacaConnected);

  const [tab, setTab] = useState<Tab>("overview");
  const [runId, setRunId] = useState<string>();
  const [portfolio, setPortfolio] = useState("");
  const [symbol, setSymbol] = useState("");

  const activeRun =
    runs.data?.find((r) => r.run_id === runId) ??
    runs.data?.find((r) => r.mode === "backtest" && r.status === "ok") ??
    runs.data?.[0];

  const start = cfg.data?.starting_capital ?? 100_000;
  const openTrades = (p: string) => {
    setPortfolio(p);
    setTab("trades");
  };
  const openChartSymbol = (sym: string) => {
    setSymbol(sym);
    setTab("chart");
  };

  const botAlive =
    live.data?.heartbeat_age_s != null ? live.data.heartbeat_age_s < 300 : undefined;
  const marketOpen = alpacaStatus.data?.clock?.isOpen ?? live.data?.market_open;

  const tickerItems = useMemo(() => {
    const snapMap = new Map(
      (snapshots.data?.snapshots ?? []).map((s) => [s.symbol, s]),
    );
    return symbols.map((sym) => {
      const snap = snapMap.get(sym);
      const bar = stream.latestBars[sym];
      const price = bar?.close ?? snap?.price ?? null;
      const prevClose = snap?.prevClose ?? null;
      const changePct =
        price != null && prevClose != null && prevClose > 0
          ? ((price - prevClose) / prevClose) * 100
          : (snap?.changePct ?? null);
      return { symbol: sym, price, changePct, streaming: Boolean(bar) };
    });
  }, [symbols, snapshots.data, stream.latestBars]);

  return (
    <>
      <div className="ticker-strip">
        <span className="ticker-badge" title="Powered by @alpacahq/alpaca-trade-api (paper, read-only)">
          <span
            className="dot"
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: alpacaConnected
                ? stream.connected
                  ? "var(--up)"
                  : "var(--accent)"
                : "var(--muted)",
            }}
          />
          {alpacaConnected
            ? stream.connected
              ? `ALPACA WS · ${(alpacaStatus.data?.feed ?? "iex").toUpperCase()}`
              : `ALPACA JS · ${(alpacaStatus.data?.feed ?? "iex").toUpperCase()}`
            : "ALPACA JS · OFFLINE"}
        </span>
        {tickerItems.map((item) => (
          <button
            key={item.symbol}
            className="ticker-item"
            onClick={() => openChartSymbol(item.symbol)}
            title={`Open ${item.symbol} chart`}
          >
            <span className="ticker-sym">{item.symbol}</span>
            <span className="ticker-px">
              {item.price != null ? `$${item.price.toFixed(2)}` : "—"}
            </span>
            {item.changePct != null && (
              <span className={`ticker-chg ${item.changePct >= 0 ? "up" : "down"}`}>
                {pct(item.changePct, 2)}
              </span>
            )}
          </button>
        ))}
      </div>

      <header className="top">
        <div className="brand">
          <span className="brand-mark">B</span>
          <span>Botfolio</span>
          <span className="brand-sub">Paper</span>
        </div>

        <nav className="tabs">
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? "on" : ""} onClick={() => setTab(id)}>
              {label}
            </button>
          ))}
        </nav>

        <div className="spacer" />

        {runs.data && runs.data.length > 0 && (
          <select
            className="run"
            value={activeRun?.run_id ?? ""}
            onChange={(e) => {
              setRunId(e.target.value);
              setPortfolio("");
            }}
          >
            {runs.data.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                {r.run_id.slice(0, 8)} · {r.mode} · {(r.start ?? r.created_at).slice(0, 10)} →{" "}
                {(r.end ?? "now").slice(0, 10)}
              </option>
            ))}
          </select>
        )}

        <button
          className={`pill ${alpacaConnected ? "ok" : "warn"}`}
          onClick={() => setTab("live")}
          title={
            alpacaConnected
              ? `Connected to Alpaca Paper (${alpacaStatus.data?.accountNumber ?? "PA"}) via @alpacahq/alpaca-trade-api`
              : (alpacaStatus.data?.error ?? "Configure ALPACA_API_KEY & ALPACA_SECRET_KEY in .env")
          }
        >
          <span className="dot" />
          {marketOpen ? "Market Open" : "Market Closed"}
        </button>

        <button
          className={`pill ${botAlive === true ? "ok" : botAlive === false ? "bad" : ""}`}
          onClick={() => setTab("live")}
          title="Forward bot status"
        >
          <span className="dot" />
          {botAlive === true ? "Bot Live" : botAlive === false ? "Bot Down" : "Bot Idle"}
        </button>

        <button
          className="icon-btn"
          onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
          title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          aria-label="Toggle theme"
        >
          {theme === "dark" ? "☀" : "☾"}
        </button>
      </header>

      <main>
        {runs.error && <div className="notice error">API unreachable: {runs.error}</div>}
        {!runs.error && tab !== "live" && !runs.loading && !activeRun && (
          <div className="notice">
            No runs yet. Execute <code>botfolio backtest</code> or <code>botfolio live</code> to
            populate strategies, or open the <strong>Live</strong> tab for Alpaca paper account
            analytics.
          </div>
        )}

        {tab === "overview" && activeRun && (
          <Overview run={activeRun} start={start} onSelectPortfolio={openTrades} />
        )}
        {tab === "chart" && (
          <ChartView
            run={activeRun ?? null}
            cfg={cfg.data ?? null}
            portfolio={portfolio}
            onPortfolio={setPortfolio}
            symbol={symbol}
            onSymbol={setSymbol}
            alpacaConnected={alpacaConnected}
            snapshots={snapshots.data?.snapshots ?? []}
            latestBars={stream.latestBars}
            streamConnected={stream.connected}
          />
        )}
        {tab === "trades" && activeRun && (
          <TradesView
            run={activeRun}
            portfolio={portfolio}
            onPortfolio={setPortfolio}
            onSymbol={openChartSymbol}
          />
        )}
        {tab === "live" && (
          <LiveView
            live={live.data ?? null}
            error={live.error ?? null}
            start={start}
            alpacaStatus={alpacaStatus.data ?? null}
            snapshots={snapshots.data?.snapshots ?? []}
            latestBars={stream.latestBars}
            streamConnected={stream.connected}
            onOpenChart={openChartSymbol}
          />
        )}
      </main>

      <footer className="muted small">
        <span>
          Read-only paper dashboard · Market data &amp; account powered by{" "}
          <code>@alpacahq/alpaca-trade-api</code>
        </span>
        <span>
          {cfg.data
            ? `Universe: ${cfg.data.symbols.join(", ")} · Feed: ${cfg.data.feed.toUpperCase()} · Capital: $${start.toLocaleString()}`
            : ""}
        </span>
      </footer>
    </>
  );
}
