# Botfolio

Botfolio watches company news, evaluates it with a configured AI provider and submits eligible orders to a dedicated Alpaca Paper account. All execution uses paper money.

## Run locally

```bash
cp .env.example .env
uv sync
uv run botfolio check
uv run botfolio events watch
```

Add your Alpaca Paper credentials and AI provider settings to `.env` before starting the worker. For Clairic, set `EVENT_AI_PROVIDER=clairic`, `EVENT_AI_MODEL=clairic` and the Cloudflare Workers AI endpoint and token in `EVENT_AI_URL` and `EVENT_AI_KEY`. Set `EVENT_AI_EXPERIMENT` to a new ID when changing provider, model, endpoint or prompt so decisions remain auditable.

In another terminal, run `uv run botfolio dashboard` and open <http://127.0.0.1:8501>. The **Paper trading** page shows the worker status and order journal, edits the news watchlist, and provides entry and emergency-stop controls. **News & AI** shows saved evidence and decisions. Use `uv run botfolio events decision-stats` for a summary of those decisions.

## Execution

- Entry begins paused. Set `EVENT_PAPER_ENABLED=true` and `EVENT_EXCLUSIVE_ACCOUNT=true` on both the worker and control room. Confirm the dedicated paper account and arm entries in the control room after the worker checks in.
- The worker scores fresh watchlist news with market data available at receipt. Clairic needs a supported directional `BUY` or `SELL` before a signal can reach the order checks. Issuer RSS is available in the news audit.
- Entries require a fresh quote and the regular Nasdaq session. The worker submits a limit order with a stop and profit target. The entry window ends five minutes before close. The default exit timer is 15 minutes and is shortened as needed to begin closing two minutes before the session closes.
- **Pause new entries** keeps the execution monitor managing existing positions. **Emergency stop** also asks the monitor to cancel its orders and close its positions during regular hours.
- SQLite stores the watchlist, arm state, journal, and decision audit. Use the same `BOTFOLIO_DB_PATH` and `EVENT_KILL_FILE` for both processes. The existing database is compatible, including an existing armed state.

The worker is supervised separately from the dashboard. `uv run botfolio health` checks its news heartbeat. The control room also shows the independent execution monitor status. Paper results do not establish profitability.

## Deploy

The systemd units in `deploy/` assume a checkout at `~/botfolio`. Use `botfolio-events.service` for the worker and `botfolio-dashboard.service` for the control room. Set `BOTFOLIO_DASHBOARD_PASSWORD` before sharing the control room outside a trusted local connection. Docker Compose runs the same worker and control room with shared state under `./data`.

Run `./restart.sh` to restart the systemd paper worker and the installed dashboards. For React changes, build the web app before restarting.

A read-only React dashboard shows Alpaca account equity, orders, positions and live prices. Its Node server connects to Alpaca Paper and uses `botfolio web` for local worker status. [React dashboard setup](web/README.md)
