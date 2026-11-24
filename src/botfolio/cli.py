"""CLI commands for the Botfolio paper trading worker and web API."""

from __future__ import annotations

import argparse
import os
from datetime import UTC, datetime

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
    parser = argparse.ArgumentParser(prog="botfolio", description="Automated multi-strategy Alpaca Paper trading worker and API")
    parser.add_argument("--config", help="YAML config path (default: config/default.yaml or BOTFOLIO_CONFIG)")
    parser.add_argument("--db", help="SQLite database path (default: data/botfolio.sqlite or BOTFOLIO_DB_PATH)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check", help="verify the Alpaca Paper account connection")
    health = sub.add_parser("health", help="exit 0 when the paper worker heartbeat is recent")
    health.add_argument("--max-age", type=int, default=900)
    web = sub.add_parser("web", help="serve the dashboard API (worker status, controls, journal, and news)")
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

    import uvicorn

    from botfolio.webapi import create_app

    uvicorn.run(create_app(cfg), host=args.host, port=args.port, log_level="warning")
    return 0
