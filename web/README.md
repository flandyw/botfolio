# Read-only paper dashboard

**Desk** shows the paper account, the three strategies with their last run and orders, positions and recent orders. **Markets** shows the watchlist as a sortable table beside the chart; filter it, click a row or use the arrow keys.

Run the worker status API from the repository root.

```bash
uv run botfolio web
```

Run the dashboard from `web/`.

```bash
npm ci
npm run dev
```

For a built dashboard, run `npm run build` and `npm run server` from `web/`, then open <http://127.0.0.1:8601>. The Node server uses the paper credentials in the repository's `.env` and proxies worker status to port 8600. Set `BOTFOLIO_API_URL` for a different worker status API address.

Use the Streamlit control room to edit the watchlist, arm or pause orders, and trigger an emergency stop.
