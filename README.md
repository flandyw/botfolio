<img src="assets/icon.svg" width="64" height="64" alt="">

# tiktoktrading: do TikTok chart patterns beat a coin flip?

A fully automated, zero-subscription-cost research bot that **paper-trades** the intraday chart patterns
popularised on TikTok/YouTube (ICT / Smart-Money-Concepts and classics) on **QQQ** (NASDAQ-100 proxy) and
**SPY** (S&P 500 proxy). It measures whether any of them has a statistically significant edge **after trading
costs**, against:

* an **inverse-signal** twin of every strategy (takes the exact opposite trade),
* a **random-entry** baseline (same exits, same costs), and a 2,000-draw Monte-Carlo random-entry null,
* **buy-and-hold**.

**Short answer: they don't, but one non-TikTok strategy does.** None of the 16 TikTok pattern portfolios
beats random entry after costs. An intraday-momentum "noise area" breakout from the academic literature,
added as a contrast, made +108% over 10.9 years on QQQ after costs (Sharpe 0.84, max drawdown 10.5%,
p = 0.004). See [Intraday momentum](#intraday-momentum-the-strategy-that-does-make-money).

Strategies: Fair Value Gaps · liquidity sweeps · order blocks · Break of Structure (BOS) · Change of
Character (CHoCH) · EMA 9/21 crossover · RSI divergence · 15-minute opening-range breakout. Each one runs on both
**1-minute and 5-minute** candles. That makes 16 pattern portfolios, 16 inverse portfolios, 2 random
portfolios and 2 buy-and-hold portfolios, each with its own **$100,000 virtual account**.

> **Paper trading only.** No code path can place a real-money order. See [Safety](#safety-no-live-trading).

---

## Results so far (1 year, 2025-11-17 → 2026-11-13, 194k minute bars)

| run | avg pattern return | best pattern | patterns > 0 | buy & hold QQQ / SPY | significant edges |
|---|---|---|---|---|---|
| iex-1y | -33.5% | bos_5m -6.6% | 0/16 | 12.8% / 13.8% | 0 |
| sip-1y | -34.2% | bos_5m -5.4% | 0/16 | 12.7% / 13.8% | 0 |

`iex-1y` = default costs on free IEX data · `sip-1y` = same on consolidated-tape (SIP) history ·
`iex-1y-lowcost` = optimistic costs (no slippage beyond the 1-cent spread).

Per strategy (`iex-1y`, default costs, sorted by total return):

| strategy    |   trades |   win % |   PF |   gross %/trade |   net %/trade |   total return % | inverse total %   |   Sharpe |   max DD % |   p vs random |   q (FDR) |
|:------------|---------:|--------:|-----:|----------------:|--------------:|-----------------:|:------------------|---------:|-----------:|--------------:|----------:|
| bos_5m      |      531 |    43.1 | 0.9  |          0.0005 |       -0.0303 |             -6.6 | -12.0             |    -0.83 |        9.2 |         0.552 |     0.93  |
| rsi_div_5m  |      370 |    34.1 | 0.8  |          0.0038 |       -0.0295 |             -8.4 | -11.4             |    -1.61 |       11.5 |         0.388 |     0.93  |
| orb15_5m    |      370 |    41.1 | 0.84 |         -0.0054 |       -0.0376 |            -10.2 | -5.3              |    -1.26 |       17.9 |         0.658 |     0.93  |
| orb15_1m    |      326 |    38   | 0.75 |         -0.0022 |       -0.035  |            -11.1 | -3.3              |    -2.09 |       13.1 |         0.59  |     0.93  |
| choch_5m    |      664 |    40.5 | 0.86 |          0.0048 |       -0.0273 |            -12.2 | -12.1             |    -1.24 |       15.7 |         0.426 |     0.93  |
| random_5m   |      592 |    33.8 | 0.71 |         -0.0029 |       -0.0367 |            -19.9 | —                 |    -3.29 |       22   |         0.666 |     0.93  |
| ema_9_21_5m |      751 |    33.2 | 0.75 |         -0.0027 |       -0.037  |            -23.1 | -16.4             |    -3.02 |       25.9 |         0.69  |     0.93  |
| ob_5m       |      813 |    29   | 0.63 |         -0.0098 |       -0.0448 |            -26.9 | -16.4             |    -4.64 |       27.2 |         0.944 |     0.952 |
| sweep_5m    |      896 |    29   | 0.56 |         -0.0035 |       -0.0388 |            -29.1 | -17.9             |    -7.74 |       29.3 |         0.894 |     0.952 |
| random_1m   |      870 |    25.4 | 0.45 |         -0.0051 |       -0.0416 |            -30.3 | —                 |    -9.43 |       30.2 |         0.952 |     0.952 |
| rsi_div_1m  |     1631 |    28   | 0.59 |          0.0063 |       -0.0294 |            -38.5 | -37.8             |    -7.59 |       39.1 |         0.077 |     0.523 |
| fvg_5m      |     1665 |    30.6 | 0.68 |          0.0046 |       -0.0303 |            -39.6 | -34.5             |    -6.39 |       41.5 |         0.126 |     0.523 |
| bos_1m      |     2363 |    34.7 | 0.72 |          0.0041 |       -0.0298 |            -47.7 | -42.2             |    -6.17 |       50.1 |         0.342 |     0.93  |
| choch_1m    |     2326 |    33.4 | 0.67 |         -0.0007 |       -0.0349 |            -53.4 | -35.1             |    -7.05 |       55.3 |         0.766 |     0.93  |
| fvg_1m      |     2500 |    27.6 | 0.55 |          0.0034 |       -0.0324 |            -54.7 | -48.1             |   -12.45 |       55.3 |         0.032 |     0.493 |
| sweep_1m    |     2472 |    22.5 | 0.38 |          0.003  |       -0.0344 |            -57.1 | -51.5             |   -20.02 |       57.2 |         0.138 |     0.523 |
| ob_1m       |     2500 |    25.7 | 0.48 |          0.0017 |       -0.0347 |            -58.2 | -45.5             |   -15.89 |       58.4 |         0.513 |     0.93  |

**What this says**

1. **No pattern has a statistically significant edge.** After Benjamini–Hochberg correction for 34 tests,
   the smallest q-value against random entry was 0.49 (IEX) and the t-test of net return > 0 gave
   p ≈ 1.0 for every strategy. Every pattern lost money after costs. Buy-and-hold made about +13%.
2. **The gross edge is roughly zero.** Before costs, per-trade returns are within ±0.01% of notional. Costs
   (spread + slippage + fees ≈ 0.03% per round trip) are 3–10× larger than any signal. The 1-minute
   versions trade the most, so they lose the most (−40% to −60%).
3. **The inverse twins lose too.** If a pattern predicted direction, its inverse would lose *more* than it
   gains. Both sides losing roughly the cost of trading is what "no information" looks like.
4. **"Beats random" is not "makes money".** In the SIP run, `inv_fvg_1m` and `sweep_1m` beat the
   random-entry null (q = 0.025) by just 0.3–0.6 bp per trade, and both still lose money. `fvg_1m` and its
   own inverse each beat the null in different runs, which points to a limit of the null model (it uses the
   strategy's *median* stop size) rather than skill. The verdict therefore requires **both** tests to pass.
5. **Optimistic costs don't change the conclusion.** `bos_5m` makes +3% a year with zero slippage, with
   no statistical support, against +13% for doing nothing.

Re-run these yourself (results are deterministic for a given data snapshot and `seed`), then let the forward
paper-trading loop collect genuinely out-of-sample evidence.

---

## Intraday momentum: the strategy that does make money

`noise_qqq_1m` / `noise_spy_1m` implement the "noise area" breakout of Zarattini, Aziz & Barbon (2024),
*Beat the Market: An Effective Intraday Momentum Strategy for S&P500 ETF (SPY)*, with the paper's parameters
(14-session lookback, checks every 30 minutes) and no tuning on this data. The band around the open is the
average absolute move from the open at that time of day over the last 14 sessions. Go long above the band, short
below it, flat inside it (the band works as a trailing stop), and flat at the close. That is about one round
trip a day, against 4–10 for the 1-minute patterns. Sizing is 100% of equity, with no leverage and no
overnight risk. Definition: [docs/STRATEGIES.md](docs/STRATEGIES.md#intraday-momentum-noise-area).

10.9 years of SIP data (2016-01-04 → 2026-11-13, 2.1M minute bars), default costs
(`uv run ttbot --config config/momentum.yaml backtest --start 2016-01-01 --end 2026-11-13 --feed sip --no-mc`):

| portfolio | trades | win % | PF | net %/trade | total return % | Sharpe | max DD % | p (t-test) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| **noise_qqq_1m** | 2244 | 37.7 | 1.19 | 0.035 | **+108.4** | **0.84** | **10.5** | **0.004** |
| noise_spy_1m | 2356 | 34.6 | 1.00 | 0.001 | +0.0 | 0.03 | 16.8 | 0.456 |
| inv_noise_qqq_1m | 2247 | 54.6 | 0.60 | −0.088 | −86.5 | −2.10 | 86.5 | 1.0 |
| random_1m | 9549 | 26.5 | 0.50 | −0.038 | −97.2 | −6.92 | 97.3 | 1.0 |
| buy_hold_QQQ | — | — | — | — | +572.6 | 0.90 | 35.0 | — |

`noise_qqq_1m` by calendar year (%): 2016 −2.4 · 2017 −0.5 · **2018 +29.8** · 2019 −2.1 · 2020 +8.0 · 2021 +3.8 ·
**2022 +12.8** · 2023 +12.5 · 2024 +7.0 · 2025 +6.8 · 2026 (to Nov 13) +4.0. Sharpe was 0.88 for 2016–2023 and 0.73
for 2024–2026.

**What this says**

1. **There is a real edge on QQQ, and it survives costs.** The gross edge is about 0.06% per trade, roughly 2× the
   0.027% cost. The inverse twin loses heavily, which a signal with no information would not do.
2. **It earns less than buy-and-hold but takes far less risk.** It returns about 7% a year against 19% for
   buy-and-hold, at a similar Sharpe, with a third of the drawdown. It is flat every night, and it made money in
   2018 and 2022 while QQQ fell. That makes it a diversifier, not a replacement for holding the index.
3. **SPY breaks even.** Its gross edge (Sharpe ≈ 0.85 before costs) is about the size of its costs, so it is kept
   only as a control. QQQ was already the stronger market in 2016–2023, so the QQQ choice does not depend on the
   recent years.
4. **The Monte-Carlo null is skipped.** That test models fixed stop/target exits, and this strategy uses neither, so
   its verdict rests on the t-test plus the inverse and random controls.
5. **Caveats.** This is a published strategy, and its edge may shrink now that it is public. Fills are simulated
   (1 bp slippage plus half a 1-cent spread per side). Keep it on forward paper trading before trusting it:
   `TTBOT_MIRROR_STRATEGY=noise_qqq_1m uv run ttbot forward`.

---

## Quick start (local)

Requires [uv](https://docs.astral.sh/uv/) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
and free Alpaca **paper** API keys
([dashboard → Paper → API keys](https://app.alpaca.markets/paper/dashboard/overview)). uv installs the right
Python (3.12, pinned in `.python-version`) and the exact locked dependencies from `uv.lock`.

```bash
cp .env.example .env            # put ALPACA_KEY / ALPACA_SECRET (paper keys) in it
uv sync                         # creates .venv with all deps (incl. dev tools)

uv run ttbot check                                         # verifies paper account + market-data access
uv run ttbot backtest --start 2025-11-17 --end 2026-11-13  # downloads + caches bars, prints the ranking
uv run ttbot backtest --start 2025-11-17 --end 2026-11-13 --feed sip --run-id sip-1y
uv run ttbot simulate --day 2026-11-13                     # local simulated execution test (see below)
uv run ttbot forward                                       # forward paper trading, runs forever
uv run ttbot dashboard                                     # http://localhost:8501
uv run ttbot report [--run-id ID]                          # list runs / print a ranking
uv run pytest                                              # 48 unit + integration tests
uv run ruff check src tests && uv run mypy src             # lint + type check
```

On a Mac, keep it awake while forward trading with `caffeinate -i uv run ttbot forward`. US regular hours
(09:30–16:00 ET) are overnight in Australia/Asia.

`ttbot backtest --synthetic …` runs on a random-walk dataset with no network access. Every strategy *must*
lose about its costs there; a "winning" strategy on synthetic data would reveal look-ahead bias.

### Configuration

* `config/default.yaml`: symbols, timeframes, strategy roster and parameters, cost model, risk limits,
  exit rules, seed. `config/low_cost.yaml` is the optimistic-cost sensitivity scenario
  (`uv run ttbot --config config/low_cost.yaml backtest …`).
* Environment variables (`.env`): `ALPACA_KEY`, `ALPACA_SECRET` (or `APCA_API_KEY_ID`,
  `APCA_API_SECRET_KEY`); optional `TTBOT_DB_PATH`, `TTBOT_CONFIG`, `TTBOT_LOG_LEVEL`, `TTBOT_LOG_JSON`,
  `TTBOT_DATA_FEED` (`iex`|`sip`), `TTBOT_SYMBOLS`, `TTBOT_MIRROR_STRATEGY`.
  Secrets never go in YAML and are never logged.

Key defaults, identical for **every** portfolio:

| | |
|---|---|
| capital | $100,000 per strategy |
| sizing | risk 0.5% of equity per trade (stop distance), max 100% notional (no leverage) |
| risk limits | ≤ 2 open positions, ≤ 10 trades/day, stop opening trades after −2% on the day |
| stop | the pattern's own invalidation level, clamped to [0.5, 3] × ATR(14); ATR × 1.5 if none |
| target | 2R · time stop 60 bars · flat at the close · no entries in the last 15 min |
| costs | half of a 1¢ spread + 1 bp slippage per market order, +2 bp on stops, SEC + FINRA TAF fees on sells, $0 commission (Alpaca) |

## How it works

```
Alpaca REST (IEX/SIP 1-min bars) ──► SQLite bar cache ──► session filter (NYSE calendar)
                                                              │
               ┌──────────────────────── Engine (same code for backtest & forward) ─────────────────┐
               │ 1m bar ─► 5m aggregator ─► strategies (signal at bar close)                        │
               │        └► portfolios: fill queued orders at NEXT bar open, then stops/targets/time │
               └─────────────────────────────────────────────────────────────────────────────────────┘
                     │ trades, signals, daily equity, engine snapshot
                     ▼
                  SQLite ──► analysis (metrics, t-test, bootstrap, Monte-Carlo null, BH-FDR) ──► Streamlit
```

* **No look-ahead / no repainting.** Signals are computed from completed bars only and filled at the
  **next** 1-minute bar's open. Swing pivots are only used after their right-hand confirmation bars, and
  5-minute bars are only emitted once complete. `tests/test_no_lookahead.py` re-runs every strategy on
  truncated histories and requires identical past signals.
* **Realistic fills.** Gaps through a stop fill at the open. When a bar touches both stop and target, the
  stop is assumed first. Targets are limit orders that need price to trade *through* them. Exits are
  checked on 1-minute bars even for 5-minute strategies.
* **Market hours.** Only regular-session bars (09:30–16:00 ET) are used. Holidays and early closes come
  from `pandas-market-calendars` (XNYS). Intraday positions are flattened on the session's final bar.
* **Backtest = forward.** `tests/test_engine_live.py` proves the polling forward runner produces exactly
  the same trades as the backtester for a session.

Exact pattern definitions are in **[docs/STRATEGIES.md](docs/STRATEGIES.md)**.

### Statistics

For every non-benchmark portfolio:

* **Metrics**: win rate, profit factor, average gross/net return per trade, average cost, expectancy (R),
  total return, Sharpe and Sortino (daily, annualised, rf = 0), max drawdown.
* **t-test**: one-sided, per-trade net return > 0, plus a 95% bootstrap CI.
* **Random-entry Monte-Carlo** (`montecarlo.py`): 2,000 portfolios of K random entry times (same K, long
  fraction, median stop in ATRs, reward/risk, costs and session filters). p = share of random portfolios
  that did at least as well. A vectorised exit simulator is verified against the event-driven portfolio
  in `tests/test_montecarlo.py`.
* **Benjamini–Hochberg FDR** over all tested strategies (q-values).
* **Verdict** `significant edge` = ≥ 30 trades and q < 0.05 on both the t-test and the random-entry test.

## Dashboard

`ttbot dashboard` (or the `dashboard` container) shows the following for any run (backtest, simulation or forward):
KPIs; a **ranking table** (win rate, profit factor, Sharpe, max DD, p/q-values, verdict); net return per
trade for pattern vs inverse vs random; **equity and drawdown curves**; a **significance chart** (bootstrap
CI vs the random-entry band); **executed trades** with CSV export; and **live status** (heartbeat,
mark-to-market equity, latest signals). The "How to read this" tab explains every number.

## Forward paper trading & crash recovery

`ttbot forward` polls Alpaca once a minute (10 s after the minute closes), processes only completed bars,
and persists trades, signals, equity, a heartbeat and a **pickled engine snapshot** to SQLite after every
cycle.

* **On a fresh start** it warms indicators and structure on the previous 3 sessions, then starts all
  portfolios from clean capital (warm-up trades are discarded).
* **After a crash or restart** it restores the snapshot and catches up on missed minutes (up to 7 days).
  Duplicate bars are ignored, and trade IDs are deterministic, so nothing is double-counted.
  `ttbot simulate` replays a real session minute by minute, kills the runner midway, recovers it, and checks
  the result is **identical** to an uninterrupted run.
* If the code or config changes incompatibly, the snapshot is discarded and the run restarts with fresh
  portfolios.
* Docker's `restart: unless-stopped` plus a heartbeat **health check** handle process crashes.

**Optional Alpaca paper mirror:** set `TTBOT_MIRROR_STRATEGY=noise_qqq_1m` (any portfolio name) to also send that
one portfolio's entries and exits as market orders to your Alpaca **paper** account, so you can watch them in
Alpaca's UI. Only one portfolio can be mirrored, because one Alpaca account cannot hold 36 independent books.
The local simulation remains the source of truth for all statistics.

## Deploy on an Ubuntu VPS (Docker Compose)

```bash
# once: install Docker Engine + compose plugin (https://docs.docker.com/engine/install/ubuntu/)
git clone <your repo> tiktoktrading && cd tiktoktrading
cp .env.example .env && nano .env              # paper keys
mkdir -p data && sudo chown 1000:1000 data     # containers run as uid 1000
docker compose up -d --build                   # starts bot + dashboard
docker compose logs -f bot                     # structured JSON logs
docker compose ps                              # health status

# historical backtest inside the stack (results appear in the dashboard):
docker compose run --rm backtest ttbot backtest --start 2025-11-17 --end 2026-11-13

# dashboard is bound to 127.0.0.1 - open it through an SSH tunnel:
ssh -L 8501:localhost:8501 user@your-vps   # then browse http://localhost:8501
```

All state lives in `./data/ttbot.sqlite` (back it up with `sqlite3 data/ttbot.sqlite ".backup b.sqlite"`).
The container clock is UTC; market hours come from the exchange calendar, so the VPS timezone does not
matter. A 1 vCPU / 1 GB VPS is enough.

## Safety: no live trading

1. The only trading endpoint is the hard-coded constant `https://paper-api.alpaca.markets`. It is not
   configurable, and every trading request is checked against it (`safety.assert_paper_url`).
2. Before any order, the account returned by Alpaca must be a **paper account** (number starts with `PA`).
   Live keys fail this check (`safety.assert_paper_account`).
3. Startup aborts if `APCA_API_BASE_URL`/`ALPACA_BASE_URL` point anywhere else, or if `ALPACA_PAPER=false`.
4. By default **no orders are sent at all**. Every strategy trades a local virtual portfolio, and Alpaca
   is used only for market data.
5. Tests cover all of the above, including that a live-account response blocks the order before any POST.

## Limitations (read before trusting any number)

* **Alpaca's free data is IEX-only in real time.** IEX carries roughly 2–3% of US equity volume, so its
  1-minute candles differ from the consolidated tape: wicks are smaller, there are occasional missing
  minutes (handled), and volume is not representative (no strategy uses volume). Historical **SIP** data is
  free except for the most recent 15 minutes, so backtests can use either (`--feed sip`); forward trading
  must use IEX. The two feeds gave the same conclusion above.
* **Fills are simulated.** The model has no queue position, partial fills or market impact, and uses a
  fixed spread/slippage assumption. Short-borrow fees are ignored (intraday only, easy-to-borrow ETFs).
  The SEC fee rate changes yearly; adjust `sec_fee_per_million`.
* **Bar-level ambiguity.** Intrabar order is unknown, so a bar that touches both stop and target is
  counted as a loss (conservative).
* **Pattern definitions are one reasonable, reproducible reading** of loosely specified folklore. Others
  exist; parameters are in the YAML. Tuning many variants on the same data will produce false positives,
  so judge only by q-values and out-of-sample (forward) results.
* **One year is one market regime.** Run longer periods (Alpaca history goes back to 2016) and keep the
  forward loop running.
* The 1-minute pattern strategies often hit the 10-trades-per-day cap, so their results reflect the
  earliest signals of each day.
* The random-entry null uses the strategy's median stop size, while real strategies vary their stops. See
  result 4 above.

## Project layout

```
src/ttbot/
  config.py safety.py logging_setup.py storage.py cli.py
  data/        models, NYSE calendar, Alpaca client, cached loader, 1m→5m aggregator, synthetic data
  indicators.py  streaming EMA / Wilder RSI / Wilder ATR
  patterns/    swings, FVG, structure (BOS/CHoCH), order blocks
  strategies/  smc.py (FVG, sweep, OB, BOS/CHoCH), classic.py (EMA, RSI div, ORB), momentum.py (noise area),
               baselines.py
  execution/   costs.py, portfolio.py
  engine.py backtest.py live.py simulate.py
  metrics.py montecarlo.py analysis.py
dashboard/app.py   Streamlit UI
tests/             48 tests: indicators, patterns, aggregation, no-look-ahead, portfolio, Monte Carlo,
                   safety, persistence, crash recovery, backtest/live equivalence
```

Tooling: `ruff check src tests` and `mypy src` both pass cleanly.
