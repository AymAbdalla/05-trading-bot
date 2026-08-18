"""The liquidation recorder: parsing, the side inversion, ids, and the writer.

Everything here is OFFLINE. Not one test in this file opens a socket. Frames are
fed to the parsers as literals - the Bybit CONTROL frames below are verbatim
captures from a real 2026-08-18 session against
wss://stream.bybit.com/v5/public/linear, the liquidation payloads are synthetic
and built to the documented shapes. A test suite that needed the venue up would
report the venue's weather as a code regression, and liquidations are sporadic:
a suite that only passes during a cascade is a suite that does not run.

The load-bearing assertions, in order of how much damage their failure does:

  * THE SIDE INVERSION, both directions, both exchanges. A forced SELL means a
    LONG died. Getting this backwards raises nothing, drops nothing and moves no
    counter - it silently flips the sign of every downstream strategy. There is
    also a structural test that neither mapping table can be edited into a
    non-inversion without going red.
  * `value_usd == price * qty`.
  * The id is a deterministic function of the fields, so a reconnect replay
    collapses under INSERT OR IGNORE instead of double-counting, and it changes
    when ANY field changes.
  * Convention 20: every malformed frame and every malformed item lands in a
    NAMED bucket, and the accounting identities hold.
  * The writer never switches the journal mode of a db that already existed.
    db/trading.db is open in another process; a mode switch is exclusive.
"""
import asyncio
import json
import os
import sqlite3

from contextlib import contextmanager

import pytest

from engine.feeds import liquidation_recorder as lr
from engine.feeds.liquidation_recorder import (
    LiquidationEvent,
    LiquidationRecorder,
    LiquidationStore,
    backoff_delay,
    event_id,
    liquidated_side,
    parse_binance_frame,
    parse_bybit_frame,
)


# ---------------------------------------------------------------------------
# Frames
# ---------------------------------------------------------------------------

def binance_frame(side, symbol='BTCUSDT', price='9910.0', qty='0.014',
                  ts=1568014460893, avg_price=None, filled=None,
                  status='FILLED'):
    """A `!forceOrder@arr` event. `side` is the ORDER side, as the venue sends it."""
    order = {
        's': symbol, 'S': side, 'o': 'LIMIT', 'f': 'IOC', 'q': qty, 'p': price,
        'ap': avg_price if avg_price is not None else price,
        'X': status, 'l': filled if filled is not None else qty,
        'z': filled if filled is not None else qty, 'T': ts,
    }
    return json.dumps({'e': 'forceOrder', 'E': ts, 'o': order})


def bybit_all_liq_frame(side, symbol='BTCUSDT', price='64152.0', qty='0.5',
                        ts=1739502302929):
    """An `allLiquidation.<SYMBOL>` frame. `data` is a LIST. `S` is the ORDER side."""
    return json.dumps({
        'topic': 'allLiquidation.%s' % symbol,
        'type': 'snapshot',
        'ts': ts + 275,
        'data': [{'T': ts, 's': symbol, 'S': side, 'v': qty, 'p': price}],
    })


def bybit_legacy_liq_frame(side, symbol='BTCUSDT', price='64152.0', qty='0.5',
                           ts=1739502302929):
    """The legacy `liquidation.<SYMBOL>` frame. `data` is a single OBJECT.

    Measured dead on the live venue on 2026-08-18 ("error:handler not found"),
    but the parse path is retained so an archived fixture stays readable.
    """
    return json.dumps({
        'topic': 'liquidation.%s' % symbol,
        'type': 'snapshot',
        'ts': ts + 275,
        'data': {'updatedTime': ts, 'symbol': symbol, 'side': side,
                 'size': qty, 'price': price},
    })


#: Verbatim captures from a real session on 2026-08-18. These are the exact
#: bytes the venue sent, which is why they are pasted rather than constructed.
REAL_BYBIT_ACK = ('{"success":true,"ret_msg":"","conn_id":'
                  '"d9tcajmgcca9r5l6sugg-21srs","req_id":"","op":"subscribe"}')
REAL_BYBIT_PONG = ('{"success":true,"ret_msg":"pong","conn_id":'
                   '"d9tcajmgcca9r5l6sugg-21srs","req_id":"","op":"ping"}')
#: This is how `liquidation.<SYMBOL>` announced its own removal.
REAL_BYBIT_SUBSCRIBE_ERROR = (
    '{"success":false,"ret_msg":"error:handler not found,topic:'
    'liquidation.BTCUSDT","conn_id":"d9tc9dal53i9ketu7b50-20zox",'
    '"req_id":"old","op":"subscribe"}')


@contextmanager
def fresh_loop():
    """Install a brand new event loop for one test, then close it."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        yield loop
    finally:
        loop.close()
        asyncio.set_event_loop(None)


# ===========================================================================
# THE SIDE INVERSION. If only one section of this file survives, keep this one.
# ===========================================================================

def test_binance_sell_order_means_a_long_was_liquidated():
    """A forced SELL closes a long. The row must say 'long'."""
    result = parse_binance_frame(binance_frame('SELL'))
    assert result.frame_bucket == 'events'
    assert len(result.events) == 1
    assert result.events[0].side == 'long'


def test_binance_buy_order_means_a_short_was_liquidated():
    """A forced BUY closes a short. The row must say 'short'."""
    result = parse_binance_frame(binance_frame('BUY'))
    assert len(result.events) == 1
    assert result.events[0].side == 'short'


def test_bybit_sell_order_means_a_long_was_liquidated():
    result = parse_bybit_frame(bybit_all_liq_frame('Sell'))
    assert result.frame_bucket == 'events'
    assert len(result.events) == 1
    assert result.events[0].side == 'long'


def test_bybit_buy_order_means_a_short_was_liquidated():
    result = parse_bybit_frame(bybit_all_liq_frame('Buy'))
    assert len(result.events) == 1
    assert result.events[0].side == 'short'


@pytest.mark.parametrize('order_side,expected', [('Sell', 'long'),
                                                 ('Buy', 'short')])
def test_bybit_legacy_topic_shape_inverts_identically(order_side, expected):
    """The legacy object-shaped `data` must not take a different side path.

    Two shapes, one semantic. If the legacy branch ever stopped inverting, an
    archived fixture would replay with every side backwards and nothing would
    say so.
    """
    result = parse_bybit_frame(bybit_legacy_liq_frame(order_side))
    assert len(result.events) == 1
    assert result.events[0].side == expected


def test_side_mapping_tables_are_strict_inversions():
    """Structural guard on the mapping tables themselves.

    The tests above prove the CURRENT wiring. This proves the tables cannot be
    quietly 'corrected' into a pass-through, which is the exact edit a future
    reader who mistakes 'S' for the position side would make.
    """
    for table in (lr._BINANCE_ORDER_SIDE_TO_LIQUIDATED,
                  lr._BYBIT_ORDER_SIDE_TO_LIQUIDATED):
        assert table['SELL'] == lr.SIDE_LONG, 'a forced SELL liquidates a LONG'
        assert table['BUY'] == lr.SIDE_SHORT, 'a forced BUY liquidates a SHORT'
        assert set(table.values()) == {lr.SIDE_LONG, lr.SIDE_SHORT}


def test_side_lookup_is_case_insensitive_across_venues():
    """Binance sends 'SELL', Bybit sends 'Sell'. One table each, one answer."""
    assert liquidated_side('binance', 'sell') == 'long'
    assert liquidated_side('bybit', 'SELL') == 'long'
    assert liquidated_side('bybit', ' Buy ') == 'short'


def test_unknown_side_returns_none_and_never_defaults():
    """A default here would be a silent 50% error rate on malformed input."""
    assert liquidated_side('binance', 'LONG') is None
    assert liquidated_side('binance', None) is None
    assert liquidated_side('kraken', 'SELL') is None


# ===========================================================================
# value_usd
# ===========================================================================

def test_value_usd_is_price_times_qty():
    result = parse_bybit_frame(bybit_all_liq_frame('Sell', price='64152.0',
                                                   qty='0.5'))
    event = result.events[0]
    assert event.price == 64152.0
    assert event.qty == 0.5
    assert event.value_usd == pytest.approx(32076.0)
    assert event.value_usd == pytest.approx(event.price * event.qty)


def test_value_usd_on_binance_uses_the_filled_quantity_and_average_price():
    """`z`/`ap` are what actually transacted; `q`/`p` are only the fallback."""
    result = parse_binance_frame(binance_frame(
        'SELL', price='100.0', qty='10', avg_price='99.5', filled='8'))
    event = result.events[0]
    assert event.price == 99.5
    assert event.qty == 8.0
    assert event.value_usd == pytest.approx(796.0)


def test_binance_falls_back_to_order_qty_when_nothing_filled_yet():
    """A snapshot pushed before the fill must not be dropped as nonpositive_qty."""
    result = parse_binance_frame(binance_frame(
        'BUY', price='100.0', qty='3', avg_price='0', filled='0', status='NEW'))
    assert len(result.events) == 1, dict(result.drops)
    assert result.events[0].price == 100.0
    assert result.events[0].qty == 3.0


def test_timestamp_is_integer_epoch_milliseconds():
    event = parse_binance_frame(binance_frame('SELL', ts=1568014460893)).events[0]
    assert isinstance(event.ts, int)
    assert event.ts == 1568014460893


# ===========================================================================
# Deterministic id / idempotency
# ===========================================================================

def test_event_id_is_deterministic_across_reparses():
    a = parse_binance_frame(binance_frame('SELL')).events[0]
    b = parse_binance_frame(binance_frame('SELL')).events[0]
    assert a.id == b.id


def test_event_id_ignores_numeric_formatting():
    """'9910' and '9910.0' are the same event and must not become two rows."""
    a = parse_binance_frame(binance_frame('SELL', price='9910',
                                          qty='0.014')).events[0]
    b = parse_binance_frame(binance_frame('SELL', price='9910.0',
                                          qty='0.0140')).events[0]
    assert a.id == b.id


@pytest.mark.parametrize('kwargs', [
    {'side': 'BUY'},
    {'symbol': 'ETHUSDT'},
    {'price': '9911.0'},
    {'qty': '0.015'},
    {'ts': 1568014460894},
])
def test_event_id_changes_when_any_field_changes(kwargs):
    base = parse_binance_frame(binance_frame('SELL')).events[0]
    args = dict({'side': 'SELL'}, **kwargs)
    other = parse_binance_frame(binance_frame(**args)).events[0]
    assert other.id != base.id


def test_the_two_venues_never_collide_on_id():
    """Same symbol, side, price, qty and ms on two venues are two events."""
    a = event_id('binance', 'BTCUSDT', 'long', 1.0, 2.0, 3)
    b = event_id('bybit', 'BTCUSDT', 'long', 1.0, 2.0, 3)
    assert a != b


# ===========================================================================
# Convention 20: malformed input is COUNTED and CATEGORISED
# ===========================================================================

@pytest.mark.parametrize('raw,bucket', [
    ('not json at all', 'not_json'),
    ('{"topic":"orderbook.1.BTCUSDT","data":{}}', 'unknown_topic'),
    ('{"topic":"allLiquidation.BTCUSDT","type":"snapshot","ts":1,"data":[]}',
     'empty_data'),
    ('{"topic":"allLiquidation.BTCUSDT","data":"nonsense"}', 'unexpected_shape'),
    ('[1,2,3]', 'unexpected_shape'),
    (REAL_BYBIT_ACK, 'ack'),
    (REAL_BYBIT_PONG, 'pong'),
    (REAL_BYBIT_SUBSCRIBE_ERROR, 'subscribe_error'),
])
def test_bybit_control_and_malformed_frames_get_their_own_buckets(raw, bucket):
    assert parse_bybit_frame(raw).frame_bucket == bucket


def test_a_failed_subscribe_is_never_pooled_with_a_successful_one():
    """'the venue refused this topic' and 'the venue accepted it' demand
    opposite responses from an operator. Two causes, two numbers."""
    assert parse_bybit_frame(REAL_BYBIT_ACK).frame_bucket == 'ack'
    assert parse_bybit_frame(
        REAL_BYBIT_SUBSCRIBE_ERROR).frame_bucket == 'subscribe_error'


@pytest.mark.parametrize('raw,bucket', [
    ('{not json', 'not_json'),
    ('{"result":null,"id":1}', 'ack'),
    ('{"e":"aggTrade","s":"BTCUSDT"}', 'unknown_topic'),
    ('[]', 'empty_data'),
    ('"a bare string"', 'unexpected_shape'),
])
def test_binance_control_and_malformed_frames_get_their_own_buckets(raw, bucket):
    assert parse_binance_frame(raw).frame_bucket == bucket


@pytest.mark.parametrize('field,value,reason', [
    ('S', 'LONG', 'bad_side'),
    ('p', 'abc', 'bad_number'),
    ('p', '0', 'nonpositive_price'),
    ('p', '-5', 'nonpositive_price'),
    ('v', '0', 'nonpositive_qty'),
    ('v', '-1', 'nonpositive_qty'),
    ('s', '', 'missing_symbol'),
    ('T', 'not-a-time', 'bad_timestamp'),
])
def test_bad_items_are_dropped_with_a_named_reason(field, value, reason):
    """No silent `continue`. Every rejection has its own name."""
    payload = json.loads(bybit_all_liq_frame('Sell'))
    payload['data'][0][field] = value
    result = parse_bybit_frame(json.dumps(payload))
    assert result.events == []
    assert result.drops[reason] == 1, dict(result.drops)
    # A frame whose every item was rejected is NOT an 'events' frame.
    assert result.frame_bucket == 'no_valid_events'


def test_nan_and_infinity_are_rejected_not_stored():
    """json.loads accepts NaN and Infinity (convention 19). The db must not."""
    for bad in ('NaN', 'Infinity', '-Infinity'):
        payload = json.loads(bybit_all_liq_frame('Sell'))
        payload['data'][0]['p'] = bad
        result = parse_bybit_frame(json.dumps(payload))
        assert result.events == [], bad


def test_a_partly_bad_frame_keeps_the_good_items_and_counts_the_bad():
    payload = json.loads(bybit_all_liq_frame('Sell'))
    payload['data'].append({'T': 1, 's': 'ETHUSDT', 'S': 'Buy', 'v': '2',
                            'p': '3000'})
    payload['data'].append({'T': 1, 's': 'SOLUSDT', 'S': 'sideways', 'v': '2',
                            'p': '150'})
    result = parse_bybit_frame(json.dumps(payload))
    assert len(result.events) == 2
    assert result.drops['bad_side'] == 1
    assert result.items_seen == 3
    assert result.frame_bucket == 'events'


def test_the_per_frame_accounting_identity_holds():
    """items_seen == parsed + drops, inside a single frame."""
    payload = json.loads(bybit_all_liq_frame('Sell'))
    payload['data'].extend([
        {'T': 1, 's': 'ETHUSDT', 'S': 'Buy', 'v': '2', 'p': '3000'},
        {'T': 1, 's': 'SOLUSDT', 'S': 'nope', 'v': '2', 'p': '150'},
        {'T': 1, 's': 'XRPUSDT', 'S': 'Buy', 'v': '0', 'p': '2'},
    ])
    result = parse_bybit_frame(json.dumps(payload))
    assert result.items_seen == len(result.events) + sum(result.drops.values())


def test_feed_level_accounting_identities_hold_over_a_mixed_stream():
    recorder = LiquidationRecorder(db_path=':memory:', dry_run=True)
    frames = [bybit_all_liq_frame('Sell'), bybit_all_liq_frame('Buy'),
              REAL_BYBIT_ACK, REAL_BYBIT_PONG, REAL_BYBIT_SUBSCRIBE_ERROR,
              'garbage', '{"topic":"kline.1.BTCUSDT","data":[]}']
    for f in frames:
        recorder.ingest('bybit', parse_bybit_frame(f))
    stats = recorder.stats['bybit']
    assert stats.frames_received == len(frames)
    assert sum(stats.frame_buckets.values()) == stats.frames_received
    assert stats.items_seen == stats.events_parsed + sum(stats.event_drops.values())
    assert stats.check_identities(buffered=len(recorder._buffers['bybit']),
                                  dry_run=True) == []


def test_a_violated_identity_is_reported_and_not_repaired():
    """Corrupt a counter by hand; check_identities must SAY so."""
    recorder = LiquidationRecorder(db_path=':memory:', dry_run=True)
    recorder.ingest('bybit', parse_bybit_frame(bybit_all_liq_frame('Sell')))
    recorder.stats['bybit'].frames_received += 7      # a lie
    problems = recorder.stats['bybit'].check_identities(buffered=1, dry_run=True)
    assert problems and 'frames_received' in problems[0]


# ===========================================================================
# The writer
# ===========================================================================

@pytest.fixture
def store(tmp_path):
    s = LiquidationStore(str(tmp_path / 'test_trading.db'))
    s.connect()
    s.ensure_schema()
    yield s
    s.close()


def _event(**kw):
    base = dict(exchange='binance', symbol='BTCUSDT', side='long',
                price=100.0, qty=2.0, ts=1700000000000)
    base.update(kw)
    return LiquidationEvent(
        id=event_id(base['exchange'], base['symbol'], base['side'],
                    base['price'], base['qty'], base['ts']),
        value_usd=base['price'] * base['qty'], **base)


def test_schema_matches_the_agreed_columns(store):
    info = store.conn.execute('PRAGMA table_info(liquidations)').fetchall()
    cols = {r[1]: r[2] for r in info}
    assert cols == {'id': 'TEXT', 'ts': 'INTEGER', 'exchange': 'TEXT',
                    'symbol': 'TEXT', 'side': 'TEXT', 'price': 'REAL',
                    'qty': 'REAL', 'value_usd': 'REAL'}
    assert [r[1] for r in info if r[5]] == ['id'], 'id must be the primary key'
    # Every column EXCEPT `id` reports NOT NULL. `id` does not, and that is
    # sqlite's own long-standing quirk rather than a slip in the schema: a TEXT
    # PRIMARY KEY stays nullable unless NOT NULL is spelled out. It cannot bite
    # here because `event_id()` always returns a string, and the column list
    # above is the agreed contract - do not "fix" the schema to satisfy a
    # tidier-looking assertion.
    assert all(r[3] for r in info if r[1] != 'id'), 'a non-id column is nullable'


def test_both_indexes_exist(store):
    idx = {r[0] for r in store.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND tbl_name='liquidations'").fetchall()}
    assert 'idx_liquidations_ts' in idx
    assert 'idx_liquidations_symbol_ts' in idx


def test_insert_batch_writes_rows_and_reports_counts(store):
    events = [_event(ts=1700000000000 + i) for i in range(5)]
    inserted, ignored = store.insert_batch(events)
    assert (inserted, ignored) == (5, 0)
    assert store.count() == 5


def test_a_reconnect_replay_is_idempotent_and_the_duplicates_are_counted(store):
    """The whole reason the id is a hash and not a uuid."""
    events = [_event(ts=1700000000000 + i) for i in range(4)]
    assert store.insert_batch(events) == (4, 0)
    inserted, ignored = store.insert_batch(events)         # the replay
    assert (inserted, ignored) == (0, 4)
    assert store.count() == 4, 'a replay must not double-count'


def test_a_partial_overlap_inserts_only_the_new_rows(store):
    first = [_event(ts=1700000000000 + i) for i in range(3)]
    store.insert_batch(first)
    overlap = first[1:] + [_event(ts=1700000000099)]
    inserted, ignored = store.insert_batch(overlap)
    assert (inserted, ignored) == (1, 2)
    assert store.count() == 4


def test_stored_values_round_trip_exactly(store):
    store.insert_batch([_event(side='short', price=64152.0, qty=0.5)])
    row = store.conn.execute(
        'SELECT exchange, symbol, side, price, qty, value_usd, ts '
        'FROM liquidations').fetchone()
    assert row == ('binance', 'BTCUSDT', 'short', 64152.0, 0.5, 32076.0,
                   1700000000000)


def test_empty_batch_is_a_no_op(store):
    assert store.insert_batch([]) == (0, 0)
    assert store.count() == 0


def test_a_new_db_is_created_in_wal(tmp_path):
    """Safe: nobody else can have a file that did not exist a moment ago."""
    s = LiquidationStore(str(tmp_path / 'brand_new.db'))
    s.connect()
    assert s.created_db is True
    assert s.journal_mode.lower() == 'wal'
    s.close()


def test_an_existing_non_wal_db_is_warned_about_and_left_alone(tmp_path, caplog):
    """db/trading.db is open in another process. A journal mode switch is a
    global exclusive operation and this module must never attempt one."""
    path = str(tmp_path / 'legacy.db')
    seed = sqlite3.connect(path)
    seed.execute('PRAGMA journal_mode=DELETE;')
    seed.execute('CREATE TABLE x (a INTEGER)')
    seed.commit()
    seed.close()

    s = LiquidationStore(path)
    with caplog.at_level('WARNING'):
        s.connect()
    assert s.created_db is False
    assert s.journal_mode.lower() != 'wal'
    assert 'NOT switching' in caplog.text
    s.ensure_schema()
    s.insert_batch([_event()])
    assert s.count() == 1, 'a non-WAL db must still be written, just warned about'
    s.close()

    after = sqlite3.connect(path)
    mode = after.execute('PRAGMA journal_mode;').fetchone()[0]
    after.close()
    assert mode.lower() == 'delete', 'the recorder changed a journal mode'


def test_an_existing_wal_db_is_left_in_wal(tmp_path):
    path = str(tmp_path / 'already_wal.db')
    seed = sqlite3.connect(path)
    seed.execute('PRAGMA journal_mode=WAL;')
    seed.execute('CREATE TABLE x (a INTEGER)')
    seed.commit()
    seed.close()

    s = LiquidationStore(path)
    s.connect()
    assert s.created_db is False
    assert s.journal_mode.lower() == 'wal'
    s.close()


def test_busy_timeout_is_set_on_the_connection(store):
    assert store.conn.execute('PRAGMA busy_timeout;').fetchone()[0] == 5000


def test_a_second_writer_can_insert_while_the_first_holds_the_db_open(tmp_path):
    """The concurrency claim, exercised rather than asserted in a docstring
    (convention 22). Two connections, WAL, both write, both rows land."""
    path = str(tmp_path / 'shared.db')
    a = LiquidationStore(path)
    a.connect()
    a.ensure_schema()
    b = LiquidationStore(path)
    b.connect()
    assert b.created_db is False and b.journal_mode.lower() == 'wal'

    a.insert_batch([_event(ts=1700000000001)])
    b.insert_batch([_event(ts=1700000000002)])
    assert a.count() == 2
    a.close()
    b.close()


# ===========================================================================
# Recorder wiring
# ===========================================================================

def test_ingest_and_flush_land_in_the_database(tmp_path):
    with fresh_loop() as loop:
        recorder = LiquidationRecorder(db_path=str(tmp_path / 'rec.db'),
                                       exchanges=('bybit',))
        loop.run_until_complete(recorder.start_store())
        recorder.ingest('bybit', parse_bybit_frame(bybit_all_liq_frame('Sell')))
        recorder.ingest('bybit', parse_bybit_frame(bybit_all_liq_frame('Buy')))
        loop.run_until_complete(recorder.flush_all())
        assert recorder.stats['bybit'].events_inserted == 2
        assert recorder.stats['bybit'].events_duplicate == 0
        rows = sorted(r[0] for r in recorder._store.conn.execute(
            'SELECT side FROM liquidations').fetchall())
        assert rows == ['long', 'short']
        loop.run_until_complete(recorder.close_store())


def test_flushing_the_same_frame_twice_counts_a_duplicate_not_a_second_row(tmp_path):
    with fresh_loop() as loop:
        recorder = LiquidationRecorder(db_path=str(tmp_path / 'rec.db'),
                                       exchanges=('bybit',))
        loop.run_until_complete(recorder.start_store())
        frame = bybit_all_liq_frame('Sell')
        recorder.ingest('bybit', parse_bybit_frame(frame))
        loop.run_until_complete(recorder.flush_all())
        recorder.ingest('bybit', parse_bybit_frame(frame))   # reconnect replay
        loop.run_until_complete(recorder.flush_all())
        st = recorder.stats['bybit']
        assert (st.events_parsed, st.events_inserted, st.events_duplicate) == (2, 1, 1)
        assert st.check_identities(buffered=0, dry_run=False) == []
        loop.run_until_complete(recorder.close_store())


def test_dry_run_parses_and_counts_but_writes_nothing(tmp_path):
    path = str(tmp_path / 'untouched.db')
    with fresh_loop() as loop:
        recorder = LiquidationRecorder(db_path=path, dry_run=True,
                                       exchanges=('bybit',))
        loop.run_until_complete(recorder.start_store())
        recorder.ingest('bybit', parse_bybit_frame(bybit_all_liq_frame('Sell')))
        loop.run_until_complete(recorder.flush_all())
        assert recorder.stats['bybit'].events_parsed == 1
        assert recorder.stats['bybit'].events_inserted == 0
        loop.run_until_complete(recorder.close_store())
    assert not os.path.exists(path), '--dry-run created a database file'


def test_one_feed_dying_does_not_stop_the_other(tmp_path):
    """Each exchange has its OWN supervisor. A socket that dies on one venue
    must not take the other venue's tape down with it.

    Two SYNTHETIC feeds, injected through `feed_runners`. This property belongs
    to `_supervise`, not to any particular venue, and the previous version of
    this test asserted it through Binance - so when Binance was retired for
    being geoblocked, the test died with it and took the coverage of a generic
    mechanism along. Injecting the pair keeps the property pinned no matter
    which venues happen to be runnable.
    """
    with fresh_loop() as loop:
        dying_attempts = []

        async def dying_feed():
            dying_attempts.append(1)
            raise ConnectionError('simulated socket death')

        recorder = None

        async def healthy_feed():
            recorder.ingest('healthy',
                            parse_bybit_frame(bybit_all_liq_frame('Sell')))
            while not recorder.stopping:
                await asyncio.sleep(0.01)

        recorder = LiquidationRecorder(
            db_path=str(tmp_path / 'rec.db'), dry_run=True,
            stats_interval=1000.0, flush_interval=0.05,
            exchanges=('dying', 'healthy'),
            feed_runners={'dying': dying_feed, 'healthy': healthy_feed})

        original = lr.backoff_delay
        lr.backoff_delay = lambda *a, **k: 0.02   # pin the jitter, not a race
        try:
            loop.run_until_complete(recorder.run(duration_sec=0.6))
        finally:
            lr.backoff_delay = original

        assert len(dying_attempts) >= 2, 'the dying feed was never retried'
        assert recorder.stats['dying'].reconnects >= 1
        assert recorder.stats['healthy'].events_parsed == 1, \
            'the healthy feed lost data because the other one died'


def test_binance_is_refused_rather_than_started(tmp_path):
    """Binance is geoblocked and its socket CONNECTS while delivering nothing.

    A silent zero that reads as uptime is worse than a refusal, so asking for it
    must fail loudly at construction and at the CLI, and the refusal must carry
    the measurement rather than a bare 'unsupported'.
    """
    with pytest.raises(ValueError) as excinfo:
        LiquidationRecorder(db_path=str(tmp_path / 'x.db'),
                            exchanges=('binance', 'bybit'))
    assert '451' in str(excinfo.value)

    assert lr.main(['--exchanges', 'binance',
                    '--db', str(tmp_path / 'y.db'), '--dry-run',
                    '--log-dir', str(tmp_path)]) == 2


def test_hyperliquid_is_refused_because_no_public_feed_exists(tmp_path):
    """There is no venue-wide HL liquidation tape to add (measured).

    The dangerous alternative is recording public `trades` as liquidations,
    which would fabricate a tape. The refusal is what stops that being a
    one-line default change later.
    """
    assert 'hyperliquid' not in lr.SUPPORTED_EXCHANGES
    assert lr.main(['--exchanges', 'hyperliquid',
                    '--db', str(tmp_path / 'y.db'), '--dry-run',
                    '--log-dir', str(tmp_path)]) == 2


def test_the_default_exchange_set_is_bybit_only(tmp_path):
    """The default is what actually runs in production via the .sh runner."""
    recorder = LiquidationRecorder(db_path=str(tmp_path / 'x.db'))
    assert recorder.exchanges == ['bybit']
    assert lr.build_parser().parse_args([]).exchanges == 'bybit'


def test_an_empty_exchange_list_is_refused(tmp_path):
    """A recorder with no feed logs clean heartbeats and records nothing."""
    assert lr.main(['--exchanges', ',,', '--db', str(tmp_path / 'y.db'),
                    '--dry-run', '--log-dir', str(tmp_path)]) == 2


def test_a_connected_feed_with_zero_events_is_flagged_as_silent(tmp_path):
    """The Binance failure shape, made mechanical.

    Connected + zero events is indistinguishable from a quiet venue for the
    first few minutes; past SILENCE_ALERT_SEC, saying nothing is the bug.
    """
    recorder = LiquidationRecorder(db_path=str(tmp_path / 'x.db'),
                                   dry_run=True)
    st = recorder.stats['bybit']

    # Never connected -> nothing to say. Silence from a feed that never came up
    # is the supervisor's problem and is already logged as a reconnect.
    assert recorder.silent_feeds() == []

    now = 1_000_000.0
    st.connected_since = now
    assert recorder.silent_feeds(now=now + 10.0) == []

    flagged = recorder.silent_feeds(now=now + lr.SILENCE_ALERT_SEC + 1.0)
    assert [name for name, _ in flagged] == ['bybit']

    # One event is enough to prove the socket is live. Volume is a separate
    # question and this check must not creep into judging it.
    st.events_parsed = 1
    assert recorder.silent_feeds(now=now + lr.SILENCE_ALERT_SEC + 1.0) == []


def test_run_returns_zero_when_the_accounting_is_clean(tmp_path):
    with fresh_loop() as loop:
        recorder = LiquidationRecorder(db_path=str(tmp_path / 'rec.db'),
                                       dry_run=True, exchanges=('bybit',),
                                       stats_interval=1000.0)

        async def quiet_bybit():
            recorder.ingest('bybit', parse_bybit_frame(REAL_BYBIT_ACK))
            while not recorder.stopping:
                await asyncio.sleep(0.01)

        recorder._run_bybit_once = quiet_bybit
        assert loop.run_until_complete(recorder.run(duration_sec=0.3)) == 0


def test_request_stop_before_run_exits_immediately(tmp_path):
    """A SIGTERM that lands during startup must not be lost."""
    with fresh_loop() as loop:
        recorder = LiquidationRecorder(db_path=str(tmp_path / 'rec.db'),
                                       dry_run=True, exchanges=('bybit',))
        recorder._run_bybit_once = lambda: asyncio.sleep(30)
        recorder.request_stop()
        assert loop.run_until_complete(recorder.run(duration_sec=30)) == 0


def test_backoff_is_exponential_capped_and_jittered():
    import random as _random
    rng = _random.Random(0)
    # Full jitter: every draw is inside [0, exp], and exp is capped.
    for attempt in range(1, 12):
        for _ in range(20):
            d = backoff_delay(attempt, rng=rng)
            assert 0.0 <= d <= lr.RECONNECT_CAP_SEC
    # It really does grow: the ceiling at attempt 5 exceeds the ceiling at 1.
    assert max(backoff_delay(5, rng=rng) for _ in range(200)) > \
        max(backoff_delay(1, rng=rng) for _ in range(200))


def test_bybit_topic_prefix_default_is_the_one_the_venue_still_serves():
    """Measured 2026-08-18: `liquidation.<SYM>` -> 'error:handler not found'."""
    assert lr.BYBIT_TOPIC_PREFIX == 'allLiquidation'
    recorder = LiquidationRecorder(symbols=['BTCUSDT', 'ethusdt'],
                                   db_path=':memory:', dry_run=True)
    assert recorder.symbols == ['BTCUSDT', 'ETHUSDT']


def test_default_bybit_symbols_cover_the_required_three():
    assert set(lr.DEFAULT_BYBIT_SYMBOLS) >= {'BTCUSDT', 'ETHUSDT', 'SOLUSDT'}


def test_cli_parses_the_documented_flags():
    args = lr.build_parser().parse_args(
        ['--symbols', 'BTCUSDT,ETHUSDT', '--db', '/tmp/x.db',
         '--log-level', 'DEBUG', '--stats-interval', '5', '--dry-run'])
    assert args.symbols == 'BTCUSDT,ETHUSDT'
    assert args.db == '/tmp/x.db'
    assert args.log_level == 'DEBUG'
    assert args.stats_interval == 5.0
    assert args.dry_run is True


def _code_only(src):
    """Strip the module docstring so a structural scan reads CODE, not prose."""
    return src.split('"""', 2)[-1]


def test_this_module_holds_no_execution_authority():
    """Read-only feed: no key material, no order verb, no mutating SQL."""
    code = _code_only(open(lr.__file__).read())
    for forbidden in ('private_key', 'api_secret', 'api_key', 'place_order',
                      'submit_order', 'create_order', 'Wallet'):
        assert forbidden not in code, forbidden
    upper = code.upper()
    assert 'DELETE FROM' not in upper
    assert 'UPDATE LIQUIDATIONS' not in upper
    assert 'DROP TABLE' not in upper
    # The one write verb it is allowed to use.
    assert 'INSERT OR IGNORE INTO liquidations' in code


def test_importing_the_module_does_not_import_websockets():
    """`_import_connect` is lazy on purpose: the parse/store surface must stay
    testable on a machine with no websocket client, and no test should be able
    to acquire the ability to open a socket by accident."""
    src = open(lr.__file__).read()
    head = src.split('def _import_connect')[0]
    assert 'import websockets' not in head
    assert 'from websockets' not in head
