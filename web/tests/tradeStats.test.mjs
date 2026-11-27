import assert from "node:assert/strict";
import test from "node:test";
import { duration, tradeStats } from "../src/tradeStats.ts";

const bar = (time, high, low) => ({ time, open: low, high, low, close: high, volume: 1 });

test("long trade stats measure R, excursions and capture over the holding bars only", () => {
  const row = { side: "long", stop: 98, target: 106, limit: 100 };
  const data = {
    bar_seconds: 300,
    entry: { price: 100.1, qty: 10, time: 600 },
    exits: [{ price: 103.1, qty: 10, time: 1500 }],
    bars: [bar(0, 120, 80), bar(600, 101, 99.1), bar(900, 105.1, 100), bar(1500, 104, 102), bar(1800, 130, 70)],
  };
  const s = tradeStats(row, data);
  assert.ok(Math.abs(s.pnl - 30) < 1e-9);
  assert.ok(Math.abs(s.rMultiple - 30 / (2.1 * 10)) < 1e-9);
  assert.ok(Math.abs(s.slippage - 0.1) < 1e-9);
  assert.ok(Math.abs(s.mfe - 5) < 1e-9);
  assert.ok(Math.abs(s.mae - 1) < 1e-9);
  assert.ok(Math.abs(s.capture - 60) < 1e-9);
  assert.equal(s.holdSeconds, 900);
});

test("short trade flips direction and an open trade holds until now", () => {
  const row = { side: "short", stop: 102, target: null, limit: 100 };
  const data = {
    bar_seconds: 300,
    entry: { price: 99.9, qty: 5, time: 0 },
    exits: [],
    bars: [bar(0, 101, 97), bar(300, 100, 95)],
  };
  const s = tradeStats(row, data, 3600);
  assert.equal(s.pnl, null);
  assert.ok(Math.abs(s.slippage - 0.1) < 1e-9);
  assert.ok(Math.abs(s.mfe - 4.9) < 1e-9);
  assert.ok(Math.abs(s.mae - 1.1) < 1e-9);
  assert.equal(s.holdSeconds, 3600);
  assert.equal(duration(3600 * 50), "2d 2h");
});
