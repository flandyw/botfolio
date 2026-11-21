import { useMemo, useState } from "react";
import type { Bar, Config, Live, Point, Stat, Trade } from "./api";
import { EquityChart } from "./EquityChart";
import { PriceChart } from "./PriceChart";
import { RankingTable } from "./RankingTable";
import { etTime, fmt, friendly, pct, seriesColors, tone, usd } from "./format";
import { useApi } from "./useApi";

function Status({ loading, error, empty }: { loading?: boolean; error?: string; empty?: boolean }) {
  if (error) return <div className="notice error">Couldn’t load data: {error}</div>;
  if (loading) return <div className="notice">Loading…</div>;
  if (empty) return <div className="notice">Nothing here yet.</div>;
  return null;
}

function Kpi({
  label,
  value,
  sub,
  cls,
}: {
  label: string;
  value: string;
  sub?: string;
  cls?: string;
}) {
  return (
    <div className="kpi">
      <div className="kpi-label">{label}</div>
      <div className={`kpi-value ${cls ?? ""}`}>{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

const DEFAULT_GROUPS = new Set(["pattern", "benchmark"]);

export function Overview({
  run,
  config,
  onPick,
}: {
  run: string;
  config: Config;
  onPick: (p: string) => void;
}) {
  const stats = useApi<Record<string, Stat>>(`runs/${run}/stats`);
  const equity = useApi<Record<string, Point[]>>(`runs/${run}/equity`);
  const [hidden, setHidden] = useState<Record<string, Set<string>>>({});
  const names = useMemo(() => Object.keys(stats.data ?? {}).sort(), [stats.data]);
  const colors = useMemo(() => seriesColors(names, config.symbols), [names, config.symbols]);
  const dashed = useMemo(
    () => new Set(names.filter((n) => stats.data![n].group === "benchmark")),
    [names, stats.data],
  );
  const visible = useMemo(() => {
    const h = hidden[run];
    return new Set(names.filter((n) => (h ? !h.has(n) : DEFAULT_GROUPS.has(stats.data![n].group))));
  }, [names, hidden, run, stats.data]);
  const toggle = (n: string) =>
    setHidden((h) => {
      const cur = h[run] ?? new Set(names.filter((x) => !visible.has(x)));
      const next = new Set(cur);
      if (next.has(n)) next.delete(n);
      else next.add(n);
      return { ...h, [run]: next };
    });

  if (!stats.data || !equity.data)
    return <Status loading={stats.loading || equity.loading} error={stats.error || equity.error} />;
  const entries = Object.entries(stats.data);
  if (!entries.length) return <Status empty />;
  const best = entries
    .filter(([, s]) => s.group === "pattern")
    .sort(([, a], [, b]) => (b.total_return_pct ?? -1e9) - (a.total_return_pct ?? -1e9))[0];
  const bench = entries.filter(([, s]) => s.group === "benchmark");
  const benchAvg =
    bench.reduce((a, [, s]) => a + (s.total_return_pct ?? 0), 0) / (bench.length || 1);
  const trends = entries.filter(([n]) => n.startsWith("trend_"));
  const beats = trends.filter(
    ([n, s]) =>
      (s.total_return_pct ?? -1e9) >
      (stats.data![`buy_hold_${n.slice(6)}`]?.total_return_pct ?? 1e9),
  ).length;
  const sig = entries.filter(([, s]) => s.verdict === "significant edge").length;

  return (
    <>
      <div className="kpis">
        <Kpi
          label="Best strategy"
          value={best ? pct(best[1].total_return_pct) : "—"}
          sub={best ? friendly(best[0]) : undefined}
          cls={tone(best?.[1].total_return_pct)}
        />
        <Kpi
          label="Buy & hold (avg)"
          value={pct(benchAvg)}
          sub={`${bench.length} ETFs, dividends after ${config.withholding_pct}% withholding`}
          cls={tone(benchAvg)}
        />
        <Kpi
          label="Trend holds beating buy & hold"
          value={trends.length ? `${beats} / ${trends.length}` : "—"}
          sub="same ETF, after costs and tax"
        />
        <Kpi
          label="Statistically significant"
          value={`${sig}`}
          sub="edges vs random entries (q < 0.05)"
        />
      </div>
      <section className="card">
        <div className="card-head">
          <h2>Performance</h2>
          <span className="muted small">
            After costs and {config.tax_rate_pct}% Australian tax owed so far · tick rows below to
            compare
          </span>
        </div>
        <EquityChart
          equity={equity.data}
          names={[...visible]}
          colors={colors}
          dashed={dashed}
          start={config.starting_capital}
        />
      </section>
      <section className="card">
        <div className="card-head">
          <h2>Ranking</h2>
        </div>
        <RankingTable
          stats={stats.data}
          visible={visible}
          colors={colors}
          dashed={dashed}
          onToggle={toggle}
          onPick={onPick}
        />
      </section>
    </>
  );
}

const TFS = [
  ["1", "1m"],
  ["5", "5m"],
  ["15", "15m"],
  ["60", "1h"],
  ["D", "1D"],
] as const;
const RANGES = [
  [5, "1W"],
  [20, "1M"],
  [90, "3M"],
  [365, "1Y"],
  [800, "2Y"],
] as const;

export function ChartView({
  run,
  config,
  portfolio,
  setPortfolio,
}: {
  run: string;
  config: Config;
  portfolio: string;
  setPortfolio: (p: string) => void;
}) {
  const stats = useApi<Record<string, Stat>>(`runs/${run}/stats`);
  const portfolios = Object.keys(stats.data ?? {}).filter(
    (n) => stats.data![n].n_trades > 0 || n.startsWith("buy_hold"),
  );
  const fixedSym = config.symbols.find((s) => portfolio.endsWith(`_${s}`));
  const [symbol, setSymbol] = useState(config.symbols[0]);
  const sym = fixedSym ?? symbol;
  const [tf, setTf] = useState<string>("D");
  const [days, setDays] = useState<number>(800);
  const effDays = tf === "1" ? Math.min(days, 20) : days;
  const bars = useApi<Bar[]>(`bars?symbol=${sym}&tf=${tf}&days=${effDays}`);
  const trades = useApi<Trade[]>(
    portfolio ? `runs/${run}/trades?portfolio=${portfolio}&symbol=${sym}` : null,
  );
  return (
    <section className="card">
      <div className="toolbar">
        <select
          value={portfolio}
          onChange={(e) => setPortfolio(e.target.value)}
          aria-label="Portfolio whose trades are marked"
        >
          <option value="">No trade markers</option>
          {portfolios.map((p) => (
            <option key={p} value={p}>
              {friendly(p)}
            </option>
          ))}
        </select>
        <div className="seg" role="group" aria-label="Symbol">
          {config.symbols.map((s) => (
            <button
              key={s}
              className={s === sym ? "on" : ""}
              disabled={!!fixedSym && s !== fixedSym}
              onClick={() => setSymbol(s)}
            >
              {s}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Candle size">
          {TFS.map(([v, l]) => (
            <button key={v} className={v === tf ? "on" : ""} onClick={() => setTf(v)}>
              {l}
            </button>
          ))}
        </div>
        <div className="seg" role="group" aria-label="Range">
          {RANGES.map(([v, l]) => (
            <button
              key={v}
              className={v === days ? "on" : ""}
              disabled={tf === "1" && v > 20}
              onClick={() => setDays(v)}
            >
              {l}
            </button>
          ))}
        </div>
      </div>
      {bars.data?.length ? (
        <PriceChart bars={bars.data} trades={trades.data ?? []} />
      ) : (
        <Status loading={bars.loading} error={bars.error} empty />
      )}
      <p className="muted small">
        Times are New York (ET). Arrows mark entries, dots exits (with net P&amp;L in $).
        Regular-hours {config.feed.toUpperCase()} data; first load of a long range downloads it,
        which can take a few seconds.
      </p>
    </section>
  );
}

export function TradesView({ run }: { run: string }) {
  const trades = useApi<Trade[]>(`runs/${run}/trades?limit=20000`);
  const [portfolio, setPortfolio] = useState("");
  const [symbol, setSymbol] = useState("");
  const [shown, setShown] = useState(200);
  const all = trades.data ?? [];
  const rows = all.filter(
    (t) => (!portfolio || t.portfolio === portfolio) && (!symbol || t.symbol === symbol),
  );
  const opts = (k: "portfolio" | "symbol") => [...new Set(all.map((t) => t[k]))].sort();
  const net = rows.reduce((a, t) => a + t.net_pnl, 0);
  const costs = rows.reduce((a, t) => a + t.costs, 0);
  const wins = rows.filter((t) => t.net_pnl > 0).length;
  return (
    <>
      <div className="kpis">
        <Kpi label="Trades" value={fmt(rows.length, 0)} />
        <Kpi label="Net P&L" value={usd(net)} cls={tone(net)} sub="before income tax" />
        <Kpi label="Costs paid" value={usd(costs)} sub="spread, slippage, commission, fees" />
        <Kpi label="Win rate" value={rows.length ? pct((100 * wins) / rows.length) : "—"} />
      </div>
      <section className="card">
        <div className="toolbar">
          <select
            value={portfolio}
            onChange={(e) => setPortfolio(e.target.value)}
            aria-label="Filter by portfolio"
          >
            <option value="">All portfolios</option>
            {opts("portfolio").map((p) => (
              <option key={p} value={p}>
                {friendly(p)}
              </option>
            ))}
          </select>
          <select
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
            aria-label="Filter by symbol"
          >
            <option value="">All symbols</option>
            {opts("symbol").map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
          <a
            className="button"
            href={`data:text/csv;charset=utf-8,${encodeURIComponent(toCsv(rows))}`}
            download={`${run}-trades.csv`}
          >
            Export CSV
          </a>
        </div>
        {!trades.data ? (
          <Status loading={trades.loading} error={trades.error} />
        ) : !rows.length ? (
          <Status empty />
        ) : (
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
                  <th className="num">Costs</th>
                  <th className="num">Net P&amp;L</th>
                  <th className="num">Return</th>
                  <th>Exit reason</th>
                </tr>
              </thead>
              <tbody>
                {rows.slice(0, shown).map((t) => (
                  <tr key={t.trade_id}>
                    <td className="mono small">{etTime(t.entry_time)}</td>
                    <td className="mono small">{etTime(t.exit_time)}</td>
                    <td>{friendly(t.portfolio)}</td>
                    <td>{t.symbol}</td>
                    <td className={t.side > 0 ? "up" : "down"}>{t.side > 0 ? "Long" : "Short"}</td>
                    <td className="num">{fmt(t.qty, 0)}</td>
                    <td className="num">{fmt(t.entry_price)}</td>
                    <td className="num">{fmt(t.exit_price)}</td>
                    <td className="num">{usd(t.costs, 2)}</td>
                    <td className={`num ${tone(t.net_pnl)}`}>{usd(t.net_pnl, 2)}</td>
                    <td className={`num ${tone(t.return_pct)}`}>{pct(t.return_pct, 2)}</td>
                    <td className="muted small">{t.exit_reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {rows.length > shown && (
              <button className="more" onClick={() => setShown((n) => n + 500)}>
                Show more ({rows.length - shown} left)
              </button>
            )}
          </div>
        )}
      </section>
    </>
  );
}

function toCsv(rows: Trade[]) {
  const cols = [
    "trade_id",
    "portfolio",
    "symbol",
    "side",
    "qty",
    "entry_price",
    "exit_price",
    "gross_pnl",
    "costs",
    "net_pnl",
    "return_pct",
    "exit_reason",
  ] as const;
  return [cols.join(","), ...rows.map((r) => cols.map((c) => r[c]).join(","))].join("\n");
}

export function LiveView({ config }: { config: Config }) {
  const live = useApi<Live>("live", 30_000);
  if (!live.data) return <Status loading={live.loading} error={live.error} />;
  const d = live.data;
  const age = d.heartbeat_age_s;
  const healthy = age != null && age < 300;
  const eq = Object.entries(d.equity).sort(([, a], [, b]) => b - a);
  const taxTotal = Object.values(d.tax_due).reduce((a, b) => a + b, 0);
  return (
    <>
      <div className="kpis">
        <Kpi
          label="Forward bot"
          value={healthy ? "Running" : "Stale"}
          cls={healthy ? "up" : "down"}
          sub={age == null ? "no heartbeat" : `heartbeat ${Math.round(age)}s ago`}
        />
        <Kpi
          label="US market"
          value={d.market_open ? "Open" : "Closed"}
          sub={
            d.session
              ? `${new Date(d.session.open).toLocaleTimeString()}–${new Date(d.session.close).toLocaleTimeString()} your time`
              : "no session today"
          }
        />
        <Kpi label="Open positions" value={fmt(d.positions.length, 0)} />
        <Kpi
          label="Tax accrued this FY"
          value={usd(taxTotal)}
          sub={`all portfolios, at ${config.tax_rate_pct}%`}
        />
      </div>
      <section className="card">
        <div className="card-head">
          <h2>Open positions</h2>
          <span className="muted small">refreshes every 30 s</span>
        </div>
        {!d.positions.length ? (
          <Status empty />
        ) : (
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
                  <th className="num">Dividends</th>
                  <th className="num">Unrealised</th>
                  <th>Since (ET)</th>
                </tr>
              </thead>
              <tbody>
                {d.positions.map((p) => (
                  <tr key={`${p.portfolio}-${p.symbol}`}>
                    <td>{friendly(p.portfolio)}</td>
                    <td>{p.symbol}</td>
                    <td className={p.side > 0 ? "up" : "down"}>{p.side > 0 ? "Long" : "Short"}</td>
                    <td className="num">{fmt(p.qty, 0)}</td>
                    <td className="num">{fmt(p.entry_price)}</td>
                    <td className="num">{fmt(p.last)}</td>
                    <td className="num">{usd(p.dividends, 2)}</td>
                    <td className={`num ${tone(p.unrealised)}`}>{usd(p.unrealised, 2)}</td>
                    <td className="mono small">{etTime(p.entry_time)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
      <section className="card">
        <div className="card-head">
          <h2>Live equity</h2>
          <span className="muted small">after tax owed so far</span>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Portfolio</th>
                <th className="num">Equity</th>
                <th className="num">Since start</th>
                <th className="num">Tax accrued</th>
              </tr>
            </thead>
            <tbody>
              {eq.map(([n, v]) => (
                <tr key={n}>
                  <td>{friendly(n)}</td>
                  <td className="num">{usd(v)}</td>
                  <td className={`num ${tone(v - config.starting_capital)}`}>
                    {pct((v / config.starting_capital - 1) * 100, 2)}
                  </td>
                  <td className="num">{usd(d.tax_due[n] ?? 0)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
