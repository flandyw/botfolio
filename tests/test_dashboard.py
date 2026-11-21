from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from botfolio.config import load_config
from botfolio.dashboard_ui import friendly, is_demo, news_age, run_label, verdict_text
from botfolio.events.ai import AI, analyze
from botfolio.events.models import Event
from botfolio.events.store import EventStore
from botfolio.storage import Storage

APP = str(Path(__file__).resolve().parents[1] / "dashboard" / "app.py")


@pytest.fixture
def app_env(monkeypatch, tmp_path):
    monkeypatch.setenv("BOTFOLIO_DB_PATH", str(tmp_path / "dashboard.sqlite"))
    monkeypatch.setenv("BOTFOLIO_ENV_FILE", str(tmp_path / "no-secrets.env"))
    monkeypatch.setenv("BOTFOLIO_DASHBOARD_PASSWORD", "")
    monkeypatch.setenv("EVENT_AI_PROVIDER", "disabled")
    monkeypatch.setenv("EVENT_AI_MODEL", "disabled")
    monkeypatch.setenv("EVENT_AI_KEY", "")
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "false")
    monkeypatch.setenv("EVENT_EXCLUSIVE_ACCOUNT", "false")
    monkeypatch.setenv("BOTFOLIO_MIRROR_STRATEGY", "")
    for name in ("ALPACA_KEY", "ALPACA_SECRET", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.setenv(name, "")
    # Any unexpected request, including an order, fails the test immediately.
    request = Mock(side_effect=AssertionError("Dashboard must not access the network during rendering"))
    monkeypatch.setattr("requests.sessions.Session.request", request)
    return request


def button(app, label):
    return next(item for item in app.button if item.label == label)


def test_home_is_beginner_first_and_safe(app_env):
    app = AppTest.from_file(APP).run()
    assert not app.exception
    assert app.title[0].value == "Your trading research workspace"
    assert app.radio[0].value == "Start here"
    assert not app.text_input  # Database and secrets are not the starting experience.
    assert button(app, "Check Alpaca connection").disabled
    assert button(app, "Collect latest news").disabled
    assert not button(app, "Run practice demo").disabled
    assert app.radio[0].options[-1] == "Advanced research"  # Expert view is last, not mid-path.
    assert any("Order sending is off" in item.value for item in app.info)
    app_env.assert_not_called()


@pytest.mark.parametrize("page", ["News & AI", "Results", "Advanced research", "Help"])
def test_navigation_and_empty_states(app_env, page):
    app = AppTest.from_file(APP).run()
    app.radio[0].set_value(page).run()
    assert not app.exception
    assert app.title
    app_env.assert_not_called()


def test_practice_demo_to_results_without_credentials(app_env):
    app = AppTest.from_file(APP, default_timeout=30).run()
    button(app, "Run practice demo").click().run()
    assert not app.exception
    assert any("Practice complete" in item.value for item in app.success)
    app.radio[0].set_value("Results").run()
    assert not app.exception
    assert any("generated prices" in item.value for item in app.warning)
    assert app.dataframe
    assert len(app.metric) == 3
    app.radio[0].set_value("Advanced research").run()
    assert not app.exception
    store = Storage(load_config().db_path)
    runs = store.runs()
    assert len(runs) == 1
    assert json.loads(runs.iloc[0]["config"])["risk"]["starting_capital"] == 10000
    store.close()
    app_env.assert_not_called()


def test_stale_news_and_wait_are_explained(app_env):
    store = EventStore(Storage(load_config().db_path))
    published = datetime.now(UTC) - timedelta(days=10)
    event = Event(source="fixture", source_id="ui-1", ticker="SPY", event_type="news",
        published_at=published, content_at=published, received_at=datetime.now(UTC),
        source_url="https://example.com/news", text="Original fixture article, for UI testing only.")
    store.add(event)
    analyze(store, event, "ui-disabled", AI(), {})
    app = AppTest.from_file(APP).run()
    assert any("not a current news feed" in item.value for item in app.warning)
    app.radio[0].set_value("News & AI").run()
    assert not app.exception
    assert any("archived articles" in item.value for item in app.warning)
    assert "Wait" in app.subheader[0].value
    assert any("abstaining" in item.value for item in app.info)
    app_env.assert_not_called()


def test_news_collection_button_uses_existing_provider(app_env, monkeypatch):
    monkeypatch.setenv("ALPACA_KEY", "fixture-key")
    monkeypatch.setenv("ALPACA_SECRET", "fixture-secret")
    collect = Mock(return_value=0)
    monkeypatch.setattr("botfolio.dashboard_ui.collect_news", collect)
    app = AppTest.from_file(APP).run()
    button(app, "Collect latest news").click().run()
    assert not app.exception
    assert any("does not confirm a live feed" in item.value for item in app.info)
    collect.assert_called_once()
    app_env.assert_not_called()


def test_connection_failure_does_not_expose_error_or_secrets(app_env, monkeypatch):
    monkeypatch.setenv("ALPACA_KEY", "private-fixture-key")
    monkeypatch.setenv("ALPACA_SECRET", "private-fixture-secret")
    monkeypatch.setattr("botfolio.data.alpaca.AlpacaClient.get_paper_account",
                        Mock(side_effect=RuntimeError("private-fixture-secret")))
    app = AppTest.from_file(APP).run()
    button(app, "Check Alpaca connection").click().run()
    assert not app.exception
    assert app.error
    assert "private-fixture-secret" not in app.error[0].value
    app_env.assert_not_called()


def test_labels_distinguish_generated_prices_and_news_age():
    run = dict(notes="DEMO: synthetic prices", mode="backtest", start="2025-01-06",
               end="2025-01-10", run_id="test")
    assert is_demo(run)
    assert run_label(run).startswith("Practice demo")
    assert news_age([]) is None
    now = datetime.now(UTC)
    assert news_age([SimpleNamespace(content_at=now-timedelta(hours=48))], now) == 48


def test_password_gate_blocks_data_until_login_and_supports_logout(app_env, monkeypatch):
    monkeypatch.setenv("BOTFOLIO_DASHBOARD_PASSWORD", "test-only-password")
    app = AppTest.from_file(APP).run()
    assert not app.exception
    assert app.title[0].value == "Sign in to Trading Research"
    assert not app.radio
    app.text_input[0].set_value("wrong-password")
    button(app, "Sign in").click().run()
    assert app.error
    assert not app.radio
    app.text_input[0].set_value("test-only-password")
    button(app, "Sign in").click().run()
    assert not app.exception
    assert app.radio[0].value == "Start here"
    button(app, "Sign out").click().run()
    assert not app.radio
    assert app.title[0].value == "Sign in to Trading Research"
    app_env.assert_not_called()


def test_paper_control_room_is_safe_by_default(app_env, monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_KILL_FILE", str(tmp_path/"KILL"))
    app = AppTest.from_file(APP).run()
    app.radio[0].set_value("Paper trading").run()
    assert not app.exception
    assert button(app, "Enable paper entries").disabled
    assert any("❌" in item.value for item in app.markdown)  # Says why the button is locked.
    assert any("No current news" in item.value for item in app.warning)
    button(app, "Emergency stop paper trading").click().run()
    assert (tmp_path/"KILL").exists()
    storage = Storage(load_config().db_path)
    assert storage.get("event-paper-armed") is False
    button(app, "Clear emergency stop; keep entries paused").click().run()
    assert not (tmp_path/"KILL").exists()
    assert storage.get("event-paper-armed") is False
    app_env.assert_not_called()


def test_watchlist_validation_and_save(app_env):
    app = AppTest.from_file(APP).run()
    app.radio[0].set_value("Paper trading").run()
    next(x for x in app.text_input if x.label == "News watchlist").set_value("AAPL, MSFT, aapl")
    button(app, "Save watchlist").click().run()
    storage = Storage(load_config().db_path)
    assert storage.get("event-watchlist") == ["AAPL", "MSFT"]
    next(x for x in app.text_input if x.label == "News watchlist").set_value("bad/ticker")
    button(app, "Save watchlist").click().run()
    assert app.error
    assert storage.get("event-watchlist") == ["AAPL", "MSFT"]
    app_env.assert_not_called()


def test_friendly_names_and_plain_verdict():
    assert friendly("inv_noise_mom_1m") == "Opposite of Intraday noise-band momentum · 1-min chart"
    assert friendly("buy_hold_SPY") == "Buy and hold SPY"
    assert friendly("my_custom_1m") == "my custom · 1-min chart"
    rows = {"noise_mom_1m": ("pattern", 3.0, "positive, not significant"), "random_1m": ("random", 1.0, "no edge"),
            "buy_hold_SPY": ("benchmark", 2.0, "benchmark")}
    table = pd.DataFrame.from_dict(rows, orient="index", columns=["group", "total_return_pct", "verdict"])
    kind, text = verdict_text(table)
    assert kind == "info" and "Intraday noise-band momentum" in text and "+3.00%" in text
    table.loc["noise_mom_1m", "total_return_pct"] = 1.5
    assert verdict_text(table)[0] == "warning"
    table.loc["noise_mom_1m", "verdict"] = "significant edge"
    assert verdict_text(table)[0] == "success"
    assert verdict_text(table[table.group != "pattern"]) is None


def test_paper_page_shows_when_entries_are_on(app_env, monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXCLUSIVE_ACCOUNT", "true")
    monkeypatch.setenv("EVENT_KILL_FILE", str(tmp_path/"KILL"))
    Storage(load_config().db_path).put("event-paper-armed", True)
    app = AppTest.from_file(APP).run()
    app.radio[0].set_value("Paper trading").run()
    assert not app.exception
    assert any("Paper entries are ON" in item.value for item in app.success)
    assert button(app, "Enable paper entries").disabled
    button(app, "Pause new entries").click().run()
    assert any("Paper entries are OFF" in item.value for item in app.info)
    app_env.assert_not_called()
