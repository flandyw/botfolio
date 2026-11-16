"""Command-line entry point: ``ttbot {check,backtest,forward,simulate,report,dashboard}``."""

from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

from ttbot.analysis import ranking_table
from ttbot.config import AppConfig, load_config, load_credentials
from ttbot.data.alpaca import AlpacaClient
from ttbot.logging_setup import setup_logging
from ttbot.safety import check_environment
from ttbot.storage import Storage

log = logging.getLogger("ttbot")

SUMMARY_COLS = ["rank", "group", "n_trades", "win_rate", "profit_factor", "avg_return_pct", "avg_cost_pct",
                "total_return_pct", "sharpe", "max_drawdown_pct", "p_ttest", "p_mc", "q_mc", "verdict"]


def print_summary(stats: dict) -> None:
    df = ranking_table(stats)
    if df.empty:
        print("no results")
        return
    cols = [c for c in SUMMARY_COLS if c in df.columns]
    with pd.option_context("display.max_rows", 200, "display.width", 250, "display.float_format", "{:,.3f}".format):
        print(df[cols].to_string())


def _client(required: bool = True) -> AlpacaClient | None:
    creds = load_credentials(required=required)
    return AlpacaClient(creds) if creds else None


def cmd_check(cfg: AppConfig, args: argparse.Namespace) -> int:
    client = _client()
    assert client is not None
    acct = client.get_paper_account()
    print(f"paper account OK: {acct['account_number']} status={acct['status']} equity={acct['equity']}")
    end = datetime.now(UTC) - timedelta(minutes=20)
    df = client.get_bars(cfg.symbols, end - timedelta(days=5), end, "1Min", cfg.data.feed)
    print(f"market data OK: {len(df)} 1m bars over the last 5 days (feed={cfg.data.feed})")
    return 0


def cmd_backtest(cfg: AppConfig, args: argparse.Namespace) -> int:
    from ttbot.backtest import run_backtest
    from ttbot.data.calendar import get_calendar
    from ttbot.data.synthetic import random_walk_bars

    storage = Storage(cfg.db_path)
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    bars_df = None
    client = None
    if args.synthetic:
        bars_df = random_walk_bars(get_calendar(cfg.session.calendar), cfg.symbols, start, end, seed=cfg.seed)
    else:
        client = _client()
    run_id, stats = run_backtest(cfg, storage, client, start, end, args.run_id, bars_df, not args.no_mc,
                                 notes="synthetic random walk" if args.synthetic else None)
    print(f"\nrun_id: {run_id}\n")
    print_summary(stats)
    return 0


def cmd_forward(cfg: AppConfig, args: argparse.Namespace) -> int:
    from ttbot.live import LiveRunner, PaperMirror

    client = _client()
    assert client is not None
    client.get_paper_account()  # verify keys + paper account up front
    storage = Storage(cfg.db_path)

    def fetch(symbols: list[str], start: datetime, end: datetime) -> pd.DataFrame:
        return client.get_bars(symbols, start, end, "1Min", cfg.data.feed, cfg.data.adjustment)

    runner = LiveRunner(cfg, storage, fetch, run_id=args.run_id or "forward")
    if cfg.mirror_strategy:
        runner.mirror = PaperMirror(client, cfg.mirror_strategy)
    runner.start()
    runner.run_forever()
    return 0


def cmd_simulate(cfg: AppConfig, args: argparse.Namespace) -> int:
    from ttbot.simulate import run_simulation

    client = None if args.synthetic else _client()
    storage = Storage(cfg.db_path)
    ok = run_simulation(cfg, storage, client, date.fromisoformat(args.day), synthetic=args.synthetic,
                        crash_at_minute=args.crash_at)
    return 0 if ok else 1


def cmd_report(cfg: AppConfig, args: argparse.Namespace) -> int:
    storage = Storage(cfg.db_path)
    runs = storage.runs()
    if args.run_id is None:
        print(runs[["run_id", "mode", "created_at", "start", "end", "status", "notes"]].to_string(index=False))
        return 0
    stats = storage.stats(args.run_id)
    print_summary(stats)
    return 0


def cmd_health(cfg: AppConfig, args: argparse.Namespace) -> int:
    """Container health check: heartbeat must be recent (the loop writes one every poll)."""
    meta = Storage(cfg.db_path).get_meta("heartbeat")
    if not meta:
        print("no heartbeat yet")
        return 1
    age = (datetime.now(UTC) - datetime.fromisoformat(meta[1])).total_seconds()
    print(f"heartbeat age {age:.0f}s")
    return 0 if age < args.max_age else 1


def cmd_dashboard(cfg: AppConfig, args: argparse.Namespace) -> int:
    app = next(p for p in (Path("dashboard/app.py"), Path(__file__).resolve().parents[2] / "dashboard" / "app.py")
               if p.exists())
    return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port),
                            "--server.address", args.address])


def main(argv: list[str] | None = None) -> int:
    load_dotenv(os.environ.get("TTBOT_ENV_FILE", ".env"))
    check_environment()
    p = argparse.ArgumentParser(prog="ttbot", description="TikTok/ICT/SMC strategy paper-trading research bot")
    p.add_argument("--config", help="YAML config path (default config/default.yaml or $TTBOT_CONFIG)")
    p.add_argument("--db", help="SQLite path (default $TTBOT_DB_PATH or data/ttbot.sqlite)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="verify paper credentials and market-data access")

    b = sub.add_parser("backtest", help="run a historical backtest")
    b.add_argument("--start", required=True)
    b.add_argument("--end", required=True)
    b.add_argument("--run-id")
    b.add_argument("--feed", choices=["iex", "sip"])
    b.add_argument("--no-mc", action="store_true", help="skip the random-entry Monte-Carlo test")
    b.add_argument("--synthetic", action="store_true", help="use a random-walk dataset (offline sanity check)")

    f = sub.add_parser("forward", help="forward paper trading loop (runs forever)")
    f.add_argument("--run-id")

    s = sub.add_parser("simulate", help="replay one session minute-by-minute through the live runner")
    s.add_argument("--day", required=True, help="session date to replay, YYYY-MM-DD")
    s.add_argument("--synthetic", action="store_true")
    s.add_argument("--crash-at", type=int, default=120, help="simulate a crash after N minutes (0=never)")

    r = sub.add_parser("report", help="list runs or print a run's ranking")
    r.add_argument("--run-id")

    h = sub.add_parser("health", help="exit 0 if the forward loop heartbeat is recent")
    h.add_argument("--max-age", type=int, default=900, help="seconds")

    d = sub.add_parser("dashboard", help="launch the Streamlit dashboard")
    d.add_argument("--port", type=int, default=8501)
    d.add_argument("--address", default="0.0.0.0")

    args = p.parse_args(argv)
    cfg = load_config(args.config)
    updates: dict = {}
    if args.db:
        updates["db_path"] = args.db
    if getattr(args, "feed", None):
        updates["data"] = cfg.data.model_copy(update={"feed": args.feed})
    if updates:
        cfg = cfg.model_copy(update=updates)
    setup_logging(cfg.log_level, cfg.log_json and args.cmd == "forward")
    handlers = {"check": cmd_check, "backtest": cmd_backtest, "forward": cmd_forward, "simulate": cmd_simulate,
                "report": cmd_report, "health": cmd_health, "dashboard": cmd_dashboard}
    return handlers[args.cmd](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
