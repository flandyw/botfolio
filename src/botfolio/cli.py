"""Commands for the Alpaca Paper worker and its dashboards."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv

from botfolio.config import load_config, load_credentials
from botfolio.data.alpaca import AlpacaClient
from botfolio.events import cli as events
from botfolio.logging_setup import setup_logging
from botfolio.safety import check_environment
from botfolio.storage import Storage


def main(argv: list[str] | None = None) -> int:
    load_dotenv(os.environ.get("BOTFOLIO_ENV_FILE", ".env"))
    check_environment()
    parser = argparse.ArgumentParser(prog="botfolio", description="News-driven Alpaca paper trading")
    parser.add_argument("--config", help="YAML config path (defaults to config/default.yaml or BOTFOLIO_CONFIG)")
    parser.add_argument("--db", help="SQLite path (defaults to data/botfolio.sqlite or BOTFOLIO_DB_PATH)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="verify the Alpaca paper account")
    health = sub.add_parser("health", help="exit 0 when the paper worker heartbeat is recent")
    health.add_argument("--max-age", type=int, default=900)
    dashboard = sub.add_parser("dashboard", help="launch the paper trading control room")
    dashboard.add_argument("--port", type=int, default=8501)
    dashboard.add_argument("--address", default="127.0.0.1")
    web = sub.add_parser("web", help="serve the read-only dashboard and paper worker status API")
    web.add_argument("--port", type=int, default=8600)
    web.add_argument("--host", default="127.0.0.1")
    events.parser(sub)

    args = parser.parse_args(argv)
    cfg = load_config(args.config)
    if args.db:
        cfg = cfg.model_copy(update={"db_path": args.db})
    setup_logging(cfg.log_level, cfg.log_json and args.cmd == "events")

    if args.cmd == "events":
        return events.command(cfg, args)
    if args.cmd == "check":
        creds = load_credentials()
        assert creds is not None
        account = AlpacaClient(creds).get_paper_account()
        print(f"paper account OK: {account['account_number']} status={account['status']}")
        return 0
    if args.cmd == "health":
        storage = Storage(cfg.db_path)
        try:
            heartbeat = storage.get("event-heartbeat")
        finally:
            storage.close()
        if not heartbeat:
            print("no paper worker heartbeat yet")
            return 1
        age = (datetime.now(UTC) - datetime.fromisoformat(heartbeat)).total_seconds()
        print(f"paper worker heartbeat age {age:.0f}s")
        return 0 if 0 <= age < args.max_age else 1
    if args.cmd == "dashboard":
        app = next(p for p in (Path("dashboard/app.py"), Path(__file__).resolve().parents[2] / "dashboard" / "app.py")
                   if p.exists())
        env = {**os.environ, "BOTFOLIO_DB_PATH": cfg.db_path}
        if args.config:
            env["BOTFOLIO_CONFIG"] = args.config
        return subprocess.call([sys.executable, "-m", "streamlit", "run", str(app), "--server.port", str(args.port),
                                "--server.address", args.address], env=env)

    import uvicorn

    from botfolio.webapi import create_app

    uvicorn.run(create_app(cfg), host=args.host, port=args.port, log_level="warning")
    return 0
