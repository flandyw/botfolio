"""Read-only JSON API + static host for the React web UI (web/). Never places orders or changes state.

Chart times are New York wall-clock seconds encoded as if UTC, because lightweight-charts renders
timestamps in UTC: a bar at 09:30 ET shows as 09:30 regardless of the viewer's timezone.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from botfolio.config import AppConfig
from botfolio.data.alpaca import AlpacaClient
from botfolio.data.calendar import get_calendar, ny_date
from botfolio.data.loader import cache_key, load_history
from botfolio.engine import Engine
from botfolio.storage import Storage

NY = "America/New_York"
TIMEFRAMES = {"1": "1min", "5": "5min", "15": "15min", "60": "60min", "D": "D"}
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def _clean(o: Any) -> Any:
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    return o


class JSON(Response):
    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        return json.dumps(_clean(content), default=str, separators=(",", ":")).encode()


def chart_time(ts: Any) -> pd.Series | int:
    """UTC timestamp(s) -> NY wall-clock epoch seconds (see module docstring)."""
    if isinstance(ts, pd.Series):
        local = pd.to_datetime(ts, utc=True).dt.tz_convert(NY).dt.tz_localize(None)
        return (local - pd.Timestamp(0)) // pd.Timedelta(seconds=1)
    local = pd.Timestamp(ts).tz_convert(NY).tz_localize(None)
    return int((local - pd.Timestamp(0)) // pd.Timedelta(seconds=1))


def regular_hours(df: pd.DataFrame, cal: Any) -> pd.DataFrame:
    """Drop pre/post-market bars (the forward loop caches whatever Alpaca returns); honours early closes."""
    if df.empty:
        return df
    ts = pd.to_datetime(df["ts"], utc=True)
    days = ts.dt.tz_convert(NY).dt.date
    keep = pd.Series(False, index=df.index)
    for d in days.unique():
        s = cal.session(d)
        if s is not None:
            keep |= (days == d) & (ts >= s.open) & (ts < s.close)
    return df[keep]


def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    """1-minute bars -> ``tf`` bars, anchored to the 09:30 open (and to the session date for daily)."""
    if df.empty or tf == "1":
        return df
    x = df.set_index(pd.to_datetime(df["ts"], utc=True).dt.tz_convert(NY))
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    if tf == "D":
        out = x.groupby(x.index.date).agg(agg)
        out.index = pd.to_datetime(out.index).tz_localize(NY)
    else:
        out = x.resample(TIMEFRAMES[tf], origin="start_day", offset="9h30min", label="left").agg(agg).dropna()
    return out.reset_index(names="ts")


def create_app(cfg: AppConfig, client: AlpacaClient | None = None) -> Starlette:
    storage = Storage(cfg.db_path)
    cal = get_calendar(cfg.session.calendar)
    snap_cache: dict[str, tuple[str, Engine | None]] = {}

    def engine_snapshot(run_id: str) -> Engine | None:
        meta = storage._conn.execute("SELECT updated_at FROM kv WHERE key=?", (f"engine:{run_id}",)).fetchone()
        if meta is None:
            return None
        cached = snap_cache.get(run_id)
        if cached is None or cached[0] != meta[0]:
            try:
                eng = storage.get(f"engine:{run_id}")
            except Exception:  # snapshot from an older code version
                eng = None
            snap_cache[run_id] = (meta[0], eng if isinstance(eng, Engine) else None)
        return snap_cache[run_id][1]

    async def config(_: Request) -> JSON:
        return JSON({"symbols": cfg.symbols, "starting_capital": cfg.risk.starting_capital,
                     "tax_rate_pct": cfg.tax.marginal_rate_pct, "withholding_pct": cfg.costs.dividend_withholding_pct,
                     "feed": cfg.data.feed})

    async def runs(_: Request) -> JSON:
        df = storage.runs().drop(columns=["config"])
        return JSON(df.astype(object).where(df.notna(), None).to_dict("records"))

    async def stats(req: Request) -> JSON:
        return JSON(storage.stats(req.path_params["run_id"]))

    async def equity(req: Request) -> JSON:
        run_id = req.path_params["run_id"]
        df = storage.equity(run_id)
        out: dict[str, list[dict[str, float]]] = {}
        if not df.empty:
            df = df.assign(time=chart_time(df["ts"])).sort_values("time")
            for name, g in df.groupby("portfolio"):
                out[str(name)] = [{"time": int(t), "value": float(v)} for t, v in zip(g["time"], g["equity"], strict=True)]
        live = storage.get(f"live_equity:{run_id}")
        if live:  # forward runs: append the latest mark-to-market point
            t = chart_time(live["ts"])
            for name, v in live["equity"].items():
                pts = out.setdefault(name, [])
                if not pts or pts[-1]["time"] < t:
                    pts.append({"time": t, "value": float(v)})
        return JSON(out)

    async def trades(req: Request) -> JSON:
        df = storage.trades(req.path_params["run_id"])
        if p := req.query_params.get("portfolio"):
            df = df[df["portfolio"] == p]
        if s := req.query_params.get("symbol"):
            df = df[df["symbol"] == s]
        if df.empty:
            return JSON([])
        df = df.assign(entry_time=chart_time(df["entry_ts"]), exit_time=chart_time(df["exit_ts"]),
                       meta=df["meta"].map(lambda m: json.loads(m) if isinstance(m, str) else {}))
        df = df.drop(columns=["run_id"]).sort_values("entry_ts", ascending=False).head(int(req.query_params.get("limit", 5000)))
        return JSON(df.astype(object).where(df.notna(), None).to_dict("records"))

    async def bars(req: Request) -> JSON:
        q = req.query_params
        symbol, tf = q.get("symbol", cfg.symbols[0]).upper(), q.get("tf", "5")
        if tf not in TIMEFRAMES:
            return JSON({"error": f"tf must be one of {list(TIMEFRAMES)}"}, status_code=400)
        if symbol not in cfg.symbols:
            return JSON({"error": f"symbol must be one of {cfg.symbols}"}, status_code=400)
        days = min(int(q.get("days", 20 if tf in {"1", "5"} else 400)), 30 if tf == "1" else 3000)
        now = datetime.now(UTC)
        try:  # downloads (market data only) and caches any missing sessions
            df = await run_in_threadpool(load_history, client, storage, cal, [symbol], ny_date(now) - timedelta(days=days),
                                         ny_date(now), cfg.data.feed, cfg.data.adjustment)
        except Exception:  # no credentials / Alpaca down: serve what is cached
            df = storage.load_bars([symbol], cache_key(cfg.data.feed, cfg.data.adjustment), now - timedelta(days=days), now)
        if not df.empty:  # include today's bars cached by the forward loop (load_history skips the open session)
            today = storage.load_bars([symbol], cache_key(cfg.data.feed, cfg.data.adjustment),
                                      pd.Timestamp(df["ts"].max()).to_pydatetime() + timedelta(seconds=1), now)
            df = pd.concat([df, today], ignore_index=True)
        df = resample(regular_hours(df, cal), tf)
        if df.empty:
            return JSON([])
        df = df.assign(time=chart_time(df["ts"]))
        return JSON(df[["time", "open", "high", "low", "close", "volume"]].to_dict("records"))

    async def live(req: Request) -> JSON:
        run_id = req.query_params.get("run_id", "forward")
        now = datetime.now(UTC)
        hb = storage.get("heartbeat")
        sess = cal.session(ny_date(now))
        positions = []
        tax_due: dict[str, float] = {}
        eng = engine_snapshot(run_id)
        if eng is not None:
            for name, p in eng.portfolios.items():
                tax_due[name] = p.tax_due()
                for sym, pos in p.positions.items():
                    last = p.last_price.get(sym, pos.entry_ref)
                    positions.append({
                        "portfolio": name, "symbol": sym, "side": pos.side, "qty": pos.qty,
                        "entry_price": pos.entry_fill, "entry_time": chart_time(pos.entry_ts), "last": last,
                        "stop": pos.stop, "target": pos.target, "dividends": pos.dividends,
                        "unrealised": pos.side * pos.qty * (last - pos.entry_fill) - pos.entry_fees + pos.dividends,
                    })
        return JSON({
            "now": now.isoformat(), "heartbeat": hb,
            "heartbeat_age_s": (now - datetime.fromisoformat(hb)).total_seconds() if hb else None,
            "market_open": bool(sess and sess.open <= now < sess.close),
            "session": {"open": sess.open.isoformat(), "close": sess.close.isoformat()} if sess else None,
            "equity": (storage.get(f"live_equity:{run_id}") or {}).get("equity", {}),
            "positions": positions, "tax_due": tax_due,
        })

    routes: list[Any] = [
        Route("/api/config", config), Route("/api/runs", runs), Route("/api/runs/{run_id}/stats", stats),
        Route("/api/runs/{run_id}/equity", equity), Route("/api/runs/{run_id}/trades", trades),
        Route("/api/bars", bars), Route("/api/live", live),
    ]
    if WEB_DIST.is_dir():
        routes.append(Mount("/", StaticFiles(directory=WEB_DIST, html=True)))
    return Starlette(routes=routes)
