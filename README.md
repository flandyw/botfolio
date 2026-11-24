# Botfolio

Botfolio runs five automated strategies in a single worker and routes every signal to a dedicated Alpaca Paper account as a real paper order. All trading uses paper money; there is no backtester.

## Strategies

| Strategy | Universe | Entry | Exit |
|---|---|---|---|
| **News + AI** | Watchlist stocks and ETFs | Fresh news rated `BUY` or `SELL` by the AI analyst | Stop, profit target, or 15-minute timer |
| **Intraday momentum** | `SPY` and `QQQ` (long or short) | 30-minute close outside its normal time-of-day range on the right side of VWAP | Stop at the far edge of the range, a close back inside the range, or before the close |
| **ETF trend** | Watchlist ETFs (long only) | Daily close above the 200-day moving average | Daily close below the moving average (held overnight) |
| **ETF rotation** | Top 5 watchlist ETFs by average 1-, 3-, 6-, and 12-month return (long only) | Ranked in the top 5 with a positive average return and a close above the 200-day average | Drops out of the top 10, turns negative, or closes below the 200-day average (held overnight) |
| **Parked cash** | Treasury-bill ETF (`BOXX`, long only) | Cash not used by ETF trend or ETF rotation, minus a 5% reserve | Sold and rebought at the new target once allocation drifts by 5% of equity |

### Watchlist and ownership

- `config/default.yaml` defines 125 default symbols (including 26 ETFs; 24 are assigned to the ETF strategies) and per-strategy toggles, position counts, and sizing under `strategies`.
- Because Alpaca nets positions per symbol, only one strategy can hold a symbol at a time. When an ETF enters rotation's top 5, ETF trend exits so rotation can take the larger position.
- `SPY` and `QQQ` are reserved for intraday momentum; the ETF strategies hold `VOO` and `QQQM` instead. Momentum defaults to `SPY` and `QQQ` because broader watchlists lost money after trading costs. Set `strategies.momentum.symbols` to customize the list (or leave it empty to scan the full watchlist).
- Alpaca Paper does not pay dividends, so parked cash uses `BOXX`, which distributes no dividends and reflects Treasury-bill returns in its share price.

## Run locally

```bash
cp .env.example .env
uv sync
uv run botfolio check
uv run botfolio events watch
```

Add your Alpaca Paper credentials and AI analyst settings to `.env` before starting the worker:

- **Primary analyst (Clairic / Cloudflare Workers AI):** Set `EVENT_AI_PROVIDER=clairic`, `EVENT_AI_MODEL=clairic`, and your endpoint and token in `EVENT_AI_URL` and `EVENT_AI_KEY`.
- **Optional secondary analyst (Vercel AI Gateway):** Set `EVENT_AI_SECONDARY_*` to split traffic across a second model with a monthly dollar cap and per-minute rate limit.
- **Experiment tracking:** Update `EVENT_AI_EXPERIMENT` whenever you change provider, model, endpoint, or prompt so saved decisions remain auditable.

To run the web dashboard in a second terminal:

```bash
uv run botfolio web
```

Then start the frontend from `web/`:

```bash
npm ci
npm run dev
```

Open the URL printed by Vite. The dashboard has five pages: **Desk** (account, strategies, positions, recent orders), **Performance** (P&L, returns, drawdown, and open exposure), **Markets** (watchlist table and charts), **News** (saved articles and AI assessments), and **Control** (entry toggle, emergency stop, watchlist editor, AI usage, and order journal).

Run `uv run botfolio events decision-stats` for a summary of saved AI decisions.

## Execution and safety

- **Entries start paused.** Set `EVENT_PAPER_ENABLED=true` and `EVENT_EXCLUSIVE_ACCOUNT=true` for both the worker and `botfolio web`. Once the worker checks in, confirm the dedicated paper account and enable entries on the **Control** page.
- **Rule-based cadence.** Momentum, ETF trend, ETF rotation, and parked cash run every 30 seconds during market hours and do not require an AI analyst. Parked cash waits 30 minutes after the open and resizes at most once per day.
- **News evaluation.** The worker scores fresh watchlist news using market context available at receipt. Clairic must return a supported directional `BUY` or `SELL` before an order is checked. Issuer RSS feeds are recorded in the news audit.
- **Order rules.** Entries require a fresh quote during regular Nasdaq hours and stop five minutes before the close. News orders use limit brackets (stop + profit target) with a 15-minute exit timer; momentum uses limit orders with a stop; ETF trend, ETF rotation, and parked cash use fractional limit orders. Intraday positions (news and momentum) begin closing two minutes before the close; overnight ETF and parked-cash positions stay open.
- **Safety controls.** **Pause entries** stops new trades while the execution monitor continues managing open positions. **Emergency stop** also cancels open orders and closes bot positions during regular hours.
- **Shared state.** SQLite stores the watchlist, armed state, order journal, and decision audit. Point both the worker and `botfolio web` at the same `BOTFOLIO_DB_PATH` and `EVENT_KILL_FILE`.

The worker runs independently of the dashboard. Check its news heartbeat with `uv run botfolio health`, or view the execution monitor status on **Desk**. Paper trading results do not guarantee live profitability.

## Deploy

The systemd units in `deploy/` assume a checkout at `~/botfolio`:

- `botfolio-events.service` runs the paper worker.
- `botfolio-web.service` runs the Python API (`botfolio web`).
- For the frontend, run `npm run build && npm run server` in `web/`.

Set `BOTFOLIO_DASHBOARD_PASSWORD` before exposing the dashboard outside a trusted local or Tailscale network; it protects the **Control** and **News** pages.

Docker Compose runs the worker, Python API, and web dashboard (at <http://127.0.0.1:8601>) with shared state under `./data`.

Run `./restart.sh` to restart the installed systemd services. Rebuild the React app before restarting after frontend changes.

The dashboard's Node server connects directly to Alpaca Paper for account data, prices, and charts, and proxies worker status and controls to `botfolio web`. See [web/README.md](web/README.md) for frontend details.
