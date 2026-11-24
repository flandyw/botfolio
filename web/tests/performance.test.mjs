import assert from "node:assert/strict";
import test from "node:test";
import { openPositionPerformance, portfolioPerformance } from "../src/performanceData.ts";

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
