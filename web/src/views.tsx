import { useMemo, useState } from "react";
import {
  AlpacaAccountChart,
  BreakdownBarChart,
  CumulativePnlChart,
  MonthlyReturnsHeatmap,
  ReturnDistributionChart,
  RiskReturnScatter,
  type StatRow,
  StrategyComparisonBars,
} from "./AnalyticsCharts";
import type {
  AlpacaAccountResponse,
  AlpacaPortfolioHistory,
  AlpacaSnapshot,
  AlpacaStatus,
  AlpacaStreamBar,
  Bar,
  Config,
  Live,
  Point,
  Run,
  Stat,
  Trade,
} from "./api";
import { EquityChart } from "./EquityChart";
import { fmtTime, friendly, GROUPS, money, num, PALETTE, pct } from "./format";
import { type ChartOverlays, PriceChart } from "./PriceChart";
import { useApi } from "./useApi";

const TFS = ["1", "5", "15", "60", "D"] as const;
const RANGES = [
  [1, "1D"],
  [5, "5D"],
  [30, "1M"],
  [90, "3M"],
  [365, "1Y"],
  [730, "All"],
] as const;

const ALPACA_PERIODS = [
  ["1D", "5Min", "1D"],
  ["1W", "15Min", "1W"],
  ["1M", "1D", "1M"],
  ["3M", "1D", "3M"],
  ["1A", "1D", "1Y"],
] as const;

const toStatRows = (data: Record<string, Stat> | undefined): StatRow[] =>
  Object.entries(data ?? {}).map(([portfolio, s]) => ({ ...s, portfolio }));

const verdictRank = (s: Stat) =>
  s.group === "benchmark" ? 2 : s.verdict === "pass" ? 0 : 1;

function Kpi({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "up" | "down" | "accent";
}) {
  return (
    <div className={`kpi ${tone ? `tone-${tone}` : ""}`}>
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value ${tone === "up" ? "up" : tone === "down" ? "down" : ""}`}>
        {value}
      </div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Overview Tab                                                               */
/* -------------------------------------------------------------------------- */

export function Overview({
  run,
  start,
  onSelectPortfolio,
}: {
  run: Run;
  start: number;
  onSelectPortfolio: (p: string) => void;
}) {
  const stats = useApi<Record<string, Stat>>(`runs/${run.run_id}/stats`);
  const eq = useApi<Record<string, Point[]>>(`runs/${run.run_id}/equity`);
  const [sort, setSort] = useState<{ key: keyof StatRow; dir: 1 | -1 }>({
    key: "sharpe",
    dir: -1,
  });
  const [picked, setPicked] = useState<string[] | null>(null);
  const [eqMode, setEqMode] = useState<"pct" | "usd">("pct");
  const [heatmapPortfolio, setHeatmapPortfolio] = useState<string>("");

  const statList = useMemo(() => toStatRows(stats.data), [stats.data]);

  const rows = useMemo(() => {
    const list = [...statList];
    list.sort((a, b) => {
      const av = a[sort.key];
      const bv = b[sort.key];
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return (av < bv ? -1 : av > bv ? 1 : 0) * sort.dir;
    });
    return list;
  }, [statList, sort]);

  const colors = useMemo(() => {
    const m: Record<string, string> = {};
    statList.forEach((s, i) => {
      m[s.portfolio] = PALETTE[i % PALETTE.length];
    });
    return m;
  }, [statList]);

  const defaultPicked = useMemo(() => {
    const strats = statList
      .filter((s) => s.group !== "benchmark")
      .sort((a, b) => verdictRank(a) - verdictRank(b) || (b.sharpe ?? -99) - (a.sharpe ?? -99))
      .slice(0, 4)
      .map((s) => s.portfolio);
    const benches = statList
      .filter((s) => s.group === "benchmark")
      .map((s) => s.portfolio);
    return [...strats, ...benches];
  }, [statList]);

  const benchmarks = useMemo(
    () =>
      new Set(
        statList.filter((s) => s.group === "benchmark").map((s) => s.portfolio),
      ),
    [statList],
  );

  const visible = picked ?? defaultPicked;
  const toggle = (name: string) =>
    setPicked((cur) => {
      const base = cur ?? defaultPicked;
      return base.includes(name) ? base.filter((x) => x !== name) : [...base, name];
    });

  const nonBench = statList.filter((s) => s.group !== "benchmark");
  const best = [...nonBench].sort((a, b) => (b.sharpe ?? -99) - (a.sharpe ?? -99))[0];
  const spy = statList.find((s) => s.portfolio === "SPY" || s.portfolio === "bh:SPY");
  const passed = nonBench.filter((s) => s.verdict === "pass");
  const avgWin = nonBench.length
    ? nonBench.reduce((acc, s) => acc + (s.win_rate ?? 0), 0) / nonBench.length
    : null;
  const totalTrades = nonBench.reduce((acc, s) => acc + s.n_trades, 0);

  const activeHeatmap = heatmapPortfolio || best?.portfolio || rows[0]?.portfolio || "";
  const heatmapPoints = eq.data?.[activeHeatmap] ?? [];

  const th = (key: keyof StatRow, label: string, right = true) => (
    <th
      className={`${right ? "num" : ""} sortable`}
      onClick={() =>
        setSort((s) => ({ key, dir: s.key === key ? (s.dir === -1 ? 1 : -1) : -1 }))
      }
    >
      {label}
      {sort.key === key ? (sort.dir === -1 ? " ↓" : " ↑") : ""}
    </th>
  );

  return (
    <>
      <section className="kpis">
        <Kpi
          label="Top Strategy"
          value={best ? pct(best.total_return_pct) : "—"}
          sub={best ? `${friendly(best.portfolio)} · Sharpe ${num(best.sharpe)}` : undefined}
          tone={best && (best.total_return_pct ?? 0) >= 0 ? "up" : "down"}
        />
        <Kpi
          label="SPY Benchmark"
          value={spy ? pct(spy.total_return_pct) : "—"}
          sub={
            spy
              ? `Sharpe ${num(spy.sharpe)} · Max DD ${pct(spy.max_drawdown_pct == null ? null : -spy.max_drawdown_pct)}`
              : undefined
          }
          tone="accent"
        />
        <Kpi
          label="Passed Gate"
          value={`${passed.length} / ${nonBench.length}`}
          sub="FDR q < 0.05 & net positive"
          tone={passed.length > 0 ? "up" : undefined}
        />
        <Kpi
          label="Avg Win Rate"
          value={avgWin != null ? `${avgWin.toFixed(1)}%` : "—"}
          sub={`${totalTrades.toLocaleString()} total strategy trades`}
        />
        <Kpi
          label="Run Window"
          value={run.mode}
          sub={`${(run.start ?? run.created_at).slice(0, 10)} → ${(run.end ?? "now").slice(0, 10)}`}
        />
      </section>

      <section className="card">
        <div className="card-head">
          <div className="card-title-group">
            <h2>Return &amp; Drawdown</h2>
            <span className="muted small">Hover chart for crosshair readout · click table swatches to compare</span>
          </div>
          <div className="card-actions">
            <div className="seg">
              <button className={eqMode === "pct" ? "on" : ""} onClick={() => setEqMode("pct")}>
                Return %
              </button>
              <button className={eqMode === "usd" ? "on" : ""} onClick={() => setEqMode("usd")}>
                Equity $
              </button>
            </div>
          </div>
        </div>
        {eq.data ? (
          <EquityChart
            series={eq.data}
            names={visible}
            colors={colors}
            dashed={benchmarks}
            start={start}
            mode={eqMode}
          />
        ) : (
          <div className="notice">{eq.error ?? "Loading equity curves…"}</div>
        )}
      </section>

      <section className="grid-2">
        <div className="card">
          <div className="card-head">
            <h2>Risk vs. Return</h2>
            <span className="muted small">Bubble size = trade count · click to inspect trades</span>
          </div>
          <RiskReturnScatter
            stats={statList}
            colors={colors}
            onSelect={onSelectPortfolio}
          />
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Strategy Comparison</h2>
            <span className="muted small">Top 10 portfolios ranked by selected metric</span>
          </div>
          <StrategyComparisonBars
            stats={statList}
            colors={colors}
            onSelect={onSelectPortfolio}
          />
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <div className="card-title-group">
            <h2>Monthly Returns Heatmap</h2>
            <span className="muted small">Compounded monthly performance</span>
          </div>
          <select
            value={activeHeatmap}
            onChange={(e) => setHeatmapPortfolio(e.target.value)}
            aria-label="Portfolio for monthly heatmap"
          >
            {statList.map((s) => (
              <option key={s.portfolio} value={s.portfolio}>
                {friendly(s.portfolio)}
              </option>
            ))}
          </select>
        </div>
        <MonthlyReturnsHeatmap points={heatmapPoints} />
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Portfolio Leaderboard</h2>
          <span className="muted small">Click any portfolio name to drill into trades</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th />
                {th("portfolio", "Portfolio", false)}
                {th("group", "Group", false)}
                {th("total_return_pct", "Return")}
                {th("sharpe", "Sharpe")}
                {th("max_drawdown_pct", "Max DD")}
                {th("n_trades", "Trades")}
                {th("win_rate", "Win %")}
                {th("profit_factor", "PF")}
                {th("avg_cost_pct", "Cost/tr")}
                {th("q_mc", "FDR q")}
                {th("verdict", "Gate", false)}
              </tr>
            </thead>
            <tbody>
              {rows.map((s) => (
                <tr key={s.portfolio} className={s.verdict === "fail" ? "dim" : ""}>
                  <td>
                    <label
                      className={`swatch ${benchmarks.has(s.portfolio) ? "dashed" : ""}`}
                      style={{ "--c": colors[s.portfolio] } as React.CSSProperties}
                    >
                      <input
                        type="checkbox"
                        checked={visible.includes(s.portfolio)}
                        onChange={() => toggle(s.portfolio)}
                      />
                    </label>
                  </td>
                  <td>
                    <button className="link" onClick={() => onSelectPortfolio(s.portfolio)}>
                      {friendly(s.portfolio)}
                    </button>
                  </td>
                  <td>
                    <span className={`tag ${s.group}`}>{GROUPS[s.group]}</span>
                  </td>
                  <td className={`num ${(s.total_return_pct ?? 0) >= 0 ? "up" : "down"}`}>
                    {pct(s.total_return_pct)}
                  </td>
                  <td className="num">{num(s.sharpe)}</td>
                  <td className="num down">
                    {pct(s.max_drawdown_pct == null ? null : -s.max_drawdown_pct)}
                  </td>
                  <td className="num">{s.n_trades}</td>
                  <td className="num">
                    {s.win_rate != null ? `${s.win_rate.toFixed(1)}%` : "—"}
                  </td>
                  <td className="num">{num(s.profit_factor)}</td>
                  <td className="num">{pct(s.avg_cost_pct, 3)}</td>
                  <td className="num">{num(s.q_mc, 3)}</td>
                  <td>
                    {s.verdict && (
                      <span className={`tag ${s.verdict === "pass" ? "benchmark" : ""}`}>
                        {s.verdict}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Chart Tab (Candles + Technical Overlays + RSI + Live Alpaca JS Stream)     */
/* -------------------------------------------------------------------------- */

export function ChartView({
  run,
  cfg,
  portfolio,
  onPortfolio,
  symbol,
  onSymbol,
  alpacaConnected,
  snapshots,
  latestBars,
  streamConnected,
}: {
  run: Run | null;
  cfg: Config | null;
  portfolio: string;
  onPortfolio: (p: string) => void;
  symbol: string;
  onSymbol: (s: string) => void;
  alpacaConnected: boolean;
  snapshots: AlpacaSnapshot[];
  latestBars: Record<string, AlpacaStreamBar>;
  streamConnected: boolean;
}) {
  const symbols = cfg?.symbols ?? ["QQQ", "SPY", "VGT", "XLK", "SOXX", "VUG"];
  const sym = symbol || symbols[0];
  const [tf, setTf] = useState<(typeof TFS)[number]>("5");
  const [days, setDays] = useState<number>(30);
  const [dataSource, setDataSource] = useState<"local" | "alpaca">("local");
  const [overlays, setOverlays] = useState<ChartOverlays>({
    sma20: true,
    ema50: false,
    vwap: true,
    bb: false,
    rsi: true,
  });

  const stats = useApi<Record<string, Stat>>(run ? `runs/${run.run_id}/stats` : null);
  const strategies = useMemo(
    () => toStatRows(stats.data).filter((s) => s.group !== "benchmark"),
    [stats.data],
  );
  const activePortfolio = portfolio || strategies[0]?.portfolio || "";

  const effDays = tf === "1" ? Math.min(days, 30) : days;
  const useAlpacaBars = dataSource === "alpaca" && alpacaConnected;
  const barsPath = useAlpacaBars
    ? `alpaca/bars?symbol=${encodeURIComponent(sym)}&tf=${tf}&days=${effDays}`
    : `bars?symbol=${encodeURIComponent(sym)}&tf=${tf}&days=${effDays}`;
  const bars = useApi<Bar[]>(barsPath);

  const trades = useApi<Trade[]>(
    run && activePortfolio
      ? `runs/${run.run_id}/trades?portfolio=${encodeURIComponent(activePortfolio)}&symbol=${encodeURIComponent(sym)}&limit=500`
      : null,
  );

  const snap = snapshots.find((s) => s.symbol === sym);
  const liveBar = alpacaConnected && tf === "1" ? latestBars[sym] ?? null : null;

  const toggleOverlay = (k: keyof ChartOverlays) =>
    setOverlays((prev) => ({ ...prev, [k]: !prev[k] }));

  return (
    <>
      {snap && (
        <section className="kpis">
          <Kpi
            label={`${sym} Last`}
            value={snap.price != null ? `$${snap.price.toFixed(2)}` : "—"}
            sub={
              snap.change != null && snap.changePct != null
                ? `${snap.change >= 0 ? "+" : ""}${snap.change.toFixed(2)} (${pct(snap.changePct, 2)})`
                : "Alpaca JS Snapshot"
            }
            tone={snap.change != null ? (snap.change >= 0 ? "up" : "down") : "accent"}
          />
          <Kpi
            label="Day Range"
            value={
              snap.low != null && snap.high != null
                ? `$${snap.low.toFixed(2)} – $${snap.high.toFixed(2)}`
                : "—"
            }
            sub={snap.open != null ? `Open $${snap.open.toFixed(2)}` : undefined}
          />
          <Kpi
            label="Session VWAP"
            value={snap.vwap != null ? `$${snap.vwap.toFixed(2)}` : "—"}
            sub={snap.volume != null ? `Vol ${snap.volume.toLocaleString()}` : undefined}
          />
          <Kpi
            label="Bid / Ask"
            value={
              snap.bid != null && snap.ask != null
                ? `$${snap.bid.toFixed(2)} / $${snap.ask.toFixed(2)}`
                : "—"
            }
            sub={snap.spread != null ? `Spread $${snap.spread.toFixed(3)}` : undefined}
          />
        </section>
      )}

      <section className="card">
        <div className="toolbar">
          <div className="seg">
            {symbols.map((s) => (
              <button key={s} className={sym === s ? "on" : ""} onClick={() => onSymbol(s)}>
                {s}
              </button>
            ))}
          </div>
          <div className="seg">
            {TFS.map((t) => (
              <button key={t} className={tf === t ? "on" : ""} onClick={() => setTf(t)}>
                {t === "D" ? "1D" : t === "60" ? "1H" : `${t}m`}
              </button>
            ))}
          </div>
          <div className="seg">
            {RANGES.map(([d, label]) => (
              <button
                key={d}
                className={effDays === d ? "on" : ""}
                disabled={tf === "1" && d > 30}
                onClick={() => setDays(d)}
              >
                {label}
              </button>
            ))}
          </div>

          <div className="seg" title="Switch between local cached SQLite/Parquet bars and live @alpacahq/alpaca-trade-api REST bars">
            <button
              className={!useAlpacaBars ? "on" : ""}
              onClick={() => setDataSource("local")}
            >
              Cache
            </button>
            <button
              className={useAlpacaBars ? "on" : ""}
              disabled={!alpacaConnected}
              onClick={() => setDataSource("alpaca")}
            >
              Alpaca JS
            </button>
          </div>

          {strategies.length > 0 && (
            <select
              value={activePortfolio}
              onChange={(e) => onPortfolio(e.target.value)}
              aria-label="Strategy trade markers"
            >
              <option value="">No trade markers</option>
              {strategies.map((s) => (
                <option key={s.portfolio} value={s.portfolio}>
                  {s.portfolio} ({s.n_trades})
                </option>
              ))}
            </select>
          )}
        </div>

        <div className="toolbar" style={{ marginBottom: 10 }}>
          <span className="muted small">Indicators:</span>
          <button className={`chip ${overlays.sma20 ? "on" : ""}`} onClick={() => toggleOverlay("sma20")}>
            <span className="chip-dot" style={{ background: "#f59e0b" }} />
            SMA 20
          </button>
          <button className={`chip ${overlays.ema50 ? "on" : ""}`} onClick={() => toggleOverlay("ema50")}>
            <span className="chip-dot" style={{ background: "#a855f7" }} />
            EMA 50
          </button>
          <button className={`chip ${overlays.vwap ? "on" : ""}`} onClick={() => toggleOverlay("vwap")}>
            <span className="chip-dot" style={{ background: "#06b6d4" }} />
            VWAP
          </button>
          <button className={`chip ${overlays.bb ? "on" : ""}`} onClick={() => toggleOverlay("bb")}>
            <span className="chip-dot" style={{ background: "#3b82f6" }} />
            Bollinger (20,2)
          </button>
          <button className={`chip ${overlays.rsi ? "on" : ""}`} onClick={() => toggleOverlay("rsi")}>
            <span className="chip-dot" style={{ background: "#38bdf8" }} />
            RSI 14
          </button>

          <span className="spacer" />
          <span className="muted small mono">
            {bars.loading
              ? "Loading bars…"
              : `${bars.data?.length ?? 0} bars · ${trades.data?.length ?? 0} trades`}
            {streamConnected && tf === "1" ? " · WS live" : ""}
          </span>
        </div>

        {bars.error && <div className="notice error">{bars.error}</div>}
        {!bars.error && bars.data && (
          <PriceChart
            bars={bars.data}
            trades={trades.data ?? []}
            overlays={overlays}
            liveBar={liveBar}
          />
        )}
      </section>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Trades Tab (Cumulative P&L + Return Histogram + Symbol/Exit/Hour Charts)   */
/* -------------------------------------------------------------------------- */

export function TradesView({
  run,
  portfolio,
  onPortfolio,
  onSymbol,
}: {
  run: Run;
  portfolio: string;
  onPortfolio: (p: string) => void;
  onSymbol: (s: string) => void;
}) {
  const [symFilter, setSymFilter] = useState("");
  const [limit, setLimit] = useState(500);
  const stats = useApi<Record<string, Stat>>(`runs/${run.run_id}/stats`);
  const statList = useMemo(() => toStatRows(stats.data), [stats.data]);
  const qs = new URLSearchParams({ limit: String(limit) });
  if (portfolio) qs.set("portfolio", portfolio);
  if (symFilter) qs.set("symbol", symFilter);
  const trades = useApi<Trade[]>(`runs/${run.run_id}/trades?${qs}`);

  const list = useMemo(() => trades.data ?? [], [trades.data]);
  const symbols = useMemo(() => [...new Set(list.map((t) => t.symbol))].sort(), [list]);

  const summary = useMemo(() => {
    const net = list.reduce((a, t) => a + t.net_pnl, 0);
    const gross = list.reduce((a, t) => a + t.gross_pnl, 0);
    const costs = list.reduce((a, t) => a + t.costs, 0);
    const wins = list.filter((t) => t.net_pnl > 0).length;
    const avgHoldMin = list.length
      ? list.reduce((a, t) => a + Math.max(0, t.exit_time - t.entry_time) / 60, 0) / list.length
      : 0;
    return { net, gross, costs, winRate: list.length ? (wins / list.length) * 100 : 0, avgHoldMin };
  }, [list]);

  const bySymbol = useMemo(() => {
    const m = new Map<string, { value: number; count: number }>();
    for (const t of list) {
      const cur = m.get(t.symbol) ?? { value: 0, count: 0 };
      cur.value += t.net_pnl;
      cur.count += 1;
      m.set(t.symbol, cur);
    }
    return [...m.entries()]
      .map(([label, v]) => ({ label, ...v }))
      .sort((a, b) => b.value - a.value);
  }, [list]);

  const byExit = useMemo(() => {
    const m = new Map<string, { value: number; count: number }>();
    for (const t of list) {
      const cur = m.get(t.exit_reason) ?? { value: 0, count: 0 };
      cur.value += t.net_pnl;
      cur.count += 1;
      m.set(t.exit_reason, cur);
    }
    return [...m.entries()]
      .map(([label, v]) => ({ label, ...v }))
      .sort((a, b) => b.value - a.value);
  }, [list]);

  const byHour = useMemo(() => {
    const m = new Map<number, { value: number; count: number }>();
    for (const t of list) {
      const hr = new Date(t.entry_time * 1000).getUTCHours();
      const cur = m.get(hr) ?? { value: 0, count: 0 };
      cur.value += t.net_pnl;
      cur.count += 1;
      m.set(hr, cur);
    }
    return [...m.entries()]
      .sort((a, b) => a[0] - b[0])
      .map(([hr, v]) => ({ label: `${String(hr).padStart(2, "0")}:00 ET`, ...v }));
  }, [list]);

  return (
    <>
      <section className="card">
        <div className="toolbar" style={{ marginBottom: 0 }}>
          <select value={portfolio} onChange={(e) => onPortfolio(e.target.value)}>
            <option value="">All portfolios</option>
            {statList
              .filter((s) => s.group !== "benchmark")
              .map((s) => (
                <option key={s.portfolio} value={s.portfolio}>
                  {friendly(s.portfolio)} ({s.n_trades})
                </option>
              ))}
          </select>
          <select value={symFilter} onChange={(e) => setSymFilter(e.target.value)}>
            <option value="">All symbols</option>
            {symbols.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="spacer" />
          <span className="muted small mono">{list.length} trades loaded</span>
        </div>
      </section>

      <section className="kpis">
        <Kpi
          label="Net P&L"
          value={`${summary.net >= 0 ? "+" : ""}${money(summary.net)}`}
          sub={`Gross ${money(summary.gross)}`}
          tone={summary.net >= 0 ? "up" : "down"}
        />
        <Kpi
          label="Execution Costs"
          value={money(summary.costs)}
          sub={list.length ? `${money(summary.costs / list.length)} / trade` : undefined}
        />
        <Kpi
          label="Win Rate"
          value={`${summary.winRate.toFixed(1)}%`}
          sub={`${list.length} trades`}
          tone={summary.winRate >= 50 ? "up" : "down"}
        />
        <Kpi
          label="Avg Hold"
          value={`${summary.avgHoldMin.toFixed(0)}m`}
          sub="Entry to exit duration"
        />
      </section>

      <section className="grid-2">
        <div className="card">
          <div className="card-head">
            <h2>Cumulative Net vs. Gross P&amp;L</h2>
            <span className="muted small">Shows slippage &amp; commission drag</span>
          </div>
          <CumulativePnlChart trades={list} />
        </div>

        <div className="card">
          <div className="card-head">
            <h2>Trade Return Distribution</h2>
            <span className="muted small">Histogram of per-trade return %</span>
          </div>
          <ReturnDistributionChart trades={list} />
        </div>
      </section>

      <section className="grid-3">
        <div className="card">
          <div className="card-head">
            <h2>P&amp;L by Symbol</h2>
            <span className="muted small">Click symbol to chart</span>
          </div>
          <BreakdownBarChart items={bySymbol} onSelect={onSymbol} />
        </div>

        <div className="card">
          <div className="card-head">
            <h2>P&amp;L by Exit Reason</h2>
            <span className="muted small">Stop vs. target vs. EOD</span>
          </div>
          <BreakdownBarChart items={byExit} />
        </div>

        <div className="card">
          <div className="card-head">
            <h2>P&amp;L by Entry Hour (ET)</h2>
            <span className="muted small">Intraday session profile</span>
          </div>
          <BreakdownBarChart items={byHour} />
        </div>
      </section>

      <section className="card">
        <div className="card-head">
          <h2>Trade Ledger</h2>
          <span className="muted small">Most recent fills</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Entry (ET)</th>
                <th>Exit (ET)</th>
                <th>Portfolio</th>
                <th>Symbol</th>
                <th>Side</th>
                <th className="num">Qty</th>
                <th className="num">Entry</th>
                <th className="num">Exit</th>
                <th className="num">Net P&amp;L</th>
                <th className="num">Costs</th>
                <th className="num">Ret %</th>
                <th>Reason</th>
              </tr>
            </thead>
            <tbody>
              {list.slice(0, 200).map((t) => (
                <tr key={t.trade_id}>
                  <td className="mono small">{fmtTime(t.entry_time)}</td>
                  <td className="mono small">{fmtTime(t.exit_time)}</td>
                  <td>{friendly(t.portfolio)}</td>
                  <td>
                    <button className="link" onClick={() => onSymbol(t.symbol)}>
                      {t.symbol}
                    </button>
                  </td>
                  <td>
                    <span className={`tag ${t.side > 0 ? "benchmark" : "down"}`}>
                      {t.side > 0 ? "long" : "short"}
                    </span>
                  </td>
                  <td className="num">{t.qty}</td>
                  <td className="num">{t.entry_price.toFixed(2)}</td>
                  <td className="num">{t.exit_price.toFixed(2)}</td>
                  <td className={`num ${t.net_pnl >= 0 ? "up" : "down"}`}>
                    {t.net_pnl >= 0 ? "+" : ""}
                    {money(t.net_pnl)}
                  </td>
                  <td className="num">{money(t.costs)}</td>
                  <td className={`num ${t.return_pct >= 0 ? "up" : "down"}`}>
                    {pct(t.return_pct, 2)}
                  </td>
                  <td>
                    <span className="tag">{t.exit_reason}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {list.length >= limit && (
          <button className="more" onClick={() => setLimit((n) => n + 500)}>
            Load more trades
          </button>
        )}
      </section>
    </>
  );
}

/* -------------------------------------------------------------------------- */
/* Live Tab (Alpaca JS SDK Paper Account + Watchlist + Botfolio Forward Bot)  */
/* -------------------------------------------------------------------------- */

export function LiveView({
  live,
  error,
  start,
  alpacaStatus,
  snapshots,
  latestBars,
  streamConnected,
  onOpenChart,
}: {
  live: Live | null;
  error: string | null;
  start: number;
  alpacaStatus: AlpacaStatus | null;
  snapshots: AlpacaSnapshot[];
  latestBars: Record<string, AlpacaStreamBar>;
  streamConnected: boolean;
  onOpenChart: (sym: string) => void;
}) {
  const alpacaConnected = Boolean(alpacaStatus?.connected);
  const [periodIdx, setPeriodIdx] = useState(2); // 1M default
  const [period, timeframe] = ALPACA_PERIODS[periodIdx];

  const alpacaAcct = useApi<AlpacaAccountResponse>(
    alpacaConnected ? "alpaca/account" : null,
    30_000,
  );
  const alpacaHist = useApi<AlpacaPortfolioHistory>(
    alpacaConnected
      ? `alpaca/portfolio-history?period=${encodeURIComponent(period)}&timeframe=${encodeURIComponent(timeframe)}`
      : null,
    60_000,
  );

  const botAlive = live?.heartbeat_age_s != null ? live.heartbeat_age_s < 300 : false;
  const equityEntries = Object.entries(live?.equity ?? {}).sort((a, b) => b[1] - a[1]);
  const totalTax = Object.values(live?.tax_due ?? {}).reduce((a, b) => a + b, 0);

  const acct = alpacaAcct.data?.account;

  return (
    <>
      {/* Alpaca JS SDK Paper Account Section */}
      <section className="card">
        <div className="card-head">
          <div className="card-title-group">
            <h2>Alpaca Paper Account</h2>
            <span className={`tag ${alpacaConnected ? "benchmark" : "warn"}`}>
              {alpacaConnected
                ? `Connected · ${acct?.accountNumber ?? alpacaStatus?.accountNumber ?? "PA"}`
                : "Offline · Configure ALPACA_API_KEY / ALPACA_SECRET_KEY"}
            </span>
            <span className="tag pattern">@alpacahq/alpaca-trade-api</span>
          </div>
          {alpacaConnected && (
            <div className="seg">
              {ALPACA_PERIODS.map(([p, , label], idx) => (
                <button
                  key={p}
                  className={periodIdx === idx ? "on" : ""}
                  onClick={() => setPeriodIdx(idx)}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>

        {alpacaConnected && acct ? (
          <>
            <div className="kpis" style={{ marginBottom: 14 }}>
              <Kpi
                label="Paper Equity"
                value={acct.equity != null ? money(acct.equity) : "—"}
                sub={`Last close ${acct.lastEquity != null ? money(acct.lastEquity) : "—"}`}
                tone="accent"
              />
              <Kpi
                label="Day P&L"
                value={
                  acct.dayPnl != null
                    ? `${acct.dayPnl >= 0 ? "+" : ""}${money(acct.dayPnl)}`
                    : "—"
                }
                sub={acct.dayPnlPct != null ? pct(acct.dayPnlPct, 2) : undefined}
                tone={(acct.dayPnl ?? 0) >= 0 ? "up" : "down"}
              />
              <Kpi
                label="Cash"
                value={acct.cash != null ? money(acct.cash) : "—"}
                sub={`Buying power ${acct.buyingPower != null ? money(acct.buyingPower) : "—"}`}
              />
              <Kpi
                label="Open Positions"
                value={String(alpacaAcct.data?.positions.length ?? 0)}
                sub={`Long MV ${acct.longMarketValue != null ? money(acct.longMarketValue) : "$0"}`}
              />
            </div>
            <AlpacaAccountChart
              points={alpacaHist.data?.points ?? []}
              baseValue={alpacaHist.data?.baseValue ?? null}
            />
          </>
        ) : (
          <div className="notice">
            {alpacaStatus?.error ??
              "Add paper-only ALPACA_API_KEY and ALPACA_SECRET_KEY in .env to enable live Alpaca JS SDK portfolio history, snapshots, and WebSocket streaming."}
          </div>
        )}
      </section>

      {/* Live ETF Watchlist Snapshot Cards */}
      {snapshots.length > 0 && (
        <section className="card">
          <div className="card-head">
            <div className="card-title-group">
              <h2>Live ETF Watchlist</h2>
              <span className="muted small">
                Snapshots via <code>alpaca.marketData.stocks.stockSnapshots</code>
                {streamConnected ? " + real-time WS bars" : ""}
              </span>
            </div>
          </div>
          <div className="watchlist-grid">
            {snapshots.map((s) => {
              const bar = latestBars[s.symbol];
              const price = bar?.close ?? s.price;
              const chgPct =
                price != null && s.prevClose
                  ? ((price - s.prevClose) / s.prevClose) * 100
                  : s.changePct;
              const isUp = (chgPct ?? 0) >= 0;
              return (
                <div key={s.symbol} className="watch-card" onClick={() => onOpenChart(s.symbol)}>
                  <div className="watch-head">
                    <span className="watch-sym">{s.symbol}</span>
                    <span className={`ticker-chg ${isUp ? "up" : "down"}`}>
                      {chgPct != null ? pct(chgPct, 2) : "—"}
                    </span>
                  </div>
                  <div className="watch-price">
                    {price != null ? `$${price.toFixed(2)}` : "—"}
                  </div>
                  <div className="watch-meta mono">
                    <span>VWAP {s.vwap != null ? `$${s.vwap.toFixed(2)}` : "—"}</span>
                    <span>Vol {s.volume != null ? s.volume.toLocaleString() : "—"}</span>
                    <span>Bid {s.bid != null ? `$${s.bid.toFixed(2)}` : "—"}</span>
                    <span>Ask {s.ask != null ? `$${s.ask.toFixed(2)}` : "—"}</span>
                  </div>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Alpaca Paper Positions & Recent Orders */}
      {alpacaConnected && alpacaAcct.data && (
        <section className="grid-2">
          <div className="card">
            <div className="card-head">
              <h2>Alpaca Paper Positions ({alpacaAcct.data.positions.length})</h2>
            </div>
            {alpacaAcct.data.positions.length === 0 ? (
              <div className="muted small">No open paper positions.</div>
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th className="num">Qty</th>
                      <th className="num">Avg Entry</th>
                      <th className="num">Current</th>
                      <th className="num">Unrealized</th>
                    </tr>
                  </thead>
                  <tbody>
                    {alpacaAcct.data.positions.map((p) => (
                      <tr key={p.symbol}>
                        <td>
                          <button className="link" onClick={() => onOpenChart(p.symbol)}>
                            {p.symbol}
                          </button>
                        </td>
                        <td>
                          <span className={`tag ${p.side === "long" ? "benchmark" : "down"}`}>
                            {p.side}
                          </span>
                        </td>
                        <td className="num">{p.qty}</td>
                        <td className="num">{p.avgEntryPrice.toFixed(2)}</td>
                        <td className="num">{p.currentPrice.toFixed(2)}</td>
                        <td className={`num ${p.unrealizedPl >= 0 ? "up" : "down"}`}>
                          {p.unrealizedPl >= 0 ? "+" : ""}
                          {money(p.unrealizedPl)} ({pct(p.unrealizedPlpc, 2)})
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>

          <div className="card">
            <div className="card-head">
              <h2>Recent Paper Orders ({alpacaAcct.data.orders.length})</h2>
              <span className="muted small">Read-only view</span>
            </div>
            {alpacaAcct.data.orders.length === 0 ? (
              <div className="muted small">No recent paper orders.</div>
            ) : (
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th>Type</th>
                      <th className="num">Qty</th>
                      <th className="num">Fill</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {alpacaAcct.data.orders.slice(0, 15).map((o) => (
                      <tr key={o.id}>
                        <td>{o.symbol}</td>
                        <td>
                          <span className={`tag ${o.side === "buy" ? "benchmark" : "down"}`}>
                            {o.side}
                          </span>
                        </td>
                        <td className="small muted">{o.type}</td>
                        <td className="num">{o.qty}</td>
                        <td className="num">
                          {o.filledAvgPrice != null ? `$${o.filledAvgPrice.toFixed(2)}` : "—"}
                        </td>
                        <td>
                          <span className="tag">{o.status}</span>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}

      {/* Botfolio Forward Engine Status */}
      {error && <div className="notice error">{error}</div>}
      {live && (
        <>
          <section className="kpis">
            <Kpi
              label="Forward Bot"
              value={botAlive ? "Running" : live.heartbeat ? "Stale" : "Idle"}
              sub={
                live.heartbeat_age_s != null
                  ? `Heartbeat ${Math.round(live.heartbeat_age_s)}s ago`
                  : undefined
              }
              tone={botAlive ? "up" : undefined}
            />
            <Kpi
              label="NYSE Market"
              value={live.market_open ? "Open" : "Closed"}
              sub={
                live.session
                  ? `${live.session.open.slice(11, 16)} – ${live.session.close.slice(11, 16)}`
                  : undefined
              }
            />
            <Kpi label="Bot Positions" value={String(live.positions.length)} />
            <Kpi
              label="Tax Owed YTD"
              value={money(totalTax)}
              sub={`Start capital ${money(start)}`}
            />
          </section>

          {equityEntries.length > 0 && (
            <section className="card">
              <div className="card-head">
                <h2>Forward Paper Equity</h2>
                <span className="muted small">Live portfolio marks vs. starting capital</span>
              </div>
              <BreakdownBarChart
                items={equityEntries.map(([name, val]) => ({
                  label: friendly(name),
                  value: val - start,
                }))}
              />
            </section>
          )}

          {live.positions.length > 0 && (
            <section className="card">
              <div className="card-head">
                <h2>Forward Strategy Positions</h2>
              </div>
              <div className="table-scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Portfolio</th>
                      <th>Symbol</th>
                      <th>Side</th>
                      <th className="num">Qty</th>
                      <th className="num">Entry</th>
                      <th className="num">Last</th>
                      <th className="num">Stop</th>
                      <th className="num">Target</th>
                      <th className="num">Unrealized</th>
                    </tr>
                  </thead>
                  <tbody>
                    {live.positions.map((p, i) => (
                      <tr key={i}>
                        <td>{friendly(p.portfolio)}</td>
                        <td>
                          <button className="link" onClick={() => onOpenChart(p.symbol)}>
                            {p.symbol}
                          </button>
                        </td>
                        <td>
                          <span className={`tag ${p.side > 0 ? "benchmark" : "down"}`}>
                            {p.side > 0 ? "long" : "short"}
                          </span>
                        </td>
                        <td className="num">{p.qty}</td>
                        <td className="num">{p.entry_price.toFixed(2)}</td>
                        <td className="num">{p.last.toFixed(2)}</td>
                        <td className="num">{p.stop != null ? p.stop.toFixed(2) : "—"}</td>
                        <td className="num">{p.target != null ? p.target.toFixed(2) : "—"}</td>
                        <td className={`num ${p.unrealised >= 0 ? "up" : "down"}`}>
                          {p.unrealised >= 0 ? "+" : ""}
                          {money(p.unrealised)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </>
  );
}
