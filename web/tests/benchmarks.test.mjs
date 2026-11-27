import assert from "node:assert/strict";
import test from "node:test";
import { createAlpacaBridge } from "../server/alpaca-bridge.ts";
import { savingsBenchmark } from "../src/benchmarkData.ts";

const seconds = (iso) => Date.parse(iso) / 1000;

void test("savings starts at $10,000 and earns the configured annual yield over 365 days", () => {
  const series = savingsBenchmark([
    seconds("2024-11-15T20:00:00Z"),
    seconds("2024-11-16T20:00:00Z"),
    seconds("2025-11-16T20:00:00Z"),
  ], "2024-11-16", 5.75);
  assert.equal(series.length, 2);
  assert.deepEqual(series.map(({ value }) => value.toFixed(2)), ["10000.00", "10575.00"]);
});

void test("ETF benchmarks retain the purchase basis and value only completed intraday bars", async (t) => {
  process.env.ALPACA_KEY = "fixture-paper-key";
  process.env.ALPACA_SECRET = "fixture-paper-secret";
  t.mock.method(globalThis, "fetch", async (input) => {
    const url = new URL(input);
    if (url.pathname === "/v2/account/portfolio/history") {
      return Response.json({
        timestamp: ["14:30", "14:35", "14:40"].map((time) => seconds(`2024-11-18T${time}:00Z`)),
        equity: [10_000, 10_000, 10_000],
      });
    }
    assert.equal(url.pathname, "/v2/stocks/bars");
    assert.equal(url.searchParams.get("adjustment"), "all");
    const daily = url.searchParams.get("timeframe") === "1Day";
    const bar = (time, open, close) => ({ t: `2024-11-18T${time}:00Z`, o: open, h: open, l: open, c: close, v: 1 });
    return Response.json({
      bars: daily ? {
        QQQ: [bar("05:00", 100, 120)],
        VGT: [bar("05:00", 200, 210)],
      } : {
        QQQ: [bar("14:30", 100, 105), bar("14:35", 105, 120)],
        VGT: [bar("14:30", 200, 180), bar("14:35", 180, 210)],
      },
    });
  });
  const bridge = createAlpacaBridge();
  t.after(() => bridge.close());
  let status, body;
  await bridge.handle({
    method: "GET",
    url: "/api/alpaca/portfolio-history?period=1D&timeframe=5Min&purchase_date=2024-11-16",
  }, {
    writeHead(code) { status = code; },
    end(raw) { body = JSON.parse(raw); },
  });
  assert.equal(status, 200, JSON.stringify(body));
  assert.deepEqual(body.benchmarks.QQQ, [
    { time: body.points[0].time, value: 10_000 },
    { time: body.points[1].time, value: 10_500 },
    { time: body.points[2].time, value: 12_000 },
  ]);
  assert.deepEqual(body.benchmarks.VGT.map(({ value }) => value), [10_000, 9_000, 10_500]);
});
