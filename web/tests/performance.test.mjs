import assert from "node:assert/strict";
import test from "node:test";
import { createAlpacaBridge } from "../server/alpaca-bridge.ts";
import { previousClose } from "../src/indicators.ts";
import { openPositionPerformance, pnlCalendar, portfolioPerformance, returnBins, tradeQuality } from "../src/performanceData.ts";

function point(time, equity, profitLoss, profitLossPct) {
  return { time, equity, profitLoss, profitLossPct };
}

test("portfolio performance preserves reported P&L and measures drawdown from the running equity peak", () => {
  const latest = point(6, 121, 14, 14);
  const result = portfolioPerformance([
    point(1, 0, 0, 0),
    point(2, 100, 0, 0),
    point(3, 80, -20, -20),
    point(4, 125, 10, 10),
    point(5, 100, 5, 5),
    latest,
  ]);

  assert.equal(result.latest, latest);
  assert.deepEqual(result.changes.map((p) => p.value), [null, -20, 30, -5, 9]);
  assert.deepEqual(result.returns.map((p) => p.value), [0, -20, 10, 5, 14]);
  assert.deepEqual(result.drawdown.map((p) => p.value), [0, -20, 0, -20, -3.2]);
  assert.equal(result.maxDrawdown, -20);
});

test("unfunded history is empty and missing P&L does not create a change bar", () => {
  assert.equal(portfolioPerformance([point(1, 0, 0, 0)]).latest, undefined);
  assert.equal(portfolioPerformance([]).maxDrawdown, null);

  const result = portfolioPerformance([
    point(1, 100, 0, 0),
    point(2, 90, null, null),
    point(3, 110, 10, 10),
    point(4, 110, 10, 10),
  ]);
  assert.deepEqual(result.changes.map((p) => p.value), [null, null, null, 0]);
  assert.deepEqual(result.pnl.map((p) => p.value), [0, null, 10, 10]);
  assert.equal(result.maxDrawdown, -10);
});

test("an omitted Alpaca equity interval stays a gap in P&L changes", async (t) => {
  process.env.ALPACA_KEY = "fixture-paper-key";
  process.env.ALPACA_SECRET = "fixture-paper-secret";
  t.mock.method(globalThis, "fetch", async (input) => {
    const url = new URL(input);
    assert.equal(url.hostname, "paper-api.alpaca.markets");
    assert.equal(url.pathname, "/v2/account/portfolio/history");
    assert.equal(url.searchParams.get("pnl_reset"), "no_reset");
    return Response.json({
      timestamp: [1731940200, 1731940500, 1731940800, 1731941100, 1731941400],
      equity: [100, null, 90, 106, null],
      profit_loss: [0, null, -10, 6, null],
      profit_loss_pct: [0, null, -0.10, 0.06, null],
      base_value: 100,
      timeframe: "5Min",
    });
  });
  const bridge = createAlpacaBridge();
  t.after(() => bridge.close());

  let status;
  let body;
  await bridge.handle(
    { method: "GET", url: "/api/alpaca/portfolio-history?period=1D&timeframe=5Min&pnl_reset=no_reset" },
    {
      writeHead(code) { status = code; },
      end(raw) { body = JSON.parse(raw); },
    },
  );
  assert.equal(status, 200, JSON.stringify(body));

  const result = portfolioPerformance(body.points);
  assert.equal(result.latest.equity, 106);
  assert.deepEqual(result.changes.map((p) => p.value), [null, null, null, 16]);
  assert.deepEqual(result.drawdown.map((p) => p.value), [0, null, -10, 0]);
});

test("open-position P&L is independent of long and short market exposure", () => {
  const result = openPositionPerformance([
    { symbol: "SPY", side: "long", marketValue: 500, unrealizedPl: -20, unrealizedPlpc: -4 },
    { symbol: "QQQ", side: "short", marketValue: -1200, unrealizedPl: 50, unrealizedPlpc: 4 },
    { symbol: "VGT", side: "short", marketValue: 200, unrealizedPl: -5, unrealizedPlpc: -2.5 },
  ]);

  assert.equal(result.pnl, 25);
  assert.equal(result.long, 500);
  assert.equal(result.short, 1400);
  assert.deepEqual(result.rows.map((p) => [p.symbol, p.pnl, p.exposure]), [
    ["SPY", -20, 500],
    ["QQQ", 50, -1200],
    ["VGT", -5, -200],
  ]);
  assert.deepEqual(openPositionPerformance([]), { rows: [], pnl: 0, long: 0, short: 0 });
});

test("trade quality, return bins and the P&L calendar", () => {
  const q = tradeQuality({ closed: 4, wins: 1, losses: 3, gross_win: 30, gross_loss: -15 });
  assert.deepEqual(q, { winRate: 25, avgWin: 30, avgLoss: -5, expectancy: 3.75, profitFactor: 2 });
  assert.equal(tradeQuality({ closed: 0, wins: 0, losses: 0, gross_win: 0, gross_loss: 0 }).profitFactor, null);
  // A break-even trade is not a loss: one $20 loss and one flat trade average a $20 loss.
  assert.equal(tradeQuality({ closed: 2, wins: 0, losses: 1, gross_win: 0, gross_loss: -20 }).avgLoss, -20);

  const bins = returnBins([-2, 0.5, 2], 4);
  assert.deepEqual(bins.map((b) => b.count), [1, 0, 1, 1]);
  assert.deepEqual(returnBins([]), []);
  assert.equal(returnBins([0, 0], 4).reduce((sum, b) => sum + b.count, 0), 2);

  const at = (date) => Date.parse(`${date}T20:00:00Z`) / 1000;
  const weeks = pnlCalendar([
    point(at("2026-11-19"), 100, 0, 0),
    point(at("2026-11-20"), 110, 10, 10), // Friday
    point(at("2026-11-23"), 99, -1, -1), // Monday
  ]);
  assert.deepEqual(weeks.map((w) => w.monday), ["2026-11-16", "2026-11-23"]);
  assert.deepEqual(weeks[0].days[4], { date: "2026-11-20", pnl: 10, pct: 10 });
  assert.equal(weeks[1].days[0].pnl, -11);
  const gapped = pnlCalendar([
    point(at("2026-11-23"), 100, 0, 0),
    point(at("2026-11-24"), null, null, null),
    point(at("2026-11-25"), 105, 5, 5),
    point(at("2026-11-26"), 107, 7, 7),
  ]);
  assert.deepEqual(gapped[0].days.map((d) => d?.pnl ?? null), [null, null, null, 2, null]);
});

test("the previous-close line follows the session the chart ends on", () => {
  const at = (iso) => Date.parse(iso) / 1000; // chart times are New York wall-clock seconds
  const premarket = { prevClose: 100, dailyClose: 105, dailyDay: "2026-11-23" }; // Tuesday before the open
  assert.equal(previousClose(premarket, at("2026-11-24T08:15:00Z")), 105);
  const regular = { prevClose: 105, dailyClose: 107, dailyDay: "2026-11-24" };
  assert.equal(previousClose(regular, at("2026-11-24T10:00:00Z")), 105);
  assert.equal(previousClose(premarket, at("2026-11-23T15:55:00Z")), 100); // chart still ends on Monday
  assert.equal(previousClose(undefined, 0), null);
});
