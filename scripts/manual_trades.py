"""Submit hand-picked paper trades through the news executor, so the running worker manages their stops and exits.

Usage (after the open):  .venv/bin/python scripts/manual_trades.py NTNX:-1 DELL:-1 [--pct 10] [--stop 2]
Each SYMBOL:DIRECTION is 1 for long, -1 for short. Positions get a broker stop, a 2R target, and are flat by the close.
"""
import argparse
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

from botfolio.config import load_config, load_credentials
from botfolio.data.alpaca import AlpacaClient
from botfolio.events.paper import PaperExecutor, Signal
from botfolio.events.store import EventStore
from botfolio.storage import Storage

load_dotenv()
parser = argparse.ArgumentParser()
parser.add_argument("trades", nargs="+")
parser.add_argument("--pct", type=float, default=10, help="position size, percent of equity")
parser.add_argument("--stop", type=float, default=2, help="stop distance, percent of entry")
args = parser.parse_args()

cfg = load_config()
client = AlpacaClient(load_credentials(), timeout=5, max_retries=1)
paper = PaperExecutor(client, EventStore(Storage(cfg.db_path)), limits=cfg.limits(), feed=cfg.data.feed)
day = datetime.now(UTC).strftime("%Y%m%d")
signals = []
for trade in args.trades:
    symbol, direction = trade.split(":")
    if int(direction) not in (1, -1):
        sys.exit(f"bad direction in {trade}")
    signals.append(Signal("news", f"manual-{day}-{symbol}", symbol, int(direction), stop_pct=args.stop / 100,
                          target_r=2, position_pct=args.pct))
quotes = {s.symbol: client._request("GET", f"https://data.alpaca.markets/v2/stocks/{s.symbol}/quotes/latest",
                                    params={"feed": cfg.data.feed})["quote"] for s in signals}
for key, result in paper.submit(signals, quotes).items():
    print(key, result)
