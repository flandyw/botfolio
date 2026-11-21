from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
from streamlit.testing.v1 import AppTest

from botfolio.config import load_config
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
    for name in ("ALPACA_KEY", "ALPACA_SECRET", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY"):
        monkeypatch.setenv(name, "")
    # Any unexpected request, including an order, fails the test immediately.
    request = Mock(side_effect=AssertionError("Dashboard must not access the network during rendering"))
    monkeypatch.setattr("requests.sessions.Session.request", request)
    return request


def button(app, label):
    return next(item for item in app.button if item.label == label)


def test_stale_news_and_wait_are_explained(app_env):
    store = EventStore(Storage(load_config().db_path))
    published = datetime.now(UTC) - timedelta(days=10)
    event = Event(source="fixture", source_id="ui-1", ticker="SPY", event_type="news",
        published_at=published, content_at=published, received_at=datetime.now(UTC),
        source_url="https://example.com/news", text="Original fixture article, for UI testing only.")
    store.add(event)
    analyze(store, event, "ui-disabled", AI(), {})
    app = AppTest.from_file(APP).run()
    app.radio[0].set_value("News & AI").run()
    assert not app.exception
    assert any("archived articles" in item.value for item in app.warning)
    assert "Wait" in app.subheader[0].value
    assert any("abstaining" in item.value for item in app.info)
    app_env.assert_not_called()


def test_password_gate_blocks_data_until_login_and_supports_logout(app_env, monkeypatch):
    monkeypatch.setenv("BOTFOLIO_DASHBOARD_PASSWORD", "test-only-password")
    app = AppTest.from_file(APP).run()
    assert not app.exception
    assert app.title[0].value == "Sign in to Botfolio"
    assert not app.radio
    app.text_input[0].set_value("wrong-password")
    button(app, "Sign in").click().run()
    assert app.error
    assert not app.radio
    app.text_input[0].set_value("test-only-password")
    button(app, "Sign in").click().run()
    assert not app.exception
    assert app.radio[0].value == "Paper trading"
    button(app, "Sign out").click().run()
    assert not app.radio
    assert app.title[0].value == "Sign in to Botfolio"
    app_env.assert_not_called()


def test_paper_control_room_is_safe_by_default(app_env, monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_KILL_FILE", str(tmp_path/"KILL"))
    app = AppTest.from_file(APP).run()
    app.radio[0].set_value("Paper trading").run()
    assert not app.exception
    assert button(app, "Enable paper entries").disabled
    assert any("Still needed" in item.value for item in app.markdown)  # Says why the button is locked.
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
    cfg = load_config()
    assert len(cfg.symbols) > 100 and set(cfg.etfs) <= set(cfg.symbols)
    box = lambda: next(x for x in app.text_area if x.label.startswith("Watchlist"))  # noqa: E731
    assert box().value == ", ".join(cfg.symbols)
    box().set_value("AAPL, MSFT, aapl")
    button(app, "Save watchlist").click().run()
    storage = Storage(cfg.db_path)
    assert storage.get("watchlist") == ["AAPL", "MSFT"]
    box().set_value("bad/ticker")
    button(app, "Save watchlist").click().run()
    assert app.error
    assert storage.get("watchlist") == ["AAPL", "MSFT"]
    large = [f"T{i}" for i in range(150)]
    box().set_value(", ".join(large))
    button(app, "Save watchlist").click().run()
    assert not app.error and storage.get("watchlist") == large
    app_env.assert_not_called()


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


def test_entries_can_be_armed_without_an_ai_provider(app_env, monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXCLUSIVE_ACCOUNT", "true")
    monkeypatch.setenv("EVENT_KILL_FILE", str(tmp_path/"KILL"))
    storage = Storage(load_config().db_path)
    storage.put("event-execution-state", {"at": datetime.now(UTC).isoformat(), "phase": "ready"})
    app = AppTest.from_file(APP).run()
    assert button(app, "Enable paper entries").disabled
    assert any("Momentum and ETF trend do not need one" in item.value for item in app.caption)
    app.checkbox[0].check().run()
    button(app, "Enable paper entries").click().run()
    assert storage.get("event-paper-armed") is True
    app_env.assert_not_called()
