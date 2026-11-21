import { type ReactNode, useEffect, useState } from "react";
import { HistogramSeries, LineStyle, type Time } from "lightweight-charts";
import { type AiUsage as Usage, type AiUsageDay, send } from "./api";
import { cell, cx } from "./classes";
import { palette, useChart, useThemeKey } from "./chart";
import { compact, num, usd } from "./format";
import { Button, Choice, Message, Section } from "./ui";
import { useApi } from "./useApi";

const neurons = (d: AiUsageDay) => d.neurons;
const vercelUsd = (d: AiUsageDay) => d.vercel_usd;
const fmtNeurons = (v: number) => compact(v);
const fmtUsd = (v: number) => usd(v, 4);
const MODEL: Record<string, string> = { clairic: "Cloudflare", chat: "Vercel", systemone: "Clef" };
const label = (model: string) => {
  const [provider, name] = model.split(":");
  return `${MODEL[provider] ?? provider} · ${name}`;
};

/** AI spend and volume, plus the model and split choices the worker applies on its next cycle. */
export function AiUsage() {
  const usage = useApi<Usage>("ai", 60_000);
  const data = usage.data;
  const today = data?.days.find((d) => d.day === new Date().toISOString().slice(0, 10));
  const totals = (data?.days ?? []).reduce(
    (t, d) => ({ cf: t.cf + d.cf_usd, vercel: t.vercel + d.vercel_usd, neurons: t.neurons + d.neurons }),
    { cf: 0, vercel: 0, neurons: 0 },
  );
  const models = data ? perModel(data.days) : [];

  return (
    <Section title="AI usage" note="UTC days; Cloudflare's free neuron allowance resets at 00:00 UTC">
      {usage.error && <Message error>{usage.error}</Message>}
      {!data ? (
        !usage.error && <Message>Loading AI usage…</Message>
      ) : (
        <div className="grid gap-6 border-t border-rule pt-3">
          {data.budget.throttled && (
            <Message>
              Vercel refused with HTTP {data.budget.throttled.status}
              {data.budget.throttled.status === 402 ? " (no credit left)" : " (rate limited)"}. Articles go to
              Cloudflare until {new Date(data.budget.throttled.until).toLocaleTimeString()}.
            </Message>
          )}
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
            <Stat label="Vercel this month" value={usd(data.budget.month_usd)}>
              <Meter value={data.budget.month_usd} max={data.budget.monthly_usd} label="Vercel spend of the monthly budget" />
              <span className="text-dim">
                of {usd(data.budget.monthly_usd)} budget
                {data.budget.balance != null && ` · ${usd(data.budget.balance)} credit left`}
              </span>
            </Stat>
            <Stat label="Vercel today" value={usd(data.budget.today_usd, 4)}>
              <Meter value={data.budget.today_usd} max={data.budget.per_day} label="Vercel spend of today's allowance" />
              <span className="text-dim">of {usd(data.budget.per_day, 3)} today · pace now {usd(data.budget.pace, 3)}</span>
            </Stat>
            <Stat label="Neurons today" value={compact(today?.neurons ?? 0)}>
              <Meter value={today?.neurons ?? 0} max={data.free_neurons} label="Neurons used of the free daily allowance" />
              <span className="text-dim">of {compact(data.free_neurons)} free</span>
            </Stat>
            <Stat label="Cloudflare overage today" value={usd(today?.cf_usd ?? 0)} />
            <Stat label="14-day spend" value={usd(totals.cf + totals.vercel)}>
              <span className="text-dim">{compact(totals.neurons)} neurons</span>
            </Stat>
          </div>

          <div className="grid gap-6 lg:grid-cols-[repeat(2,minmax(0,1fr))]">
            <DailyChart
              title="Cloudflare neurons per day"
              days={data.days}
              value={neurons}
              format={fmtNeurons}
              limit={data.free_neurons}
            />
            <DailyChart title="Vercel spend per day" days={data.days} value={vercelUsd} format={fmtUsd} />
          </div>

          {models.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[620px] border-collapse text-[13px]">
                <thead>
                  <tr>
                    <th className={cell.th}>Model</th>
                    <th className={cx(cell.th, cell.right)}>Calls today</th>
                    <th className={cx(cell.th, cell.right)}>Calls 14d</th>
                    <th className={cx(cell.th, cell.right)}>Failed</th>
                    <th className={cx(cell.th, cell.right)}>Input tokens / call</th>
                    <th className={cx(cell.th, cell.right)}>Output tokens / call</th>
                    <th className={cx(cell.th, cell.right)}>Latency</th>
                  </tr>
                </thead>
                <tbody>
                  {models.map((m) => (
                    <tr key={m.model}>
                      <td className={cell.td}>{label(m.model)}</td>
                      <td className={cx(cell.td, cell.right)}>{today?.models[m.model]?.calls ?? 0}</td>
                      <td className={cx(cell.td, cell.right)}>{m.calls}</td>
                      <td className={cx(cell.td, cell.right, m.failures > 0 && "text-warn")}>
                        {num((100 * m.failures) / m.calls, 1)}%
                      </td>
                      <td className={cx(cell.td, cell.right)}>{compact(m.input / m.calls)}</td>
                      <td className={cx(cell.td, cell.right)}>{compact(m.output / m.calls)}</td>
                      <td className={cx(cell.td, cell.right)}>{num(m.latency / m.calls, 1)} s</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <Models usage={data} onSaved={usage.reload} />
        </div>
      )}
    </Section>
  );
}

function perModel(days: AiUsageDay[]) {
  const rows: Record<string, { model: string; calls: number; failures: number; input: number; output: number; latency: number }> = {};
  for (const d of days)
    for (const [model, m] of Object.entries(d.models)) {
      const r = (rows[model] ??= { model, calls: 0, failures: 0, input: 0, output: 0, latency: 0 });
      r.calls += m.calls;
      r.failures += m.failures;
      r.input += m.input;
      r.output += m.output;
      r.latency += m.latency * m.calls;
    }
  return Object.values(rows).sort((a, b) => b.calls - a.calls);
}

function Stat({ label, value, children }: { label: string; value: string; children?: ReactNode }) {
  return (
    <div className="grid content-start gap-1 text-[12px]">
      <span className="text-dim">{label}</span>
      <span className="num text-[20px] leading-6">{value}</span>
      {children}
    </div>
  );
}

function Meter({ value, max, label }: { value: number; max: number; label: string }) {
  const share = max > 0 ? Math.min(1, value / max) : 1;
  return (
    <div
      role="meter"
      aria-valuenow={value}
      aria-valuemin={0}
      aria-valuemax={max}
      aria-label={label}
      className="h-1.5 w-full bg-hair"
    >
      <div className={cx("h-full", value > max ? "bg-down" : share > 0.8 ? "bg-warn" : "bg-ink")} style={{ width: `${share * 100}%` }} />
    </div>
  );
}

function DailyChart({
  title,
  days,
  value,
  format,
  limit,
}: {
  title: string;
  days: AiUsageDay[];
  value: (d: AiUsageDay) => number;
  format: (v: number) => string;
  limit?: number;
}) {
  const { ref, chart } = useChart({ timeScale: { timeVisible: false }, rightPriceScale: { scaleMargins: { top: 0.15, bottom: 0 } } });
  const themeKey = useThemeKey();

  useEffect(() => {
    if (!chart) return;
    const colors = palette();
    const bars = chart.addSeries(HistogramSeries, {
      priceLineVisible: false,
      priceFormat: { type: "custom", formatter: format, minMove: 0.0001 },
    });
    bars.setData(
      days.map((d) => ({
        time: d.day as Time,
        value: value(d),
        color: limit != null && value(d) > limit ? colors.down : colors.dim,
      })),
    );
    if (limit != null)
      bars.createPriceLine({ price: limit, color: colors.faint, lineWidth: 1, lineStyle: LineStyle.Dashed, title: "free" });
    chart.timeScale().fitContent();
    return () => {
      try {
        chart.removeSeries(bars);
      } catch {
        // the chart was already removed
      }
    };
  }, [chart, days, value, format, limit, themeKey]);

  return (
    <figure className="m-0 grid gap-1">
      <figcaption className="text-[13px] text-dim">{title}</figcaption>
      <div ref={ref} className="h-[180px] w-full" aria-label={title} role="img" />
    </figure>
  );
}

function Models({ usage, onSaved }: { usage: Usage; onSaved: () => void }) {
  const s = usage.settings;
  const [primary, setPrimary] = useState(s.primary_model);
  const [secondary, setSecondary] = useState(s.secondary_model);
  const saved = s.share === "auto" ? "auto" : Math.round(s.share * 100);
  const [share, setShare] = useState<"auto" | number>(saved);
  const [monthly, setMonthly] = useState(s.monthly_usd);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ error?: boolean; text: string } | null>(null);
  const changed = primary !== s.primary_model || secondary !== s.secondary_model || share !== saved || monthly !== s.monthly_usd;

  return (
    <form
      className="grid gap-4 border-t border-rule pt-3 text-[13px]"
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        send("PUT", "ai", { primary_model: primary, secondary_model: secondary, share: share === "auto" ? "auto" : share / 100, monthly_usd: monthly })
          .then(
            () => {
              setResult({ text: "Saved. The worker switches on its next cycle; a new model gets its own experiment." });
              onSaved();
            },
            (err: Error) => setResult({ error: true, text: err.message }),
          )
          .finally(() => setBusy(false));
      }}
    >
      <h3 className="text-[14px] font-semibold">Models</h3>
      <div className="grid gap-4 sm:grid-cols-[repeat(2,minmax(0,1fr))]">
        <div className="grid content-start gap-2">
          <span className="text-dim">Cloudflare Workers AI</span>
          {usage.options.primary.length > 1 ? (
            <Choice
              label="Cloudflare model"
              value={primary}
              onChange={setPrimary}
              options={usage.options.primary.map((m) => [m, m] as const)}
            />
          ) : (
            <span>{s.primary_model} (set in .env)</span>
          )}
        </div>
        <label className="grid content-start gap-2">
          <span className="text-dim">Vercel AI Gateway</span>
          {s.secondary_ready ? (
            <select
              value={secondary}
              onChange={(e) => setSecondary(e.target.value)}
              className="num border border-rule bg-bg p-1.5 text-[12px] focus:border-ink focus:outline-none"
            >
              {!usage.options.secondary.includes(secondary) && <option value={secondary}>{secondary}</option>}
              {usage.options.secondary.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : (
            <span>Not set up. Add the EVENT_AI_SECONDARY_* settings to .env.</span>
          )}
        </label>
      </div>
      {s.secondary_ready && (
        <label className="grid gap-2">
          <span className="text-dim">
            Vercel spend per month: <span className="num text-ink">{usd(monthly)}</span>
            {monthly > 10 && " (above the $10 free credit, so the rest is billed)"}
          </span>
          <input
            type="range"
            min={0}
            max={usage.max_monthly_usd}
            step={0.5}
            value={monthly}
            onChange={(e) => setMonthly(Number(e.target.value))}
            aria-label="Vercel spend per month in US dollars"
            className="w-full max-w-md accent-(--ink)"
          />
        </label>
      )}
      {s.secondary_ready && (
        <div className="grid gap-2">
          <span className="text-dim">Split</span>
          <Choice
            label="Split mode"
            value={share === "auto" ? "auto" : "fixed"}
            onChange={(v) => setShare(v === "auto" ? "auto" : 40)}
            options={[
              ["auto", "Auto: pace the monthly budget"],
              ["fixed", "Fixed share"],
            ]}
          />
          {share === "auto" ? (
            <p className="text-dim">
              Vercel takes articles while its spend is under an even pace toward {usd(monthly)} a month;
              unspent credit carries to later days. Cloudflare takes the rest within its free neurons, and Vercel picks
              up the overflow once those run out.
            </p>
          ) : (
            <label className="grid gap-2">
              <span className="num">
                {100 - share}% Cloudflare / {share}% Vercel
              </span>
              <input
                type="range"
                min={0}
                max={100}
                step={5}
                value={share}
                onChange={(e) => setShare(Number(e.target.value))}
                aria-label="Vercel share of articles"
                className="w-full max-w-md accent-(--ink)"
              />
            </label>
          )}
          <p className="text-dim">
            Either way Vercel stops at the daily budget, at {usage.budget.rpm} calls a minute, and for a cooldown after
            a rate limit (429) or empty credit (402); those articles go to Cloudflare.
          </p>
        </div>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={busy || !changed}>
          Save models
        </Button>
        {changed && (
          <button
            type="button"
            className="text-[13px] text-dim underline underline-offset-4"
            onClick={() => {
              setPrimary(s.primary_model);
              setSecondary(s.secondary_model);
              setShare(saved);
              setMonthly(s.monthly_usd);
            }}
          >
            Discard changes
          </button>
        )}
      </div>
      {result && <Message error={result.error}>{result.text}</Message>}
    </form>
  );
}
