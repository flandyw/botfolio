import { useEffect, useMemo, useState } from "react";
import type { AlpacaSnapshotsResponse, AlpacaStatus, Config, Live } from "./api";
import { Moon, Sun } from "lucide-react";
import { cn } from "cn";
import { Notice } from "@/components/dash";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { pct } from "./format";
import { useAlpacaStream } from "./useAlpacaStream";
import { useApi } from "./useApi";
import { ChartView, LiveView } from "./views";

const TABS = [
  ["live", "Paper account"],
  ["chart", "Market chart"],
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
    document.documentElement.classList.toggle("dark", theme === "dark");
    try {
      localStorage.setItem("botfolio-theme", theme);
    } catch {
      // ignore storage errors
    }
  }, [theme]);

  const cfg = useApi<Config>("config", 30_000);
  const live = useApi<Live>("live", 5_000);
  const alpacaStatus = useApi<AlpacaStatus>("alpaca/status", 60_000);

  const symbols = useMemo(() => cfg.data?.symbols ?? ["QQQ", "SPY", "VGT", "XLK", "SOXX", "VUG"], [cfg.data]);
  const symbolsQuery = symbols.join(",");
  const alpacaConnected = Boolean(alpacaStatus.data?.connected);

  const snapshots = useApi<AlpacaSnapshotsResponse>(
    alpacaConnected ? `alpaca/snapshots?symbols=${encodeURIComponent(symbolsQuery)}` : null,
    30_000,
  );
  const stream = useAlpacaStream(symbols, alpacaConnected);

  const [tab, setTab] = useState<Tab>("live");
  const [symbol, setSymbol] = useState("");

  const openChartSymbol = (sym: string) => {
    setSymbol(sym);
    setTab("chart");
  };

  const botAlive =
    live.data?.heartbeat_age_s != null
      ? live.data.heartbeat_age_s >= 0 && live.data.heartbeat_age_s < 120
      : undefined;
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
      return { symbol: sym, price, changePct };
    });
  }, [symbols, snapshots.data, stream.latestBars]);

  const statusDot = (tone: "ok" | "bad" | "warn" | "idle") =>
    cn(
      "size-2 rounded-full",
      tone === "ok" && "bg-up shadow-[0_0_0_3px] shadow-up/20",
      tone === "bad" && "bg-down shadow-[0_0_0_3px] shadow-down/20",
      tone === "warn" && "bg-warn shadow-[0_0_0_3px] shadow-warn/20",
      tone === "idle" && "bg-muted-foreground",
    );
  const feed = (alpacaStatus.data?.feed ?? "iex").toUpperCase();

  return (
    <div className="min-h-svh">
      <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur-md">
        <div className="mx-auto flex max-w-[1520px] flex-wrap items-center gap-3 px-4 py-2.5 md:px-6">
          <div className="flex items-center gap-2 font-semibold tracking-tight">
            <span className="grid size-7 place-items-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
              B
            </span>
            Botfolio
            <span className="rounded bg-primary/15 px-1.5 py-0.5 text-[10px] font-semibold tracking-widest text-primary uppercase">
              Paper
            </span>
          </div>

          <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)} className="order-last w-full sm:order-none sm:w-auto">
            <TabsList className="w-full sm:w-auto">
              {TABS.map(([id, label]) => (
                <TabsTrigger key={id} value={id} className="px-3">
                  {label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          <div className="flex-1" />

          <Button
            variant="outline"
            size="sm"
            className="rounded-full"
            onClick={() => setTab("live")}
            title={
              alpacaConnected
                ? `Connected to Alpaca Paper (${alpacaStatus.data?.accountNumber ?? "PA"})`
                : (alpacaStatus.data?.error ?? "Configure ALPACA_KEY & ALPACA_SECRET in .env")
            }
          >
            <span className={statusDot(alpacaConnected ? (marketOpen ? "ok" : "idle") : "warn")} />
            {marketOpen == null ? "Market status unknown" : marketOpen ? "Market open" : "Market closed"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="rounded-full"
            onClick={() => setTab("live")}
            title="News worker status"
          >
            <span className={statusDot(botAlive === true ? "ok" : botAlive === false ? "bad" : "idle")} />
            {botAlive === true ? "Bot live" : botAlive === false ? "Bot down" : "Bot idle"}
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
            aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}
          >
            {theme === "dark" ? <Sun /> : <Moon />}
          </Button>
        </div>

        <div className="border-t bg-card/40">
          <div className="mx-auto flex max-w-[1520px] items-center gap-2 overflow-x-auto px-4 py-1.5 [scrollbar-width:none] md:px-6">
            <span
              className="flex shrink-0 items-center gap-1.5 pr-2 font-mono text-[10.5px] font-medium tracking-wide text-muted-foreground"
              title="Powered by @alpacahq/alpaca-trade-api (paper, read-only)"
            >
              <span className={statusDot(alpacaConnected ? (stream.connected ? "ok" : "warn") : "idle")} />
              {alpacaConnected ? `${stream.connected ? "WS" : "REST"} · ${feed}` : "OFFLINE"}
            </span>
            {tickerItems.map((item) => (
              <button
                key={item.symbol}
                type="button"
                className="flex shrink-0 items-center gap-2 rounded-md px-2 py-1 text-xs transition-colors hover:bg-muted"
                onClick={() => openChartSymbol(item.symbol)}
                title={`Open ${item.symbol} chart`}
              >
                <span className="font-semibold">{item.symbol}</span>
                <span className="num">{item.price != null ? `$${item.price.toFixed(2)}` : "—"}</span>
                {item.changePct != null && (
                  <span className={cn("num text-[11px]", item.changePct >= 0 ? "text-up" : "text-down")}>
                    {pct(item.changePct, 2)}
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-[1520px] gap-4 px-4 py-5 md:px-6">
        {cfg.error && <Notice error>{cfg.error}</Notice>}
        {alpacaStatus.error && <Notice error>{alpacaStatus.error}</Notice>}
        {tab === "chart" && (
          <ChartView
            symbols={symbols.includes(symbol) || !symbol ? symbols : [symbol, ...symbols]}
            symbol={symbol}
            onSymbol={setSymbol}
            alpacaConnected={alpacaConnected}
            snapshots={snapshots.data?.snapshots ?? []}
            latestBars={stream.latestBars}
            streamConnected={stream.connected}
          />
        )}
        {tab === "live" && (
          <LiveView
            live={live.data ?? null}
            error={live.error ?? null}
            alpacaStatus={alpacaStatus.data ?? null}
            snapshots={snapshots.data?.snapshots ?? []}
            latestBars={stream.latestBars}
            streamConnected={stream.connected}
            onOpenChart={openChartSymbol}
          />
        )}
      </main>

      <footer className="mx-auto flex max-w-[1520px] flex-wrap justify-between gap-2 border-t px-4 py-4 text-xs text-muted-foreground md:px-6">
        <span>
          Read-only paper dashboard · data via <code className="font-mono">@alpacahq/alpaca-trade-api</code>
        </span>
        <span>
          {cfg.data
            ? `Universe: ${cfg.data.symbols.join(", ")} · Feed: ${cfg.data.feed.toUpperCase()}`
            : ""}
        </span>
      </footer>
    </div>
  );
}
