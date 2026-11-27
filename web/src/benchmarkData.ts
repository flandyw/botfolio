import type { Point } from "./api";

const DAY = 86_400;

export function savingsBenchmark(times: number[], startDate: string, annualRatePct: number): Point[] {
  const start = Date.parse(startDate) / 1000;
  return times.flatMap((time) => {
    const days = Math.floor((time - start) / DAY);
    const value = 10_000 * (1 + annualRatePct / 100) ** (days / 365);
    return days >= 0 && Number.isFinite(value) ? [{ time, value }] : [];
  });
}
