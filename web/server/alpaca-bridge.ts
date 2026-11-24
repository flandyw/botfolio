/**
 * Read-only, paper-only Alpaca Market Data & Paper Account bridge powered by
 * `@alpacahq/alpaca-trade-api`.
 *
 * Security & safety invariants:
 * - Credentials stay strictly server-side (never sent to the browser).
 * - Always connects with `paper: true` and refuses live-trading base URLs.
 * - Verifies the account number starts with "PA" (paper account) before serving account data.
 * - Exposes read-only endpoints only — no order placement or account mutations.
 */
import * as fs from "node:fs";
import type { IncomingMessage, ServerResponse } from "node:http";
import * as path from "node:path";
import {
  Alpaca,
  ApiError,
  TimeFrame,
  TimeFrameUnit,
  timeFrame,
  type Bar,
} from "@alpacahq/alpaca-trade-api";

export type AlpacaBridgeConfig = {
  envFile?: string;
  feed?: "iex" | "sip";
  defaultSymbols?: string[];
};

const DEFAULT_SYMBOLS = ["QQQ", "SPY", "VGT", "XLK", "SOXX", "VUG"];
const LIVE_URL_RE = /api\.alpaca\.markets/i;

const NY_PARTS = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** Encode a timestamp as New York wall-clock seconds in UTC (matches webapi.py's chart_time). */
export function nyChartTime(ts: Date | string | number): number {
  const d = ts instanceof Date ? ts : new Date(ts);
  if (Number.isNaN(d.getTime())) return 0;
  const parts: Record<string, number> = {};
  for (const p of NY_PARTS.formatToParts(d)) {
    if (p.type !== "literal") parts[p.type] = Number(p.value);
  }
  const hour = parts.hour === 24 ? 0 : parts.hour;
  return Math.floor(
    Date.UTC(parts.year, parts.month - 1, parts.day, hour, parts.minute, parts.second) / 1000,
  );
}

/** Load key=value pairs from a .env file without mutating existing process.env entries. */
function loadDotEnv(filePath: string): Record<string, string> {
  const out: Record<string, string> = {};
  try {
    if (!fs.existsSync(filePath)) return out;
    const raw = fs.readFileSync(filePath, "utf8");
    for (const line of raw.split(/\r?\n/)) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#")) continue;
      const eq = trimmed.indexOf("=");
      if (eq <= 0) continue;
      const key = trimmed.slice(0, eq).trim();
      let val = trimmed.slice(eq + 1).trim();
      if (
        (val.startsWith('"') && val.endsWith('"')) ||
        (val.startsWith("'") && val.endsWith("'"))
      ) {
        val = val.slice(1, -1);
      }
      out[key] = val;
    }
  } catch {
    // Ignore unreadable .env files; credentials may come from environment variables.
  }
  return out;
}

function readEnv(dotEnv: Record<string, string>, ...keys: string[]): string | undefined {
  for (const k of keys) {
    const v = process.env[k] ?? dotEnv[k];
    if (v && v.trim()) return v.trim();
  }
  return undefined;
}

function mapTimeframe(tf: string) {
  switch (tf) {
    case "1":
    case "1m":
      return TimeFrame.Minute;
    case "5":
    case "5m":
      return timeFrame(5, TimeFrameUnit.Minute);
    case "15":
    case "15m":
      return timeFrame(15, TimeFrameUnit.Minute);
    case "60":
    case "1h":
      return TimeFrame.Hour;
    case "D":
    case "1D":
      return TimeFrame.Day;
    default:
      return timeFrame(5, TimeFrameUnit.Minute);
  }
}

function sendJson(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  res.end(JSON.stringify(body));
}

function num(v: unknown): number | null {
  if (v == null || v === "") return null;
  const n = Number(v);
  return Number.isFinite(n) ? n : null;
}

type SseClient = {
  res: ServerResponse;
  symbols: Set<string>;
};

export type AlpacaBridge = {
  handle: (req: IncomingMessage, res: ServerResponse) => Promise<boolean>;
  close: () => void;
};

export function createAlpacaBridge(cfg: AlpacaBridgeConfig = {}): AlpacaBridge {
  const envPath =
    cfg.envFile ??
    process.env.BOTFOLIO_ENV_FILE ??
    path.resolve(process.cwd(), "..", ".env");
  const dotEnv = loadDotEnv(envPath);

  const baseUrl = readEnv(dotEnv, "APCA_API_BASE_URL", "ALPACA_BASE_URL", "ALPACA_TRADING_URL");
  if (baseUrl && LIVE_URL_RE.test(baseUrl)) {
    throw new Error(
      `Refusing to start Alpaca bridge against live trading URL (${baseUrl}). Paper trading only.`,
    );
  }

  const keyId = readEnv(dotEnv, "ALPACA_KEY", "APCA_API_KEY_ID");
  const secret = readEnv(dotEnv, "ALPACA_SECRET", "APCA_API_SECRET_KEY");
  const feed: "iex" | "sip" =
    cfg.feed ??
    (readEnv(dotEnv, "BOTFOLIO_DATA_FEED")?.toLowerCase() === "sip" ? "sip" : "iex");
  const defaultSymbols = cfg.defaultSymbols ?? DEFAULT_SYMBOLS;

  const alpaca =
    keyId && secret
      ? new Alpaca({
          keyId,
          secret,
          paper: true,
        })
      : null;

  // Lazy, reference-counted market-data WebSocket fanned out to SSE clients.
  const clients = new Set<SseClient>();
  const subscribed = new Set<string>();
  let stream: ReturnType<Alpaca["marketData"]["stockStream"]> | null = null;
  let streamConnected = false;

  function broadcast(event: string, payload: unknown, symbol?: string): void {
    const frame = `event: ${event}\ndata: ${JSON.stringify(payload)}\n\n`;
    for (const c of clients) {
      if (symbol && c.symbols.size > 0 && !c.symbols.has(symbol)) continue;
      try {
        c.res.write(frame);
      } catch {
        clients.delete(c);
      }
    }
  }

  function syncStreamSubscriptions(): void {
    if (!alpaca) return;
    const wanted = new Set<string>();
    for (const c of clients) {
      for (const s of c.symbols) wanted.add(s);
    }

    if (wanted.size === 0) {
      if (stream) {
        try {
          stream.disconnect();
        } catch {
          // ignore disconnect errors during teardown
        }
        stream = null;
        streamConnected = false;
        subscribed.clear();
      }
      return;
    }

    if (!stream) {
      const s = alpaca.marketData.stockStream({ feed });
      s.onConnect(() => {
        streamConnected = true;
        const list = [...subscribed];
        if (list.length) s.subscribeForBars(list);
        broadcast("status", { connected: true, feed, symbols: list });
      });
      s.onBar((bar: Bar) => {
        if (!bar.symbol) return;
        const sym = bar.symbol.toUpperCase();
        broadcast(
          "bar",
          {
            symbol: sym,
            time: nyChartTime(bar.timestamp),
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
            volume: bar.volume,
            vwap: bar.vwap ?? null,
          },
          sym,
        );
      });
      s.onError((msg) => {
        broadcast("stream_error", { message: String(msg) });
      });
      s.onDisconnect(() => {
        streamConnected = false;
        broadcast("status", { connected: false, feed });
      });
      stream = s;
      for (const sym of wanted) subscribed.add(sym);
      s.connect();
      return;
    }

    const toAdd = [...wanted].filter((sym) => !subscribed.has(sym));
    const toRemove = [...subscribed].filter((sym) => !wanted.has(sym));
    for (const sym of toAdd) subscribed.add(sym);
    for (const sym of toRemove) subscribed.delete(sym);
    if (streamConnected) {
      if (toAdd.length) stream.subscribeForBars(toAdd);
      if (toRemove.length) stream.unsubscribeFromBars(toRemove);
    }
  }

  async function requirePaperClient(): Promise<Alpaca> {
    if (!alpaca) {
      throw new Error(
        "Alpaca credentials not configured (set ALPACA_KEY and ALPACA_SECRET in .env)",
      );
    }
    return alpaca;
  }

  async function handle(req: IncomingMessage, res: ServerResponse): Promise<boolean> {
    const rawUrl = req.url ?? "/";
    if (!rawUrl.startsWith("/api/alpaca")) return false;
    if (req.method !== "GET") {
      sendJson(res, 405, { error: "Read-only Alpaca bridge: GET only" });
      return true;
    }

    const url = new URL(rawUrl, "http://127.0.0.1");
    const route = url.pathname.replace(/^\/api\/alpaca\/?/, "");

    try {
      if (route === "status") {
        if (!alpaca) {
          sendJson(res, 200, {
            configured: false,
            connected: false,
            paper: true,
            feed,
            sdk: "@alpacahq/alpaca-trade-api",
            streamClients: clients.size,
            clock: null,
            accountNumber: null,
            error: "No Alpaca Paper credentials configured in .env",
          });
          return true;
        }
        const [check, clock] = await Promise.all([
          alpaca.trading.validateConnection(),
          alpaca.trading.clock.legacyClock().catch(() => null),
        ]);
        const acctNum = check.ok ? (check.account.accountNumber ?? null) : null;
        const isPaperAcct = acctNum ? acctNum.startsWith("PA") : true;
        sendJson(res, 200, {
          configured: true,
          connected: check.ok && isPaperAcct,
          paper: isPaperAcct,
          feed,
          sdk: "@alpacahq/alpaca-trade-api",
          streamClients: clients.size,
          streamConnected,
          accountNumber: acctNum,
          clock: clock
            ? {
                isOpen: Boolean(clock.isOpen),
                timestamp: clock.timestamp ? new Date(clock.timestamp).toISOString() : null,
                nextOpen: clock.nextOpen ? new Date(clock.nextOpen).toISOString() : null,
                nextClose: clock.nextClose ? new Date(clock.nextClose).toISOString() : null,
              }
            : null,
          error: !check.ok
            ? check.message
            : !isPaperAcct
              ? "Connected account is not a paper account (expected PA*)"
              : null,
        });
        return true;
      }

      if (route === "snapshots") {
        const client = await requirePaperClient();
        const symbolsParam = (url.searchParams.get("symbols") ?? defaultSymbols.join(","))
          .split(",")
          .map((s) => s.trim().toUpperCase())
          .filter(Boolean);
        const raw = await client.marketData.stocks.stockSnapshots({
          symbols: symbolsParam.join(","),
          feed,
        });
        const snapshots = symbolsParam
          .map((symbol) => {
            const snap = raw[symbol];
            if (!snap) return null;
            const last =
              num(snap.latestTrade?.p) ??
              num(snap.minuteBar?.c) ??
              num(snap.dailyBar?.c) ??
              num(snap.prevDailyBar?.c);
            const prevClose = num(snap.prevDailyBar?.c);
            const change = last != null && prevClose != null ? last - prevClose : null;
            const changePct =
              change != null && prevClose && prevClose > 0 ? (change / prevClose) * 100 : null;
            const bid = num(snap.latestQuote?.bp);
            const ask = num(snap.latestQuote?.ap);
            const spread = bid != null && ask != null && ask >= bid ? ask - bid : null;
            return {
              symbol,
              price: last,
              prevClose,
              change,
              changePct,
              open: num(snap.dailyBar?.o),
              high: num(snap.dailyBar?.h),
              low: num(snap.dailyBar?.l),
              volume: num(snap.dailyBar?.v),
              vwap: num(snap.dailyBar?.vw),
              bid,
              ask,
              spread,
              updatedAt: snap.latestTrade?.t
                ? new Date(snap.latestTrade.t).toISOString()
                : snap.minuteBar?.t
                  ? new Date(snap.minuteBar.t).toISOString()
                  : null,
            };
          })
          .filter(Boolean);
        sendJson(res, 200, { feed, snapshots });
        return true;
      }

      if (route === "bars") {
        const client = await requirePaperClient();
        const symbol = (url.searchParams.get("symbol") ?? defaultSymbols[0]).toUpperCase();
        const tf = url.searchParams.get("tf") ?? "5";
        const days = Math.min(Math.max(Number(url.searchParams.get("days") ?? 30), 1), 730);
        const start = new Date(Date.now() - days * 24 * 3600 * 1000);
        const bars = await client.marketData.getStockBarsFor(symbol, {
          timeframe: mapTimeframe(tf),
          start,
          feed,
          adjustment: "split",
        });
        const records = bars
          .map((b) => ({
            time: nyChartTime(b.timestamp),
            open: b.open,
            high: b.high,
            low: b.low,
            close: b.close,
            volume: b.volume,
          }))
          .filter((b) => b.time > 0);
        // Deduplicate ascending timestamps for lightweight-charts
        const deduped: typeof records = [];
        let prevTime = -1;
        for (const r of records) {
          if (r.time > prevTime) {
            deduped.push(r);
            prevTime = r.time;
          }
        }
        sendJson(res, 200, deduped);
        return true;
      }

      if (route === "account") {
        const client = await requirePaperClient();
        const account = await client.trading.account.getAccount();
        const acctNum = account.accountNumber ?? "";
        if (!acctNum.startsWith("PA")) {
          sendJson(res, 403, { error: "Refused non-paper Alpaca account" });
          return true;
        }
        const [positions, orders, clock] = await Promise.all([
          client.trading.positions.getAllOpenPositions(),
          client.trading.orders.getAllOrders({ status: "all", limit: 50, direction: "desc" }),
          client.trading.clock.legacyClock().catch(() => null),
        ]);
        const equity = num(account.equity);
        const lastEquity = num(account.lastEquity);
        const dayPnl = equity != null && lastEquity != null ? equity - lastEquity : null;
        const dayPnlPct =
          dayPnl != null && lastEquity && lastEquity > 0 ? (dayPnl / lastEquity) * 100 : null;
        sendJson(res, 200, {
          account: {
            accountNumber: acctNum,
            status: account.status ?? "ACTIVE",
            equity,
            lastEquity,
            cash: num(account.cash),
            buyingPower: num(account.buyingPower),
            portfolioValue: num(account.portfolioValue),
            longMarketValue: num(account.longMarketValue),
            shortMarketValue: num(account.shortMarketValue),
            dayPnl,
            dayPnlPct,
            daytradeCount: num(account.daytradeCount) ?? 0,
            patternDayTrader: Boolean(account.patternDayTrader),
          },
          clock: clock
            ? {
                isOpen: Boolean(clock.isOpen),
                timestamp: clock.timestamp ? new Date(clock.timestamp).toISOString() : null,
                nextOpen: clock.nextOpen ? new Date(clock.nextOpen).toISOString() : null,
                nextClose: clock.nextClose ? new Date(clock.nextClose).toISOString() : null,
              }
            : null,
          positions: positions.map((p) => ({
            symbol: p.symbol,
            side: p.side,
            qty: num(p.qty) ?? 0,
            avgEntryPrice: num(p.avgEntryPrice) ?? 0,
            currentPrice: num(p.currentPrice) ?? 0,
            marketValue: num(p.marketValue) ?? 0,
            unrealizedPl: num(p.unrealizedPl) ?? 0,
            unrealizedPlpc: (num(p.unrealizedPlpc) ?? 0) * 100,
            changeToday: (num(p.changeToday) ?? 0) * 100,
          })),
          orders: orders.slice(0, 30).map((o) => ({
            id: o.id,
            symbol: o.symbol,
            side: o.side,
            type: o.type,
            qty: num(o.qty ?? o.filledQty) ?? 0,
            filledAvgPrice: num(o.filledAvgPrice),
            status: o.status,
            submittedAt: o.submittedAt ? new Date(o.submittedAt).toISOString() : null,
            filledAt: o.filledAt ? new Date(o.filledAt).toISOString() : null,
          })),
        });
        return true;
      }

      if (route === "portfolio-history") {
        const client = await requirePaperClient();
        const period = url.searchParams.get("period") ?? "1M";
        const timeframe = url.searchParams.get("timeframe") ?? (period === "1D" ? "5Min" : "1D");
        const continuousPnl = url.searchParams.get("pnl_reset") === "no_reset";
        const hist = await client.trading.portfolioHistory.getAccountPortfolioHistory({
          period,
          timeframe,
          pnlReset: continuousPnl ? "no_reset" : undefined,
        });
        const timestamps = hist.timestamp ?? [];
        const equities = hist.equity ?? [];
        const pl = hist.profitLoss ?? [];
        const plPct = hist.profitLossPct ?? [];
        const points = timestamps
          .map((ts, i) => {
            const returnFraction = num(plPct[i]);
            return {
              time: nyChartTime(ts * 1000),
              equity: num(equities[i]),
              profitLoss: num(pl[i]),
              profitLossPct: returnFraction == null ? null : returnFraction * 100,
            };
          })
          .filter((p) => p.time > 0)
          .sort((a, b) => a.time - b.time)
          .filter((p, i, list) => p.time !== list[i + 1]?.time);
        sendJson(res, 200, {
          period,
          timeframe,
          baseValue: num(hist.baseValue),
          points,
        });
        return true;
      }

      if (route === "stream") {
        if (!alpaca) {
          sendJson(res, 503, { error: "Alpaca credentials not configured for live streaming" });
          return true;
        }
        const symbols = new Set(
          (url.searchParams.get("symbols") ?? defaultSymbols.join(","))
            .split(",")
            .map((s) => s.trim().toUpperCase())
            .filter(Boolean),
        );
        res.writeHead(200, {
          "Content-Type": "text/event-stream",
          "Cache-Control": "no-cache, no-transform",
          Connection: "keep-alive",
        });
        res.write(
          `event: status\ndata: ${JSON.stringify({ connected: streamConnected, feed, symbols: [...symbols] })}\n\n`,
        );
        const clientEntry: SseClient = { res, symbols };
        clients.add(clientEntry);
        syncStreamSubscriptions();
        const keepalive = setInterval(() => {
          try {
            res.write(": ping\n\n");
          } catch {
            clearInterval(keepalive);
          }
        }, 25_000);
        req.on("close", () => {
          clearInterval(keepalive);
          clients.delete(clientEntry);
          syncStreamSubscriptions();
        });
        return true;
      }

      sendJson(res, 404, { error: `Unknown Alpaca bridge route: /api/alpaca/${route}` });
      return true;
    } catch (err) {
      if (err instanceof ApiError) {
        sendJson(res, err.status || 502, {
          error: err.message,
          code: err.code,
          requestId: err.requestId,
        });
      } else {
        sendJson(res, alpaca ? 500 : 503, {
          error: err instanceof Error ? err.message : String(err),
        });
      }
      return true;
    }
  }

  function close(): void {
    for (const c of clients) {
      try {
        c.res.end();
      } catch {
        // ignore
      }
    }
    clients.clear();
    if (stream) {
      try {
        stream.disconnect();
      } catch {
        // ignore
      }
      stream = null;
    }
  }

  return { handle, close };
}
