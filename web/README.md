# Web dashboard

- **Desk**: the paper account, the five strategies with their last run and orders, positions and recent orders.
- **Performance**: account P&L, returns, and drawdown across selectable periods, plus unrealized P&L and exposure by open position.
- **Markets**: the watchlist as a sortable table beside the chart. Filter it, click a row or use the arrow keys.
- **News**: saved articles with the AI assessment of each.
- **Control**: turn entries on or off, trigger the emergency stop, edit the watchlist and read the order journal.

Run the worker API from the repository root.

```bash
uv run botfolio web
```

Run the dashboard from `web/`.

```bash
npm ci
npm run dev
```

For a built dashboard, run `npm run build` and `npm run server` from `web/`, then open <http://127.0.0.1:8601>. The Node server uses the paper credentials in the repository's `.env` and proxies everything else under `/api` to the worker API on port 8600. Set `BOTFOLIO_API_URL` for a different API address and `HOST` to listen on another interface.

Set `BOTFOLIO_DASHBOARD_PASSWORD` in `.env` to require a password for the Control and News pages. Account, prices and worker status stay readable without it.
