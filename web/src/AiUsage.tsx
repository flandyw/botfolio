import { Fragment, type ReactNode, useEffect, useState } from "react";
import { HistogramSeries, LineStyle, type Time } from "lightweight-charts";
import { type AiUsage as Usage, type AiUsageDay, send } from "./api";
import { cell, cx } from "./classes";
import { palette, useChart, useThemeKey } from "./chart";
import { compact, num, usd } from "./format";
import { Button, Message, Section } from "./ui";
import { useApi } from "./useApi";

const neurons = (d: AiUsageDay) => d.neurons;
const vercelUsd = (d: AiUsageDay) => d.vercel_usd;
const fmtNeurons = (v: number) => compact(v);
const fmtUsd = (v: number) => usd(v, 4);
const MODEL: Record<string, string> = { clairic: "Cloudflare", chat: "Chat", chatgpt: "ChatGPT", claude: "Claude", systemone: "Clef", none: "No provider" };
const PLAN: Record<string, string> = { claude: "Claude", chatgpt: "ChatGPT" };
const label = (model: string) => {
  const [provider, name] = model.split(":");
  return `${MODEL[provider] ?? provider} · ${name}`;
};

/** AI spend and volume, plus the provider order and models the worker applies on its next cycle. */
export function AiUsage() {
  const usage = useApi<Usage>("ai", 60_000);
  const data = usage.data;
  const today = data?.days.find((d) => d.day === new Date().toISOString().slice(0, 10));
  const totals = (data?.days ?? []).reduce(
    (t, d) => ({ cf: t.cf + d.cf_usd, vercel: t.vercel + d.vercel_usd, neurons: t.neurons + d.neurons }),
    { cf: 0, vercel: 0, neurons: 0 },
  );
  const models = data ? perModel(data.days) : [];
  const vercel = data?.providers.find((p) => p.name === "vercel");
  const gw = vercel?.label ?? "Vercel";
  const labelOf = (name: string) => data?.providers.find((p) => p.name === name)?.label ?? name;

  return (
    <Section title="AI usage" note="UTC days · Cloudflare's free neuron allowance resets at 00:00 UTC">
      {usage.error && <Message error>{usage.error}</Message>}
      {!data ? (
        !usage.error && <Message>Loading AI usage…</Message>
      ) : (
        <div className="grid gap-6 border-t border-rule pt-3">
          {Object.entries(data.budget.throttled).map(([name, t]) => (
            <Message key={name}>
              {labelOf(name)} returned HTTP {t.status}
              {t.status === 402 ? " (no credit remaining)"
                : Date.parse(t.until) - Date.parse(t.at) > 1_800_000 ? " (plan usage limit reached)" : " (rate limited)"}.
              Skipping it until {new Date(t.until).toLocaleTimeString()}; articles go to the next provider in order.
            </Message>
          ))}
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
            {Object.entries(data.budget.plans).map(([name, u]) => (
              <Fragment key={name}>
                <Stat label={`${PLAN[name] ?? name}, last 5 hours`} value={`${u.five_hour} calls`}>
                  <Meter value={u.five_hour} max={u.max_five_hour} label={`${PLAN[name] ?? name} calls of the bot's 5-hour cap`} />
                  <span className="text-dim">of {u.max_five_hour} the bot may use</span>
                </Stat>
                <Stat label={`${PLAN[name] ?? name}, last 7 days`} value={`${u.week} calls`}>
                  <Meter value={u.week} max={u.max_week} label={`${PLAN[name] ?? name} calls of the bot's weekly cap`} />
                  <span className="text-dim">of {u.max_week} · the plan's own limits also cover your own use</span>
                </Stat>
              </Fragment>
            ))}
            {vercel?.ready && (
              <>
                <Stat label={`${gw} this month`} value={usd(data.budget.month_usd)}>
                  <Meter value={data.budget.month_usd} max={data.budget.monthly_usd} label={`${gw} spend of the monthly budget`} />
                  <span className="text-dim">
                    of {usd(data.budget.monthly_usd)} budget
                    {data.budget.balance != null && ` · ${usd(data.budget.balance)} credit left`}
                  </span>
                </Stat>
                <Stat label={`${gw} today`} value={usd(data.budget.today_usd, 4)}>
                  <Meter value={data.budget.today_usd} max={data.budget.per_day} label={`${gw} spend of today's allowance`} />
                  <span className="text-dim">of {usd(data.budget.per_day, 3)} today · pace now {usd(data.budget.pace, 3)}</span>
                </Stat>
              </>
            )}
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
            {vercel?.ready && <DailyChart title={`${gw} spend per day`} days={data.days} value={vercelUsd} format={fmtUsd} />}
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

          <Providers usage={data} onSaved={usage.reload} />
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

function Providers({ usage, onSaved }: { usage: Usage; onSaved: () => void }) {
  const s = usage.settings;
  const byName = Object.fromEntries(usage.providers.map((p) => [p.name, p]));
  // Enabled providers in priority order, then the rest; unticking one drops it from the order.
  const initial = () => [...s.order, ...usage.providers.map((p) => p.name).filter((n) => !s.order.includes(n))];
  const [list, setList] = useState(initial);
  const [enabled, setEnabled] = useState(() => new Set(s.order));
  const [models, setModels] = useState(s.models);
  const [monthly, setMonthly] = useState(s.monthly_usd);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ error?: boolean; text: string } | null>(null);
  const order = list.filter((n) => enabled.has(n));
  const changed =
    order.join() !== s.order.join() || JSON.stringify(models) !== JSON.stringify(s.models) || monthly !== s.monthly_usd;
  const move = (i: number, by: number) =>
    setList((l) => {
      const next = [...l];
      [next[i], next[i + by]] = [next[i + by], next[i]];
      return next;
    });
  const gw = byName.vercel?.label ?? "Vercel";

  return (
    <form
      className="grid gap-4 border-t border-rule pt-3 text-[13px]"
      onSubmit={(e) => {
        e.preventDefault();
        setBusy(true);
        send("PUT", "ai", { order, models, monthly_usd: monthly })
          .then(
            () => {
              setResult({ text: "Saved. The worker applies the new order on its next cycle; a model change records a new experiment." });
              onSaved();
            },
            (err: Error) => setResult({ error: true, text: err.message }),
          )
          .finally(() => setBusy(false));
      }}
    >
      <h3 className="text-[14px] font-semibold">Providers</h3>
      <p className="text-dim">
        Each article goes to the first ticked provider that can take it. Plans stop at the bot's call caps, {gw} at its paced
        monthly budget and {usage.budget.rpm} requests/minute, and any provider that returns a rate or usage limit is skipped
        while it cools down. Cloudflare always answers (it bills past its free neurons), so keep it last as the fallback. If
        none can take an article, the bot does not trade it.
      </p>
      <ol className="grid gap-2">
        {list.map((name, i) => {
          const p = byName[name];
          return (
            <li key={name} className="flex flex-wrap items-center gap-3 border border-rule p-2">
              <span className="num w-5 text-dim">{enabled.has(name) && p.ready ? order.indexOf(name) + 1 : "–"}</span>
              <label className="flex min-w-48 flex-1 items-center gap-2">
                <input
                  type="checkbox"
                  disabled={!p.ready}
                  checked={enabled.has(name)}
                  onChange={(e) =>
                    setEnabled((set) => {
                      const next = new Set(set);
                      if (e.target.checked) next.add(name);
                      else next.delete(name);
                      return next;
                    })
                  }
                />
                <span>{p.label}</span>
              </label>
              {p.ready ? (
                p.options.length > 1 ? (
                  <select
                    value={models[name]}
                    onChange={(e) => setModels({ ...models, [name]: e.target.value })}
                    aria-label={`${p.label} model`}
                    className="num border border-rule bg-bg p-1.5 text-[12px] focus:border-ink focus:outline-none"
                  >
                    {!p.options.includes(models[name]) && <option value={models[name]}>{models[name]}</option>}
                    {p.options.map((m) => (
                      <option key={m} value={m}>
                        {m}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="num text-dim">{models[name]}</span>
                )
              ) : (
                <span className="text-dim">Not configured in .env</span>
              )}
              <span className="flex gap-1">
                {([-1, 1] as const).map((by) => (
                  <button
                    key={by}
                    type="button"
                    disabled={i + by < 0 || i + by >= list.length}
                    onClick={() => move(i, by)}
                    aria-label={`Move ${p.label} ${by < 0 ? "up" : "down"}`}
                    className="border border-rule px-2 py-1 hover:border-ink disabled:cursor-not-allowed disabled:border-hair disabled:text-faint"
                  >
                    {by < 0 ? "↑" : "↓"}
                  </button>
                ))}
              </span>
            </li>
          );
        })}
      </ol>
      {byName.vercel?.ready && (
        <label className="grid gap-2">
          <span className="text-dim">
            {gw} monthly budget: <span className="num text-ink">{usd(monthly)}</span>
            {gw === "Vercel" && monthly > 5 && " (above the $5/month free credit; overage is billed)"}
          </span>
          <input
            type="range"
            min={0}
            max={usage.max_monthly_usd}
            step={0.5}
            value={monthly}
            onChange={(e) => setMonthly(Number(e.target.value))}
            aria-label={`${gw} spend per month in US dollars`}
            className="w-full max-w-md accent-(--ink)"
          />
        </label>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={busy || !changed || order.length === 0}>
          Save providers
        </Button>
        {changed && (
          <button
            type="button"
            className="text-[13px] text-dim underline underline-offset-4"
            onClick={() => {
              setList(initial());
              setEnabled(new Set(s.order));
              setModels(s.models);
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
