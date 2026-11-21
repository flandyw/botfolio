<img src="assets/icon.svg" width="64" height="64" alt="">

# Botfolio: paper-trading bots that have to beat a coin flip

A fully automated, zero-subscription-cost research bot that **paper-trades** strategies on liquid US ETFs: **QQQ**
(NASDAQ-100), **SPY** (S&P 500), **VGT** and **XLK** (US tech sector), **SOXX** (semiconductors) and **VUG**
(large-cap growth). Each strategy runs in its own **$100,000 virtual account**. It measures
whether a strategy has a statistically significant edge **after trading costs**, against:

* an **inverse-signal** twin of every strategy (takes the exact opposite trade),
* a **random-entry** baseline (same exits, same costs), and a 2,000-draw Monte-Carlo random-entry null,
* **buy-and-hold**.

Strategies: a long-only ETF **trend-filtered hold** (own the ETF above its 200-day average, cash below),
intraday noise-band momentum (Zarattini, Aziz & Barbon, 2024), and the news/earnings event pipeline described
[below](#news-and-earnings-research). Holding strategies receive **cash dividends** on each ex-date (net of an
optional withholding tax), so they are compared with buy-and-hold on a total-return basis. Add your own in `src/botfolio/strategies/`.

> **Paper trading only.** No code path can place a real-money order. See [Safety](#safety-no-live-trading).

---

## Quick start (local)

Requires [uv](https://docs.astral.sh/uv/) (`brew install uv` or `curl -LsSf https://astral.sh/uv/install.sh | sh`)
and free Alpaca **paper** API keys
([dashboard → Paper → API keys](https://app.alpaca.markets/paper/dashboard/overview)). uv installs the right
Python (3.12, pinned in `.python-version`) and the exact locked dependencies from `uv.lock`.

```bash
cp .env.example .env            # put ALPACA_KEY / ALPACA_SECRET (paper keys) in it
uv sync                         # creates .venv with all deps (incl. dev tools)

uv run botfolio check                                         # verifies paper account + market-data access
uv run botfolio backtest --start 2025-11-17 --end 2026-11-13  # downloads + caches bars, prints the ranking
uv run botfolio backtest --start 2025-11-17 --end 2026-11-13 --feed sip --run-id sip-1y
uv run botfolio simulate --day 2026-11-13                     # local simulated execution test (see below)
uv run botfolio forward                                       # forward paper trading, runs forever
uv run botfolio dashboard                                     # http://localhost:8501
uv run botfolio report [--run-id ID]                          # list runs / print a ranking
uv run pytest                                                 # unit + integration tests
uv run ruff check src tests && uv run mypy src             # lint + type check
```

On a Mac, keep it awake while forward trading with `caffeinate -i uv run botfolio forward`. US regular hours
(09:30–16:00 ET) are overnight in Australia/Asia.

`botfolio backtest --synthetic …` runs on a random-walk dataset with no network access. Every strategy *must*
lose about its costs there; a "winning" strategy on synthetic data would reveal look-ahead bias.

### Configuration

* `config/default.yaml`: symbols, timeframes, strategy roster and parameters, cost model, risk limits,
  exit rules, seed. `config/low_cost.yaml` is the optimistic-cost sensitivity scenario
  (`uv run botfolio --config config/low_cost.yaml backtest …`).
* Environment variables (`.env`): `ALPACA_KEY`, `ALPACA_SECRET` (or `APCA_API_KEY_ID`,
  `APCA_API_SECRET_KEY`); optional `BOTFOLIO_DB_PATH`, `BOTFOLIO_CONFIG`, `BOTFOLIO_LOG_LEVEL`, `BOTFOLIO_LOG_JSON`,
  `BOTFOLIO_DATA_FEED` (`iex`|`sip`), `BOTFOLIO_SYMBOLS`, `BOTFOLIO_MIRROR_STRATEGY`.
  Secrets never go in YAML and are never logged.

Key defaults, identical for **every** portfolio:

| | |
|---|---|
| capital | $100,000 per strategy |
| sizing | risk 0.5% of equity per trade (stop distance), max 100% notional (no leverage) |
| risk limits | ≤ 2 open positions, ≤ 10 trades/day, stop opening trades after −2% on the day |
| stop | the strategy's own invalidation level, clamped to [0.5, 3] × ATR(14); ATR × 1.5 if none |
| target | 2R · time stop 60 bars · flat at the close · no entries in the last 15 min |
| costs | half of a 1¢ spread + 1 bp slippage per market order, +2 bp on stops, SEC + FINRA TAF fees on sells, Interactive Brokers (AU) commission US$0.005/share, US$1 minimum |
| tax | Australian resident at 32% (30% bracket + Medicare): realised gains taxed each 1 Jul–30 Jun year, 50% CGT discount on longs held > 12 months, losses offset gains then carry forward; dividends taxed at 32% with the 15% US withholding credited. Equity is shown after tax owed so far; unrealised gains are untaxed |
| dividends | prices are split-adjusted only; cash dividends (Alpaca corporate actions) are credited to longs held over the ex-date, minus `dividend_withholding_pct`, and charged to shorts |
| ETF fees | expense ratios are already deducted from the ETF's price, so they need no separate charge |

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
  **next** 1-minute bar's open. 5-minute bars are only emitted once complete. `tests/test_no_lookahead.py` re-runs every strategy on
  truncated histories and requires identical past signals.
* **Realistic fills.** Gaps through a stop fill at the open. When a bar touches both stop and target, the
  stop is assumed first. Targets are limit orders that need price to trade *through* them. Exits are
  checked on 1-minute bars even for 5-minute strategies.
* **Market hours.** Only regular-session bars (09:30–16:00 ET) are used. Holidays and early closes come
  from `pandas-market-calendars` (XNYS). Intraday positions are flattened on the session's final bar.
* **Backtest = forward.** `tests/test_engine_live.py` proves the polling forward runner produces exactly
  the same trades as the backtester for a session.

Exact strategy definitions are in **[docs/STRATEGIES.md](docs/STRATEGIES.md)**.

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

`botfolio dashboard` (or the `dashboard` container) shows the following for any run (backtest, simulation or forward):
KPIs; a **ranking table** (win rate, profit factor, Sharpe, max DD, p/q-values, verdict); net return per
trade for strategy vs inverse vs random; **equity and drawdown curves**; a **significance chart** (bootstrap
CI vs the random-entry band); **executed trades** with CSV export; and **live status** (heartbeat,
mark-to-market equity, latest signals). The "How to read this" tab explains every number.

## Web UI (React)

`web/` is a React + TypeScript app built with [Vite+](https://viteplus.dev) and charted with TradingView
[lightweight-charts](https://github.com/tradingview/lightweight-charts). `botfolio web` serves the built app and
a read-only JSON API (`/api/*`, `src/botfolio/webapi.py`). It cannot place orders or change settings.

* **Overview**: return and drawdown curves after costs and tax (buy-and-hold dashed, same colour per ETF),
  and a sortable ranking that also toggles chart lines.
* **Chart**: candles (1m to 1D) with the selected portfolio's entries and exits marked.
* **Trades**: filterable trade log with CSV export. **Live**: forward-bot health, open positions, live
  equity and tax accrued this financial year.

```bash
cd web && npm install && npm run build && cd ..   # build once after changing web/
uv run botfolio web                                # http://127.0.0.1:8600
cd web && npm run dev                              # hot-reload UI on :5173, proxies /api to :8600
```

On the VPS it runs as the `botfolio-web` user service (`deploy/botfolio-web.service`) and is published to the
tailnet only with `sudo tailscale serve --bg --https=8443 http://127.0.0.1:8600` (port 8443 because nginx owns 443
on this host).

## Forward paper trading & crash recovery

`botfolio forward` polls Alpaca once a minute (10 s after the minute closes), processes only completed bars,
and persists trades, signals, equity, a heartbeat and a **pickled engine snapshot** to SQLite after every
cycle.

* **On a fresh start** it warms indicators on the previous 3 sessions, then starts all
  portfolios from clean capital (warm-up trades are discarded).
* **After a crash or restart** it restores the snapshot and catches up on missed minutes (up to 7 days).
  Duplicate bars are ignored, and trade IDs are deterministic, so nothing is double-counted.
  `botfolio simulate` replays a real session minute by minute, kills the runner midway, recovers it, and checks
  the result is **identical** to an uninterrupted run.
* If the code or config changes incompatibly, the snapshot is discarded and the run restarts with fresh
  portfolios.
* Docker's `restart: unless-stopped` plus a heartbeat **health check** handle process crashes.

**Optional Alpaca paper mirror:** set `BOTFOLIO_MIRROR_STRATEGY=noise_mom_1m` (any portfolio name) to also send that
one portfolio's entries and exits as market orders to your Alpaca **paper** account, so you can watch them in
Alpaca's UI. Only one portfolio can be mirrored, because one Alpaca account cannot hold many independent books.
The local simulation remains the source of truth for all statistics.

## Deploy on an Ubuntu VPS (Docker Compose)

```bash
# once: install Docker Engine + compose plugin (https://docs.docker.com/engine/install/ubuntu/)
git clone <your repo> botfolio && cd botfolio
cp .env.example .env && nano .env              # paper keys
mkdir -p data && sudo chown 1000:1000 data     # containers run as uid 1000
docker compose up -d --build                   # starts bot + dashboard
docker compose logs -f bot                     # structured JSON logs
docker compose ps                              # health status

# historical backtest inside the stack (results appear in the dashboard):
docker compose run --rm backtest botfolio backtest --start 2025-11-17 --end 2026-11-13

# dashboard is bound to 127.0.0.1 - open it through an SSH tunnel:
ssh -L 8501:localhost:8501 user@your-vps   # then browse http://localhost:8501
```

All state lives in `./data/botfolio.sqlite` (back it up with `sqlite3 data/botfolio.sqlite ".backup b.sqlite"`).
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
  must use IEX. 
* **Fills are simulated.** The model has no queue position, partial fills or market impact, and uses a
  fixed spread/slippage assumption. Short-borrow fees are ignored (intraday only, easy-to-borrow ETFs).
  The SEC fee rate changes yearly; adjust `sec_fee_per_million`.
* **Tax is a simplified Australian model** (`tax:` in the YAML). It assumes the investor is not a "share
  trader" (whose gains are income with no CGT discount, and whose losses offset salary). It ignores tax
  brackets moving with income, and pays tax at 30 June rather than at assessment. Buy-and-hold owes deferred
  CGT if sold at the end. Accounts are in USD: AUD/USD moves and FX conversion fees (IBKR ≈ 0.002%, most
  AU platforms ≈ 0.5–0.7% each way) are not modelled. Idle cash earns 0%, which
  understates a trend filter that would park cash in a T-bill ETF.
* **Bar-level ambiguity.** Intrabar order is unknown, so a bar that touches both stop and target is
  counted as a loss (conservative).
* **Tuning many variants on the same data will produce false positives**, so judge only by q-values and
  out-of-sample (forward) results.
* **One year is one market regime.** Run longer periods (Alpaca history goes back to 2016) and keep the
  forward loop running.
* The random-entry null uses the strategy's median stop size, while real strategies vary their stops, so
  the verdict requires **both** the t-test and the random-entry test to pass.

## Project layout

```
src/botfolio/
  config.py safety.py logging_setup.py storage.py cli.py
  data/        models, NYSE calendar, Alpaca client, cached loader, 1m→5m aggregator, synthetic data
  indicators.py  streaming Wilder ATR
  strategies/  trend.py (ETF trend hold), momentum.py (noise-band momentum), baselines.py (random, buy-and-hold, inverse)
  events/      news/earnings ingestion, AI decisions, replay, paper execution
  execution/   costs.py, portfolio.py
  engine.py backtest.py live.py simulate.py
  metrics.py montecarlo.py analysis.py
dashboard/app.py   Streamlit UI
tests/             indicators, aggregation, no-look-ahead, portfolio, Monte Carlo,
                   safety, persistence, crash recovery, backtest/live equivalence
```

Tooling: `ruff check src tests` and `mypy src` both pass cleanly.

## News and earnings research

`botfolio events` adds immutable event ingestion, configurable structured AI decisions, conservative
minute-bar replay with paired random controls, optional LightGBM walk-forward evaluation and guarded
paper execution. See [setup, scientific limits and commands](docs/EVENT_RESEARCH.md).
Paper execution starts disabled. This pipeline has not demonstrated a profitable edge.

The dashboard opens on **Start here**, with connection checks, a news collection button and a
practice demo that needs no credentials. Use **News & AI** for readable article assessments,
**Results** for explained comparisons, and **Help** for a glossary. Detailed statistics and database
settings remain available under **Advanced research**. Dashboard controls do not submit orders.

**Paper trading** now shows the market session, execution-monitor health, entry pause/enable,
emergency stop, editable news watchlist and broker journal. Entries require fresh news, an eligible
AI hypothesis, risk checks and both the Nasdaq calendar and Alpaca clock to be open. Restartable VPS
user services are provided in `deploy/`; see the operational update in [the event guide](docs/EVENT_RESEARCH.md).
