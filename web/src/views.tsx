import { useState } from "react";
import { Kpi, KpiRow, LinkButton, Notice, Panel, Tag, upDown } from "@/components/dash";
import { Segmented } from "@/components/segmented";
import { Toggle } from "@/components/ui/toggle";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { AlpacaAccountChart } from "./AnalyticsCharts";
import type {
  AlpacaAccountResponse,
  AlpacaPortfolioHistory,
  AlpacaSnapshot,
  AlpacaStatus,
  AlpacaStreamBar,
  Bar,
  Live,
} from "./api";
import { money, pct } from "./format";
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

const R = "text-right num"; // numeric cell/head alignment
const usd = (v: number | null | undefined) => (v != null ? `$${v.toFixed(2)}` : "—");
const signed = (v: number) => `${v >= 0 ? "+" : ""}${money(v)}`;

function Grid({ cols, children }: { cols: 2 | 3; children: React.ReactNode }) {
  return (
    <section className={cols === 2 ? "grid gap-4 lg:grid-cols-2" : "grid gap-4 lg:grid-cols-3"}>
      {children}
    </section>
  );
}

const OVERLAY_CHIPS: [keyof ChartOverlays, string, string][] = [
  ["sma20", "SMA 20", "#f59e0b"],
  ["ema50", "EMA 50", "#a855f7"],
  ["vwap", "VWAP", "#06b6d4"],
  ["bb", "Bollinger (20,2)", "#3b82f6"],
  ["rsi", "RSI 14", "#38bdf8"],
];

export function ChartView({
  symbols,
  symbol,
  onSymbol,
  alpacaConnected,
  snapshots,
  latestBars,
  streamConnected,
}: {
  symbols: string[];
  symbol: string;
  onSymbol: (s: string) => void;
  alpacaConnected: boolean;
  snapshots: AlpacaSnapshot[];
  latestBars: Record<string, AlpacaStreamBar>;
  streamConnected: boolean;
}) {
  const sym = symbol || symbols[0];
  const [tf, setTf] = useState<(typeof TFS)[number]>("5");
  const [days, setDays] = useState<number>(30);
  const [overlays, setOverlays] = useState<ChartOverlays>({
    sma20: true,
    ema50: false,
    vwap: true,
    bb: false,
    rsi: true,
  });

  const effDays = tf === "1" ? Math.min(days, 30) : days;
  const bars = useApi<Bar[]>(
    alpacaConnected ? `alpaca/bars?symbol=${encodeURIComponent(sym)}&tf=${tf}&days=${effDays}` : null,
  );

  const snap = snapshots.find((s) => s.symbol === sym);
  const liveBar = alpacaConnected && tf === "1" ? latestBars[sym] ?? null : null;

  const toggleOverlay = (k: keyof ChartOverlays) =>
    setOverlays((prev) => ({ ...prev, [k]: !prev[k] }));

  return (
    <>
      {snap && (
        <KpiRow>
          <Kpi
            label={`${sym} Last`}
            value={usd(snap.price)}
            sub={
              snap.change != null && snap.changePct != null
                ? `${snap.change >= 0 ? "+" : ""}${snap.change.toFixed(2)} (${pct(snap.changePct, 2)})`
                : "Alpaca JS Snapshot"
            }
            tone={snap.change != null ? (snap.change >= 0 ? "up" : "down") : "accent"}
          />
          <Kpi
            label="Day Range"
            value={snap.low != null && snap.high != null ? `${usd(snap.low)} – ${usd(snap.high)}` : "—"}
            sub={snap.open != null ? `Open ${usd(snap.open)}` : undefined}
          />
          <Kpi
            label="Session VWAP"
            value={usd(snap.vwap)}
            sub={snap.volume != null ? `Vol ${snap.volume.toLocaleString()}` : undefined}
          />
          <Kpi
            label="Bid / Ask"
            value={snap.bid != null && snap.ask != null ? `${usd(snap.bid)} / ${usd(snap.ask)}` : "—"}
            sub={snap.spread != null ? `Spread $${snap.spread.toFixed(3)}` : undefined}
          />
        </KpiRow>
      )}

      <Panel>
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <Segmented
            value={sym}
            onChange={onSymbol}
            options={symbols.map((s) => [s, s] as const)}
            label="Symbol"
          />
          <Segmented
            value={tf}
            onChange={setTf}
            options={TFS.map((t) => [t, t === "D" ? "1D" : t === "60" ? "1H" : `${t}m`] as const)}
            label="Timeframe"
          />
          <Segmented
            value={String(effDays)}
            onChange={(d) => setDays(Number(d))}
            options={RANGES.map(([d, label]) => [String(d), label, tf === "1" && d > 30] as const)}
            label="Range"
          />
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-1.5">
          <span className="mr-1 text-xs text-muted-foreground">Indicators</span>
          {OVERLAY_CHIPS.map(([k, label, color]) => (
            <Toggle
              key={k}
              size="sm"
              variant="outline"
              pressed={overlays[k]}
              onPressedChange={() => toggleOverlay(k)}
              className="text-xs data-[state=on]:border-primary/60 data-[state=on]:bg-primary/10"
            >
              <span className="size-2 rounded-full" style={{ background: color }} />
              {label}
            </Toggle>
          ))}
          <span className="ml-auto font-mono text-xs text-muted-foreground">
            {bars.loading ? "Loading bars…" : `${bars.data?.length ?? 0} bars`}
            {streamConnected && tf === "1" ? " · WS live" : ""}
          </span>
        </div>

        {!alpacaConnected && <Notice>Connect to Alpaca Paper to view market charts.</Notice>}
        {bars.error && <Notice error>{bars.error}</Notice>}
        {!bars.error && bars.data && (
          <PriceChart bars={bars.data} overlays={overlays} liveBar={liveBar} />
        )}
      </Panel>
    </>
  );
}

export function LiveView({
  live,
  error,
  alpacaStatus,
  snapshots,
  latestBars,
  streamConnected,
  onOpenChart,
}: {
  live: Live | null;
  error: string | null;
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

  const monitorReady = live?.execution_age_s != null && live.execution_age_s >= 0 && live.execution_age_s <= 20;
  const workerReady = live?.heartbeat_age_s != null && live.heartbeat_age_s >= 0 && live.heartbeat_age_s < 120;
  const acct = alpacaAcct.data?.account;

  return (
    <>
      {error && <Notice error>{error}</Notice>}
      {live && (
        <KpiRow>
          <Kpi label="News worker" value={workerReady ? "Running" : "Not ready"} />
          <Kpi label="Execution monitor" value={monitorReady ? live.phase ?? "Unknown" : "Not ready"} />
          <Kpi label="Paper entries" value={live.enabled && live.armed ? "Armed" : "Paused"} />
          <Kpi label="Active order intents" value={monitorReady ? String(live.open_intents ?? "—") : "—"} />
        </KpiRow>
      )}
      <Panel
        title={
          <>
            Alpaca Paper Account
            <Tag tone={alpacaConnected ? "up" : "warn"}>
              {alpacaConnected
                ? `Connected · ${acct?.accountNumber ?? alpacaStatus?.accountNumber ?? "PA"}`
                : "Offline"}
            </Tag>
          </>
        }
        description="Read-only via @alpacahq/alpaca-trade-api"
        action={
          alpacaConnected && (
            <Segmented
              value={String(periodIdx)}
              onChange={(v) => setPeriodIdx(Number(v))}
              options={ALPACA_PERIODS.map(([, , label], idx) => [String(idx), label] as const)}
              label="History period"
            />
          )
        }
      >
        {alpacaAcct.error ? <Notice error>{alpacaAcct.error}</Notice> : alpacaConnected && !acct ? (
          <Notice>Loading Alpaca paper account…</Notice>
        ) : acct ? (
          <div className="space-y-4">
            <KpiRow>
              <Kpi
                label="Paper Equity"
                value={acct.equity != null ? money(acct.equity) : "—"}
                sub={`Last close ${acct.lastEquity != null ? money(acct.lastEquity) : "—"}`}
                tone="accent"
              />
              <Kpi
                label="Day P&L"
                value={acct.dayPnl != null ? signed(acct.dayPnl) : "—"}
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
            </KpiRow>
            {alpacaHist.error && <Notice error>{alpacaHist.error}</Notice>}
            <AlpacaAccountChart
              points={alpacaHist.data?.points ?? []}
              baseValue={alpacaHist.data?.baseValue ?? null}
            />
          </div>
        ) : (
          <Notice>
            {alpacaStatus?.error ??
              "Add paper-only ALPACA_KEY and ALPACA_SECRET in .env to enable live Alpaca portfolio history, snapshots, and WebSocket streaming."}
          </Notice>
        )}
      </Panel>

      {snapshots.length > 0 && (
        <Panel
          title="News Watchlist"
          description={`Alpaca snapshots${streamConnected ? " + real-time WS bars" : ""} · click to chart`}
        >
          <div className="grid grid-cols-[repeat(auto-fit,minmax(200px,1fr))] gap-3">
            {snapshots.map((s) => {
              const bar = latestBars[s.symbol];
              const price = bar?.close ?? s.price;
              const chgPct =
                price != null && s.prevClose ? ((price - s.prevClose) / s.prevClose) * 100 : s.changePct;
              return (
                <button
                  key={s.symbol}
                  type="button"
                  onClick={() => onOpenChart(s.symbol)}
                  className="rounded-lg border bg-muted/40 p-3 text-left transition hover:-translate-y-px hover:border-primary/60"
                >
                  <div className="mb-1 flex items-baseline justify-between">
                    <span className="font-semibold">{s.symbol}</span>
                    <Tag tone={(chgPct ?? 0) >= 0 ? "up" : "down"}>{chgPct != null ? pct(chgPct, 2) : "—"}</Tag>
                  </div>
                  <div className="num text-lg font-semibold">{usd(price)}</div>
                  <div className="mt-2 grid grid-cols-2 gap-x-2 gap-y-0.5 border-t pt-2 font-mono text-[11px] text-muted-foreground">
                    <span>VWAP {usd(s.vwap)}</span>
                    <span>Vol {s.volume != null ? s.volume.toLocaleString() : "—"}</span>
                    <span>Bid {usd(s.bid)}</span>
                    <span>Ask {usd(s.ask)}</span>
                  </div>
                </button>
              );
            })}
          </div>
        </Panel>
      )}

      {alpacaConnected && alpacaAcct.data && (
        <Grid cols={2}>
          <Panel title={`Paper Positions (${alpacaAcct.data.positions.length})`}>
            {alpacaAcct.data.positions.length === 0 ? (
              <p className="text-xs text-muted-foreground">No open paper positions.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Side</TableHead>
                    <TableHead className={R}>Qty</TableHead>
                    <TableHead className={R}>Avg Entry</TableHead>
                    <TableHead className={R}>Current</TableHead>
                    <TableHead className={R}>Unrealized</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {alpacaAcct.data.positions.map((p) => (
                    <TableRow key={p.symbol}>
                      <TableCell>
                        <LinkButton onClick={() => onOpenChart(p.symbol)}>{p.symbol}</LinkButton>
                      </TableCell>
                      <TableCell>
                        <Tag tone={p.side}>{p.side}</Tag>
                      </TableCell>
                      <TableCell className={R}>{p.qty}</TableCell>
                      <TableCell className={R}>{p.avgEntryPrice.toFixed(2)}</TableCell>
                      <TableCell className={R}>{p.currentPrice.toFixed(2)}</TableCell>
                      <TableCell className={`${R} ${upDown(p.unrealizedPl)}`}>
                        {signed(p.unrealizedPl)} ({pct(p.unrealizedPlpc, 2)})
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Panel>

          <Panel title={`Recent Paper Orders (${alpacaAcct.data.orders.length})`} description="Read-only view">
            {alpacaAcct.data.orders.length === 0 ? (
              <p className="text-xs text-muted-foreground">No recent paper orders.</p>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Symbol</TableHead>
                    <TableHead>Side</TableHead>
                    <TableHead>Type</TableHead>
                    <TableHead className={R}>Qty</TableHead>
                    <TableHead className={R}>Fill</TableHead>
                    <TableHead>Status</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {alpacaAcct.data.orders.slice(0, 15).map((o) => (
                    <TableRow key={o.id}>
                      <TableCell>{o.symbol}</TableCell>
                      <TableCell>
                        <Tag tone={o.side}>{o.side}</Tag>
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">{o.type}</TableCell>
                      <TableCell className={R}>{o.qty}</TableCell>
                      <TableCell className={R}>{usd(o.filledAvgPrice)}</TableCell>
                      <TableCell>
                        <Tag>{o.status}</Tag>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            )}
          </Panel>
        </Grid>
      )}

    </>
  );
}
