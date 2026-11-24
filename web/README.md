# Web dashboard

The Botfolio dashboard has five pages:

- **Desk**: paper account summary, equity chart, all five strategies with their latest runs, open positions, and recent orders.
- **Performance**: portfolio P&L, returns, and drawdown across selectable timeframes, plus unrealized P&L and exposure by open position.
- **Markets**: sortable watchlist table alongside a live price chart. Filter by symbol, click a row, or step through rows with the arrow keys.
- **News**: saved articles paired with their AI assessments and on-demand evaluation controls.
- **Control**: entry toggle, emergency stop, watchlist editor, AI usage and model routing, and the order journal.

## Development

Start the Python API from the repository root:

```bash
uv run botfolio web
```

Start the Vite dev server from `web/`:

```bash
npm ci
npm run dev
```

## Production

Build and serve the dashboard from `web/`:

```bash
npm run build
npm run server
```

Then open <http://127.0.0.1:8601>.

The Node server reads Alpaca Paper credentials from the repository's `.env` for account and market data, and proxies all other `/api/*` requests to `botfolio web` on port `8600`. Set `BOTFOLIO_API_URL` to point at a different API address, or `HOST` and `PORT` to bind elsewhere.

Set `BOTFOLIO_DASHBOARD_PASSWORD` in `.env` to protect the **Control** and **News** pages. Account, market, and worker status views remain readable without a password.
