from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import requests

from botfolio.config import Credentials
from botfolio.data.alpaca import AlpacaAPIError, AlpacaClient
from botfolio.events.cli import execution_monitor
from botfolio.events.paper import PaperExecutor, event_is_fresh
from botfolio.events.store import EventStore
from botfolio.storage import Storage

from .test_events import AT, decision, event


@pytest.fixture
def paper(monkeypatch, tmp_path):
    monkeypatch.setenv("EVENT_PAPER_ENABLED", "true")
    monkeypatch.setenv("EVENT_EXCLUSIVE_ACCOUNT", "true")
    monkeypatch.setenv("EVENT_KILL_FILE", str(tmp_path/"KILL"))
    store = EventStore(Storage(":memory:"))
    store.storage.put("event-paper-armed", True)
    client = Mock()
    client.get_paper_account.return_value = {"status": "ACTIVE", "equity": "10000", "cash": "10000", "last_equity": "10000"}
    return PaperExecutor(client, store)


def reserve(paper, **updates):
    intent = dict(state="submitted", client_id="evt-fixture", symbol="SPY", qty=2,
                  entry_deadline=(AT+timedelta(seconds=30)).isoformat(), deadline=(AT+timedelta(minutes=15)).isoformat())
    intent.update(updates)
    with paper.store.storage.tx() as conn:
        conn.execute("INSERT INTO event_orders VALUES (?,?)", (event().key, json.dumps(intent)))
    return intent


def test_old_article_download_is_not_fresh():
    e = event(mode="forward", received_at=AT+timedelta(days=1))
    assert not event_is_fresh(e, e.received_at)
    assert not event_is_fresh(event(), AT)


def test_holidays_and_early_closes():
    # Thanksgiving is closed; the next day closes at 13:00 Eastern.
    for at in [datetime(2025,11,27,15,tzinfo=UTC), datetime(2025,11,28,18,tzinfo=UTC)]:
        e = event(mode="forward", published_at=at, content_at=at, received_at=at)
        assert event_is_fresh(e, at)  # Fresh news can wait; session checks separately prevent entry.
        from botfolio.data.calendar import get_calendar
        assert get_calendar("NASDAQ").session_for(at) is None


def test_kill_waits_for_child_cancellation_before_exit(paper, monkeypatch):
    from pathlib import Path
    Path(__import__('os').environ['EVENT_KILL_FILE']).touch()
    reserve(paper)
    parent = dict(id="entry", qty="2", status="filled", filled_qty="2", legs=[
        dict(id="stop", status="new"), dict(id="target", status="new")])
    calls = []
    def request(method, path, **kw):
        calls.append((method,path))
        if path == 'orders:by_client_order_id':
            return parent
        if path == 'positions':
            return [dict(symbol="SPY", qty="2", side="long")]
    paper.request = request
    paper.maintain(AT)
    assert ('DELETE','orders/stop') in calls
    assert ('DELETE','orders/target') in calls
    assert not any(method == 'POST' for method,path in calls)
    assert ('DELETE','orders') not in calls
    # Once cancellation is confirmed, one deterministic exit is submitted.
    parent['legs'] = [dict(id="stop",status="canceled"), dict(id="target",status="canceled")]
    def find(client_id):
        return parent if client_id == 'evt-fixture' else None
    paper.find_order = find
    original = paper.request
    paper.request = lambda method,path,**kw: {'is_open':True} if path == 'clock' else original(method,path,**kw)
    paper.maintain(AT)
    assert sum(method == 'POST' for method,path in calls) == 1
    assert paper.intents()[0][1]['exit_reserved'] is True
    # Lost response is blocked rather than blindly resubmitted.
    with pytest.raises(RuntimeError,match='ambiguous'):
        paper.maintain(AT)
    assert sum(method == 'POST' for method,path in calls) == 1


def test_partial_fill_cancels_remaining_entry(paper):
    reserve(paper)
    paper.find_order = Mock(return_value=dict(id='entry',qty='2',status='partially_filled',filled_qty='1',legs=[]))
    paper.request = Mock(side_effect=[[dict(symbol='SPY',qty='1',side='long')],None])
    paper.maintain(AT)
    assert paper.request.call_args.args == ('DELETE','orders/entry')


def test_cancelled_partially_filled_order_is_closed_when_flat(paper):
    reserve(paper)
    paper.find_order = Mock(return_value=dict(id='entry',qty='2',status='canceled',filled_qty='1',legs=[]))
    paper.request = Mock(return_value=[])
    assert paper.maintain(AT) == []
    assert paper.intents()[0][1]['state'] == 'closed'


def test_404_reservation_blocks_new_entries(paper):
    reserve(paper)
    paper.request = Mock(side_effect=AlpacaAPIError(404,'not found'))
    with pytest.raises(RuntimeError,match='manual reconciliation'):
        paper.maintain(AT)


def test_closed_broker_clock_prevents_order(paper):
    e = event(mode='forward',published_at=AT,content_at=AT,received_at=AT)
    paper.request = Mock(return_value={'is_open':False,'timestamp':AT.isoformat()})
    assert 'clock closed' in paper.enter(e,decision(e),{},AT)
    assert all(call.args[0] == 'GET' for call in paper.request.call_args_list)


def test_pause_prevents_entry_but_does_not_skip_reconciliation(paper):
    e = event(mode='forward',published_at=AT,content_at=AT,received_at=AT)
    paper.store.storage.put('event-paper-armed',False)
    assert 'paused' in paper.enter(e,decision(e),{},AT)
    reserve(paper)
    paper.find_order = Mock(return_value=dict(id='entry',qty='2',status='canceled',filled_qty='0',legs=[]))
    paper.request = Mock(return_value=[])
    paper.maintain(AT)
    assert paper.intents()[0][1]['state'] == 'closed'


def test_daily_loss_halt_stays_latched(paper):
    e = event(mode='forward',published_at=AT,content_at=AT,received_at=AT)
    paper._maintain = Mock(return_value=[])
    paper.request = Mock(side_effect=lambda method,path,**kw:
                         {'is_open':True,'timestamp':AT.isoformat()} if path == 'clock' else [])
    account = paper.client.get_paper_account.return_value
    account.update(equity='9600',last_equity='10000')
    assert paper.enter(e,decision(e),{},AT) == 'daily loss limit'
    account['equity']='10000'
    assert paper.enter(e,decision(e),{},AT) == 'daily loss limit'


def test_writes_do_not_retry_ambiguous_timeouts():
    client = AlpacaClient(Credentials(key_id='fixture',secret_key='fixture'),max_retries=4)
    client._session.request = Mock(side_effect=requests.Timeout('ambiguous'))
    with pytest.raises(requests.Timeout):
        client._request('POST','https://paper-api.alpaca.markets/v2/orders',json={})
    assert client._session.request.call_count == 1


def test_supervisor_reports_failure_and_runs_without_ai(paper):
    paper.maintain = Mock(side_effect=RuntimeError('broker unavailable'))
    asyncio.run(execution_monitor(paper,paper.store,once=True))
    status=paper.store.storage.get('event-execution-state')
    assert status['phase'] == 'error'
    assert 'blocked' in status['message']


def ready_entry(paper):
    e=event(mode='forward',published_at=AT,content_at=AT,received_at=AT)
    paper._maintain=Mock(return_value=[])
    def request(method,path,**kw):
        if path=='clock':
            return {'is_open':True,'timestamp':AT.isoformat()}
        if method=='GET':
            return []
        return {'id':'entry'}
    paper.request=Mock(side_effect=request)
    quote={'t':AT.isoformat(),'bp':99.99,'ap':100.}
    return e,quote


def test_opening_news_waits_for_0945(paper):
    open_ = AT - timedelta(minutes=15)  # 09:30 ET
    e = event(mode='forward', published_at=open_, content_at=open_, received_at=open_+timedelta(minutes=1))
    _, _ = ready_entry(paper)
    assert paper.enter(e, decision(e), {}, open_+timedelta(minutes=5)) == 'waiting for the open or the opening range'
    assert event_is_fresh(e, AT+timedelta(minutes=5)) and not event_is_fresh(e, AT+timedelta(minutes=6))
    quote = {'t': AT.isoformat(), 'bp': 99.99, 'ap': 100.}
    assert paper.enter(e, decision(e), quote, AT) == 'submitted'
    body = next(call.kwargs['json'] for call in paper.request.call_args_list if call.args[0] == 'POST')
    assert float(body['stop_loss']['stop_price']) == round(float(body['limit_price'])*.98, 2)


def test_eligible_signal_submits_one_bounded_bracket_and_deduplicates(paper):
    e,quote=ready_entry(paper)
    assert paper.enter(e,decision(e),quote,AT)=='submitted'
    body=next(call.kwargs['json'] for call in paper.request.call_args_list if call.args[0]=='POST')
    assert body['extended_hours'] is False
    assert body['order_class']=='oto' and 'take_profit' not in body
    assert float(body['qty'])*float(body['limit_price']) <= 2500
    assert paper.enter(e,decision(e),quote,AT)=='duplicate event'
    assert sum(call.args[0]=='POST' for call in paper.request.call_args_list)==1


def test_definitive_broker_rejection_does_not_leave_unknown_reservation(paper):
    e,quote=ready_entry(paper)
    original=paper.request.side_effect
    def reject(method,path,**kw):
        if method=='POST':
            raise AlpacaAPIError(403,'blocked')
        return original(method,path,**kw)
    paper.request.side_effect=reject
    assert 'rejected' in paper.enter(e,decision(e),quote,AT)
    assert paper.intents()[0][1]['state']=='closed'


def test_slow_preflight_cannot_use_old_quote(paper,monkeypatch):
    e,quote=ready_entry(paper)
    monkeypatch.setattr('botfolio.events.paper.time.monotonic',Mock(side_effect=[0.,20.]))
    assert 'took too long' in paper.enter(e,decision(e),quote,AT)
    assert not paper.intents()
    assert not any(call.args[0]=='POST' for call in paper.request.call_args_list)


@pytest.mark.parametrize('field', ['equity','buying_power','last_equity'])
def test_nonfinite_account_values_block_entry(paper,field):
    e,quote=ready_entry(paper)
    paper.client.get_paper_account.return_value[field]='nan'
    assert 'invalid' in paper.enter(e,decision(e),quote,AT)
    assert not paper.intents()


def test_concurrent_positions_one_per_symbol_and_capped(paper):
    e,quote=ready_entry(paper)  # SPY event
    paper._maintain.return_value=[{'symbol':'NVDA'}]
    assert paper.enter(e,decision(e),quote,AT)=='submitted'
    paper._maintain.return_value=[{'symbol':'SPY'}]
    assert paper.enter(e,decision(e),quote,AT)=='symbol occupied'
    paper._maintain.return_value=[{'symbol':s} for s in ('A','B','C','D','E')]
    assert paper.enter(e,decision(e),quote,AT)=='max concurrent positions'


PRE = AT - timedelta(hours=1, minutes=15)  # 08:30 ET


def test_premarket_news_waits_for_the_regular_open(paper):
    e, quote = ready_entry(paper)
    e = event(mode='forward', published_at=PRE, content_at=PRE, received_at=PRE)
    assert paper.enter(e, decision(e), {'t': PRE.isoformat(), 'bp': 99.9, 'ap': 100.}, PRE) == \
        'waiting for the open or the opening range'
    open_ = AT - timedelta(minutes=15)  # 09:30 ET: news from before the open enters at once, with the normal 1% stop
    paper.request.side_effect = lambda method, path, **kw: (
        {'is_open': True, 'timestamp': open_.isoformat()} if path == 'clock' else [] if method == 'GET' else {})
    assert paper.enter(e, decision(e), {'t': open_.isoformat(), 'bp': 99.99, 'ap': 100.}, open_) == 'submitted'
    body = next(call.kwargs['json'] for call in paper.request.call_args_list if call.args[0] == 'POST')
    assert body['extended_hours'] is False and body['stop_loss']['stop_price'] == str(round(float(body['limit_price'])*.98, 2))


def test_premarket_soft_stop_exits_with_marketable_extended_limit(paper):
    reserve(paper, side='long', holding=True, soft_stop=99., soft_target=102.,
            deadline=(PRE+timedelta(minutes=15)).isoformat())
    paper.client.get_latest_quotes.return_value = {'SPY': {'t': PRE.isoformat(), 'bp': 98.5, 'ap': 98.6}}
    def request(method, path, **kw):
        if path == 'positions':
            return [{'symbol': 'SPY', 'side': 'long', 'qty': '2', 'current_price': '98.5'}]
        if path == 'clock':
            return {'is_open': False, 'timestamp': PRE.isoformat()}
        if path == 'orders:by_client_order_id':
            raise AlpacaAPIError(404, 'none')
        return {}
    paper.request = Mock(side_effect=request)
    paper.maintain(PRE)
    body = next(call.kwargs['json'] for call in paper.request.call_args_list if call.args[0] == 'POST')
    assert body == dict(symbol='SPY', qty='2', side='sell', time_in_force='day', type='limit', extended_hours=True,
                        limit_price='97.52', client_order_id=body['client_order_id'])
    assert 'stop/target' in paper.intents()[0][1]['exit_requested']


def test_wide_quote_is_not_an_exit_price(paper):
    paper.client.get_latest_quotes.return_value = {'MSFT': {'t': AT.isoformat(), 'bp': 460.75, 'ap': 484.9}}
    assert paper._exit_price('MSFT', 'long', {'current_price': '483.1'}, AT) == 483.1
    paper.client.get_latest_quotes.return_value = {'MSFT': {'t': AT.isoformat(), 'bp': 483.0, 'ap': 483.2}}
    assert paper._exit_price('MSFT', 'long', {'current_price': '483.1'}, AT) == 483.0


def test_regular_strategies_stay_out_of_extended_hours(paper):
    from botfolio.events.paper import Signal
    paper.request = Mock()
    assert paper.submit([Signal('news', 'k', 'SPY', 1)], {}, PRE) == {'k': 'outside Nasdaq entry window'}


def test_partial_fill_with_stop_keeps_shares_under_soft_stop(paper):
    reserve(paper, side='long', stop=99., target=102.)
    paper.find_order = Mock(return_value=dict(id='entry', qty='2', status='partially_filled', filled_qty='1', legs=[
        dict(id='stop', status='held')]))
    paper.request = Mock(side_effect=[[dict(symbol='SPY', qty='1', side='long')],
                                      AlpacaAPIError(422, 'order pending cancel'), None])
    paper.maintain(AT)  # a 422 on cancel is not an error
    intent = paper.intents()[0][1]
    assert intent['state'] == 'submitted' and intent['soft_stop'] == 99. and intent['soft_target'] == 102.
    assert not any(call.args[0] == 'POST' for call in paper.request.call_args_list)


def test_news_entry_blocked_near_close_and_after_a_trade_today(paper):
    e, quote = ready_entry(paper)
    late = AT.replace(hour=20, minute=30)  # 15:30 ET: 30 minutes left, a 60-minute hold won't fit
    paper.hold = 60
    late_e = event(mode='forward', published_at=late, content_at=late, received_at=late)
    assert paper.enter(late_e, decision(late_e), quote, late) == 'too close to the close for the holding period'
    reserve(paper, strategy='news', opened=AT.isoformat(), filled_qty='3', state='closed')
    assert paper.enter(e, decision(e), quote, AT+timedelta(minutes=1)) == 'symbol already traded today'
