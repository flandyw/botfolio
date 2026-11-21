/**
 * Standalone Node server for Botfolio web UI:
 * - Serves `/api/alpaca/*` via `@alpacahq/alpaca-trade-api` (paper-only, read-only)
 * - Proxies `/api/*` (SQLite runs, stats, equity, trades, live, bars) to `botfolio web`
 * - Serves the built SPA from `web/dist`
 *
 * Usage: `npm run server` (after `npm run build`)
 */
import * as fs from "node:fs";
import * as http from "node:http";
import * as path from "node:path";
import { fileURLToPath } from "node:url";
import { createAlpacaBridge } from "./alpaca-bridge.ts";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DIST_DIR = path.resolve(__dirname, "..", "dist");
const PORT = Number(process.env.PORT ?? 8601);
const PYTHON_API = process.env.BOTFOLIO_API_URL ?? "http://127.0.0.1:8600";

const MIME: Record<string, string> = {
  ".html": "text/html; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".ico": "image/x-icon",
};

const bridge = createAlpacaBridge();

function proxyToPython(req: http.IncomingMessage, res: http.ServerResponse): void {
  const target = new URL(req.url ?? "/", PYTHON_API);
  const proxyReq = http.request(
    target,
    { method: req.method, headers: { ...req.headers, host: target.host } },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
      proxyRes.pipe(res);
    },
  );
  proxyReq.on("error", (err) => {
    res.writeHead(502, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: `Python API unreachable (${PYTHON_API}): ${err.message}` }));
  });
  req.pipe(proxyReq);
}

function serveStatic(req: http.IncomingMessage, res: http.ServerResponse): void {
  const urlPath = new URL(req.url ?? "/", "http://127.0.0.1").pathname;
  const safePath = path.normalize(urlPath).replace(/^(\.\.(\/|\\|$))+/, "");
  let filePath = path.join(DIST_DIR, safePath === "/" ? "index.html" : safePath);
  if (!fs.existsSync(filePath) || fs.statSync(filePath).isDirectory()) {
    filePath = path.join(DIST_DIR, "index.html");
  }
  if (!fs.existsSync(filePath)) {
    res.writeHead(404, { "Content-Type": "text/plain" });
    res.end("web/dist not built — run `npm run build` first.");
    return;
  }
  const ext = path.extname(filePath);
  res.writeHead(200, { "Content-Type": MIME[ext] ?? "application/octet-stream" });
  fs.createReadStream(filePath).pipe(res);
}

const server = http.createServer(async (req, res) => {
  if (await bridge.handle(req, res)) return;
  if ((req.url ?? "").startsWith("/api/")) {
    proxyToPython(req, res);
    return;
  }
  serveStatic(req, res);
});

server.listen(PORT, "127.0.0.1", () => {
  console.log(`Botfolio Alpaca JS server listening on http://127.0.0.1:${PORT} (API proxy -> ${PYTHON_API})`);
});

process.on("SIGINT", () => {
  bridge.close();
  server.close();
  process.exit(0);
});
