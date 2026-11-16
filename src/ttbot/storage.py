"""SQLite persistence: bar cache, runs, trades, signals, equity, statistics and engine snapshots."""

from __future__ import annotations

import json
import pickle
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from ttbot.execution.portfolio import EquityPoint, Trade

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL, feed TEXT NOT NULL, ts INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, feed, ts)
);
CREATE TABLE IF NOT EXISTS bar_days (
    symbol TEXT NOT NULL, feed TEXT NOT NULL, day TEXT NOT NULL,
    PRIMARY KEY (symbol, feed, day)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, mode TEXT NOT NULL, created_at TEXT NOT NULL,
    start TEXT, end TEXT, status TEXT, config TEXT, notes TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    run_id TEXT NOT NULL, trade_id TEXT NOT NULL, portfolio TEXT NOT NULL, symbol TEXT NOT NULL,
    side INTEGER, qty INTEGER, entry_ts TEXT, entry_price REAL, exit_ts TEXT, exit_price REAL,
    gross_pnl REAL, costs REAL, net_pnl REAL, return_pct REAL, r_multiple REAL, exit_reason TEXT,
    meta TEXT,
    PRIMARY KEY (run_id, trade_id)
);
CREATE INDEX IF NOT EXISTS ix_trades_run_port ON trades(run_id, portfolio);
CREATE TABLE IF NOT EXISTS signals (
    run_id TEXT NOT NULL, portfolio TEXT NOT NULL, symbol TEXT NOT NULL, ts TEXT NOT NULL,
    side INTEGER, price REAL, stop REAL, atr REAL, meta TEXT,
    PRIMARY KEY (run_id, portfolio, symbol, ts)
);
CREATE TABLE IF NOT EXISTS equity (
    run_id TEXT NOT NULL, portfolio TEXT NOT NULL, ts TEXT NOT NULL,
    equity REAL, cash REAL, open_positions INTEGER,
    PRIMARY KEY (run_id, portfolio, ts)
);
CREATE TABLE IF NOT EXISTS strategy_stats (
    run_id TEXT NOT NULL, portfolio TEXT NOT NULL, stats TEXT NOT NULL,
    PRIMARY KEY (run_id, portfolio)
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY, value BLOB, updated_at TEXT
);
"""


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(UTC).isoformat() if ts else None


class Storage:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._lock = threading.RLock()
        with self.tx() as c:
            c.executescript(SCHEMA)

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------------- bars
    def fetched_days(self, symbol: str, feed: str) -> set[date]:
        rows = self._conn.execute("SELECT day FROM bar_days WHERE symbol=? AND feed=?", (symbol, feed)).fetchall()
        return {date.fromisoformat(r[0]) for r in rows}

    def save_bars(self, df: pd.DataFrame, feed: str, complete_days: Iterable[tuple[str, date]] = ()) -> None:
        rows = [
            (r.symbol, feed, int(pd.Timestamp(r.ts).timestamp()), r.open, r.high, r.low, r.close, r.volume)
            for r in df.itertuples(index=False)
        ]
        with self.tx() as c:
            c.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?,?,?,?)", rows)
            c.executemany(
                "INSERT OR IGNORE INTO bar_days VALUES (?,?,?)",
                [(s, feed, d.isoformat()) for s, d in complete_days],
            )

    def load_bars(self, symbols: list[str], feed: str, start: datetime, end: datetime) -> pd.DataFrame:
        q = (
            "SELECT symbol, ts, open, high, low, close, volume FROM bars WHERE feed=? AND ts>=? AND ts<? "
            f"AND symbol IN ({','.join('?' * len(symbols))}) ORDER BY ts, symbol"
        )
        df = pd.read_sql_query(q, self._conn, params=[feed, int(start.timestamp()), int(end.timestamp()), *symbols])
        df["ts"] = pd.to_datetime(df["ts"], unit="s", utc=True)
        return df

    # ---------------------------------------------------------------- runs
    def create_run(self, run_id: str, mode: str, config: dict[str, Any], start: str | None, end: str | None) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR IGNORE INTO runs VALUES (?,?,?,?,?,?,?,?)",
                (run_id, mode, datetime.now(UTC).isoformat(), start, end, "running", json.dumps(config), None),
            )

    def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        with self.tx() as c:
            c.execute(f"UPDATE runs SET {cols} WHERE run_id=?", (*fields.values(), run_id))

    def delete_run(self, run_id: str) -> None:
        with self.tx() as c:
            for t in ("trades", "signals", "equity", "strategy_stats", "runs"):
                c.execute(f"DELETE FROM {t} WHERE run_id=?", (run_id,))

    def runs(self) -> pd.DataFrame:
        return pd.read_sql_query("SELECT * FROM runs ORDER BY created_at DESC", self._conn)

    # --------------------------------------------------------- run outputs
    def save_trades(self, run_id: str, trades: Iterable[Trade]) -> None:
        rows = [
            (
                run_id, t.trade_id, t.portfolio, t.symbol, t.side, t.qty, _iso(t.entry_ts), t.entry_price,
                _iso(t.exit_ts), t.exit_price, t.gross_pnl, t.costs, t.net_pnl, t.return_pct, t.r_multiple,
                t.exit_reason, json.dumps(t.meta, default=str),
            )
            for t in trades
        ]
        with self.tx() as c:
            c.executemany("INSERT OR REPLACE INTO trades VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def save_signals(self, run_id: str, signals: Iterable[dict[str, Any]]) -> None:
        rows = [
            (run_id, s["portfolio"], s["symbol"], _iso(s["ts"]), s["side"], s["price"], s["stop"], s["atr"],
             json.dumps(s["meta"], default=str))
            for s in signals
        ]
        with self.tx() as c:
            c.executemany("INSERT OR REPLACE INTO signals VALUES (?,?,?,?,?,?,?,?,?)", rows)

    def save_equity(self, run_id: str, points: Iterable[EquityPoint]) -> None:
        rows = [(run_id, p.portfolio, _iso(p.ts), p.equity, p.cash, p.open_positions) for p in points]
        with self.tx() as c:
            c.executemany("INSERT OR REPLACE INTO equity VALUES (?,?,?,?,?,?)", rows)

    def save_stats(self, run_id: str, stats: dict[str, dict[str, Any]]) -> None:
        with self.tx() as c:
            c.executemany(
                "INSERT OR REPLACE INTO strategy_stats VALUES (?,?,?)",
                [(run_id, k, json.dumps(v, default=float)) for k, v in stats.items()],
            )

    def trades(self, run_id: str) -> pd.DataFrame:
        df = pd.read_sql_query("SELECT * FROM trades WHERE run_id=? ORDER BY exit_ts", self._conn, params=[run_id])
        for col in ("entry_ts", "exit_ts"):
            df[col] = pd.to_datetime(df[col], utc=True, format="ISO8601")
        return df

    def equity(self, run_id: str) -> pd.DataFrame:
        df = pd.read_sql_query("SELECT * FROM equity WHERE run_id=? ORDER BY ts", self._conn, params=[run_id])
        df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
        return df

    def signals(self, run_id: str, limit: int = 5000) -> pd.DataFrame:
        df = pd.read_sql_query(
            "SELECT * FROM signals WHERE run_id=? ORDER BY ts DESC LIMIT ?", self._conn, params=[run_id, limit]
        )
        df["ts"] = pd.to_datetime(df["ts"], utc=True, format="ISO8601")
        return df

    def stats(self, run_id: str) -> dict[str, dict[str, Any]]:
        rows = self._conn.execute("SELECT portfolio, stats FROM strategy_stats WHERE run_id=?", (run_id,)).fetchall()
        return {r[0]: json.loads(r[1]) for r in rows}

    # ------------------------------------------------------------------ kv
    def put(self, key: str, value: Any) -> None:
        blob = pickle.dumps(value, protocol=pickle.HIGHEST_PROTOCOL)
        with self.tx() as c:
            c.execute("INSERT OR REPLACE INTO kv VALUES (?,?,?)", (key, blob, datetime.now(UTC).isoformat()))

    def get(self, key: str) -> Any | None:
        row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return pickle.loads(row[0]) if row else None

    def get_meta(self, key: str) -> tuple[Any, str] | None:
        row = self._conn.execute("SELECT value, updated_at FROM kv WHERE key=?", (key,)).fetchone()
        return (pickle.loads(row[0]), row[1]) if row else None
