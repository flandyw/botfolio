from __future__ import annotations

import json
from datetime import UTC, datetime

import pandas as pd

from botfolio.events.models import Decision, Event
from botfolio.storage import Storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
 event_key TEXT PRIMARY KEY, payload TEXT NOT NULL, first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_decisions (
 event_key TEXT NOT NULL, experiment TEXT NOT NULL, model TEXT NOT NULL,
 payload TEXT NOT NULL, request TEXT NOT NULL, response TEXT NOT NULL,
 latency REAL NOT NULL, cost REAL, completed_at TEXT NOT NULL,
 PRIMARY KEY(event_key, experiment)
);
CREATE TABLE IF NOT EXISTS event_experiments (
 experiment TEXT PRIMARY KEY, config TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS event_orders (
 event_key TEXT PRIMARY KEY, payload TEXT NOT NULL
);
"""


class EventStore:
    def __init__(self, storage: Storage):
        self.storage = storage
        with storage.tx() as conn:
            conn.executescript(SCHEMA)

    def freeze_experiment(self, experiment, config):
        encoded = json.dumps(config, sort_keys=True)
        with self.storage.tx() as conn:
            conn.execute("INSERT OR IGNORE INTO event_experiments VALUES (?,?)", (experiment, encoded))
            previous = conn.execute("SELECT config FROM event_experiments WHERE experiment=?", (experiment,)).fetchone()[0]
            if previous != encoded:
                raise ValueError("experiment configuration changed; use a new experiment id")

    def add(self, event: Event) -> bool:
        with self.storage.tx() as conn:
            return conn.execute("INSERT OR IGNORE INTO events VALUES (?,?,?)", (
                event.key, event.model_dump_json(), datetime.now(UTC).isoformat())).rowcount == 1

    def events(self) -> list[Event]:
        with self.storage.tx() as conn:
            rows = conn.execute("SELECT payload FROM events ORDER BY first_seen, event_key").fetchall()
        return [Event.model_validate_json(row[0]) for row in rows]

    def decision(self, key: str, experiment: str):
        with self.storage.tx() as conn:
            row = conn.execute("SELECT payload,latency,model,completed_at FROM event_decisions "
                               "WHERE event_key=? AND experiment=?", (key, experiment)).fetchone()
        return (Decision.model_validate_json(row[0]), row[1], row[2], datetime.fromisoformat(row[3])) if row else None

    def save_decision(self, event, experiment, model, decision, request, response, latency, cost=None):
        decision.validate_event(event)
        with self.storage.tx() as conn:
            conn.execute("INSERT OR IGNORE INTO event_decisions VALUES (?,?,?,?,?,?,?,?,?)", (
                event.key, experiment, model, decision.model_dump_json(), json.dumps(request),
                json.dumps(response), latency, cost, datetime.now(UTC).isoformat()))

    def audit(self):
        with self.storage.tx() as conn:
            return pd.read_sql_query("SELECT * FROM event_decisions", conn)

    def decision_stats(self):
        with self.storage.tx() as conn:
            return pd.read_sql_query("""
                WITH decisions AS (
                    SELECT d.experiment, d.model, json_extract(e.payload, '$.source') AS source,
                           json_extract(d.payload, '$.recommended_action') AS action,
                           json_extract(d.payload, '$.directional_hypothesis') AS direction,
                           json_extract(d.payload, '$.reasoning') AS reasoning,
                           COALESCE(json_extract(d.response, '$.result.usage.input_tokens'),
                                    json_extract(d.response, '$.usage.input_tokens')) AS input_tokens,
                           d.latency, d.cost, d.completed_at
                    FROM event_decisions d JOIN events e USING (event_key)
                )
                SELECT experiment, model, source, COUNT(*) AS decisions,
                       SUM(action = 'BUY') AS buy, SUM(action = 'SELL') AS sell,
                       SUM(action = 'HOLD') AS hold, SUM(action = 'WAIT') AS wait,
                       SUM(direction = 'bullish') AS bullish, SUM(direction = 'bearish') AS bearish,
                       SUM(reasoning = 'Provider failure or invalid structured output') AS provider_failures,
                       ROUND(AVG(latency), 3) AS mean_latency_seconds,
                       SUM(input_tokens) AS known_input_tokens,
                       COUNT(cost) AS decisions_with_cost, SUM(cost) AS known_input_cost_usd,
                       MAX(completed_at) AS latest_decision_utc
                FROM decisions
                GROUP BY experiment, model, source
                ORDER BY latest_decision_utc DESC, experiment, model, source
            """, conn)
