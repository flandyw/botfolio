# Botfolio

Botfolio runs five strategies in one worker and sends every signal to a dedicated Alpaca Paper account as a real paper order. All execution uses paper money.

| Strategy | Trades | Entry | Exit |
|---|---|---|---|
| News + AI | Watchlist stocks and ETFs | Fresh news scored `BUY` or `SELL` by the AI provider | Stop, profit target or 15-minute timer |
| Intraday momentum | `SPY` and `QQQ`, long and short | 30-minute close outside its usual range for that time of day, on the right side of VWAP | Stop at the far side of the range, a close back inside it, or before the close |
| ETF trend | ETFs in the watchlist, long only | Daily close above the 200-day average | Daily close below the average; held overnight |
| ETF rotation | The 5 watchlist ETFs with the highest average 1, 3, 6 and 12-month return, long only | In the top 5, with a positive average return and a close above the 200-day average | Out of the top 10, a negative average return, or a close below the 200-day average; held overnight |
| Parked cash | One Treasury-bill ETF (`BOXX`), long only | Equity that ETF trend and ETF rotation are not using, less a 5% cash reserve | Sold and bought again at the new size once it is 5% of equity away from that amount |

The default watchlist in `config/default.yaml` has 125 symbols, 26 of them ETFs. `strategies` in the same file turns each strategy on or off and sets its position count and size. A symbol is held by one strategy at a time because Alpaca nets positions per symbol, so ETF trend sells an ETF that enters the rotation's top 5 and ETF rotation buys it at its larger size. For the same reason the ETF strategies leave `SPY` and `QQQ` to intraday momentum and hold `VOO` and `QQQM`, which track the same indexes. Momentum is limited to those two because a backtest of all 124 symbols lost money after trading costs; `strategies.momentum.symbols` changes the list, and an empty list means the whole watchlist. Alpaca Paper does not pay dividends, which is why the parked cash uses `BOXX`: it pays none and its Treasury-bill return shows in its price. There is no backtester.

## Run locally

```bash
cp .env.example .env
uv sync
uv run botfolio check
uv run botfolio events watch
```

Add your Alpaca Paper credentials and AI provider settings to `.env` before starting the worker. For Clairic, set `EVENT_AI_PROVIDER=clairic`, `EVENT_AI_MODEL=clairic` and the Cloudflare Workers AI endpoint and token in `EVENT_AI_URL` and `EVENT_AI_KEY`. Set `EVENT_AI_EXPERIMENT` to a new ID when changing provider, model, endpoint or prompt so decisions remain auditable.

In another terminal, run `uv run botfolio web`, then `npm ci && npm run dev` in `web/`, and open the address it prints. The **Control** page shows the order journal, edits the watchlist (up to 500 symbols) and provides entry and emergency-stop controls. **News** shows saved articles and the AI assessment of each. **Desk** and **Markets** show the account, strategies, positions and charts. Use `uv run botfolio events decision-stats` for a summary of those decisions.

## Execution

- Entry begins paused. Set `EVENT_PAPER_ENABLED=true` and `EVENT_EXCLUSIVE_ACCOUNT=true` for both the worker and `botfolio web`. Confirm the dedicated paper account and enable entries on the Control page after the worker checks in.
- Momentum, ETF trend, ETF rotation and parked cash are checked every 30 seconds during the session and need no AI provider. The parked cash is resized at most once a day and waits for the first 30 minutes of the session.
- The worker scores fresh watchlist news with market data available at receipt. Clairic needs a supported directional `BUY` or `SELL` before a signal can reach the order checks. Issuer RSS is available in the news audit.
- Entries require a fresh quote and the regular Nasdaq session. News orders are limit orders with a stop and profit target, momentum orders are limit orders with a stop, and ETF trend, ETF rotation and parked cash orders are fractional limit orders. The entry window ends five minutes before close. The news exit timer is 15 minutes. News and momentum positions begin closing two minutes before the session closes; ETF trend, ETF rotation and parked cash positions stay open.
- **Pause entries** keeps the execution monitor managing existing positions. **Emergency stop** also asks the monitor to cancel its orders and close its positions during regular hours.
- SQLite stores the watchlist, arm state, journal, and decision audit. Use the same `BOTFOLIO_DB_PATH` and `EVENT_KILL_FILE` for both processes. The existing database is compatible, including an existing armed state.

The worker is supervised separately from the dashboard. `uv run botfolio health` checks its news heartbeat. The Desk page also shows the independent execution monitor status. Paper results do not establish profitability.

## Deploy

The systemd units in `deploy/` assume a checkout at `~/botfolio`. Use `botfolio-events.service` for the worker and `botfolio-web.service` for the dashboard's API; run the dashboard itself with `npm run build && npm run server` in `web/`. Set `BOTFOLIO_DASHBOARD_PASSWORD` before sharing the dashboard outside a trusted local or Tailscale connection: it protects the Control and News pages. Docker Compose runs the worker, the API and the dashboard (on <http://127.0.0.1:8601>) with shared state under `./data`.

Run `./restart.sh` to restart the systemd paper worker and the installed dashboard services. For React changes, build the web app before restarting.

The dashboard's Node server connects to Alpaca Paper for the account, prices and charts, and uses `botfolio web` for worker status and controls. [React dashboard setup](web/README.md)
