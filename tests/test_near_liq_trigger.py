"""Tests for PM_near_liq_trigger.

Fully offline. No network, no live database, no engine process. Every test
builds its own sqlite file under `tmp_path` with the REAL schema
(`engine.feeds.hyperliquid_client.SCHEMA_SQL`, imported rather than retyped, so
a schema change breaks these tests instead of silently passing against a copy
that has drifted).

Three jobs:

  1. THE DIRECTION PAIR. `test_long_cluster_below_spot_buys_down` and
     `test_short_cluster_above_spot_buys_up` are one test in two halves and
     neither is meaningful alone. Inverting `CLUSTER_SIDE_TO_OUTCOME` does not
     raise, does not drop a row and does not move a counter - it just loses
     money on every trade forever. A single-sided test would still pass against
     a strategy that always bought Down.

  2. THE FEED DEGRADES HONESTLY. The client has never written a row, so on the
     day this ships every evaluation takes a skip path. Those paths are the
     whole deliverable for now, and "table missing", "empty", "one snapshot"
     and "stale" must be four different words (convention 20). A skip that
     pools them is a missing number.

  3. NOTHING RAISES. A strategy that raises inside the shadow loop takes the
     loop's window with it.
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.feeds.hyperliquid_client import SCHEMA_SQL  # noqa: E402
from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
from strategies.polymarket.base import MarketContext, Window  # noqa: E402
from strategies.polymarket.liquidation_feed import (  # noqa: E402
    NO_DATA_REASONS, continuation_outcome)
from strategies.polymarket.near_liq_trigger import (  # noqa: E402
    CLUSTER_SIDE_TO_LIQUIDATED_SIDE, CLUSTER_SIDE_TO_OUTCOME,
    FEED_SKIP_REASONS, MAX_ENTRY_PRICE, NEAR_BPS, POSITION_DROP_REASONS,
    SECOND_LOCK_MIN_USD, SECOND_LOCK_WINDOW_SEC, SKIP_REASONS, WHALE_MIN_USD,
    LiqCluster, NearLiqTrigger, build_clusters, liq_distance_bps, read_feed)

NOW = 1_800_000_000          # a fixed clock; nothing here reads the wall time
WINDOW_TS = 1_799_999_700    # NOW - 300, so the window is 300s in... see below
SPOT = 60_000.0


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------
# The recorder's schema for the SECOND LOCK's table, copied from
# `engine/feeds/liquidation_recorder.py` the same way `tests/
# test_liquidation_strategies.py` copies it, so a divergence fails a test here
# rather than reading as a permanently silent tape in the shadow log.
LIQ_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS liquidations (
    id TEXT PRIMARY KEY,
    ts INTEGER NOT NULL,
    exchange TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    price REAL NOT NULL,
    qty REAL NOT NULL,
    value_usd REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_liquidations_ts ON liquidations (ts);
CREATE INDEX IF NOT EXISTS idx_liquidations_symbol_ts ON liquidations (symbol, ts);
"""

#: The default tape: a healthy recorder with a qualifying print on BOTH sides.
#:
#: Both sides on purpose. The second lock asks only "did MY side print at least
#: $5,000 in the last 120s", so seeding both makes this builder direction-blind
#: and lets every pre-existing test keep arming whichever way it arms. It is NOT
#: a claim that both sides cascade at once, and no test asserts anything about
#: the unmatched side.
#:
#: The two ancient $1 rows exist for `min_history_sec`, which
#: `read_liquidation_window` defaults to the 120s lookback: a tape whose oldest
#: and newest rows are 10 seconds apart is `liquidation_history_too_short` and
#: cannot answer a question about the last two minutes. They sit outside the
#: lookback so they contribute nothing to the window sums.
DEFAULT_LIQ_ROWS = ((200.0, 'long', 1.0), (200.0, 'short', 1.0),
                    (10.0, 'long', 25_000.0), (10.0, 'short', 25_000.0))


def _make_db(tmp_path, rows=(), create_table=True, name='trading.db',
             liq_rows=DEFAULT_LIQ_ROWS, create_liq_table=True,
             liq_symbol='BTCUSDT', now_s=float(NOW)):
    """A sqlite file with the real schema and `rows` inserted.

    `rows` are (ts, wallet, symbol, side, size_usd, entry_price, liq_price,
    leverage) tuples - the column order of the actual table.

    `liq_rows` are (offset_sec_before_now, side, usd) tuples for the SECOND
    LOCK's `liquidations` table, in the same notation
    `tests/test_liquidation_strategies.py` uses. They default to a healthy tape
    (see DEFAULT_LIQ_ROWS) so a test about clusters, books or timing is not
    silently answering a question about the liquidation recorder instead.

    `create_liq_table=False` gives a database that has the whale table and NOT
    the liquidations table - the real state of any machine where the recorder
    has never run.
    """
    path = str(tmp_path / name)
    conn = sqlite3.connect(path)
    if create_table:
        conn.executescript(SCHEMA_SQL)
        if rows:
            conn.executemany(
                'INSERT INTO hyperliquid_positions (ts, wallet, symbol, side, '
                'size_usd, entry_price, liq_price, leverage) '
                'VALUES (?,?,?,?,?,?,?,?)', list(rows))
    else:
        # A db that exists and opens but has no hyperliquid_positions table.
        conn.execute('CREATE TABLE unrelated (x INTEGER)')
    if create_liq_table:
        conn.executescript(LIQ_SCHEMA_SQL)
        for i, (offset_sec, side, usd) in enumerate(liq_rows or ()):
            conn.execute(
                'INSERT INTO liquidations '
                '(id, ts, exchange, symbol, side, price, qty, value_usd) '
                'VALUES (?,?,?,?,?,?,?,?)',
                ('liq-%d' % i, int((now_s - offset_sec) * 1000), 'bybit',
                 liq_symbol, side, SPOT, usd / SPOT, usd))
    conn.commit()
    conn.close()
    return path


def _bps_below(spot, bps):
    """A price `bps` basis points BELOW spot - where a LONG's liq price sits."""
    return spot * (1.0 - bps / 10_000.0)


def _bps_above(spot, bps):
    """A price `bps` basis points ABOVE spot - where a SHORT's liq price sits."""
    return spot * (1.0 + bps / 10_000.0)


def _position(ts, side, liq_price, wallet='0xw', size_usd=150_000.0,
              symbol='BTC', entry_price=60_500.0, leverage=20.0):
    return (ts, wallet, symbol, side, size_usd, entry_price, liq_price,
            leverage)


def _cluster_rows(ts, side, spot=SPOT, n=3, bps=20.0, size_usd=150_000.0,
                  symbol='BTC'):
    """`n` whale positions on one side, all `bps` from spot on the right side."""
    price = (_bps_below(spot, bps) if side == 'LONG'
             else _bps_above(spot, bps))
    return [_position(ts, side, price, wallet='0xw{}'.format(i),
                      size_usd=size_usd, symbol=symbol)
            for i in range(n)]


def _two_snapshots(side, spot=SPOT, n=3, bps=20.0, size_usd=150_000.0,
                   symbol='BTC'):
    """MIN_SNAPSHOTS worth of history: the live snapshot and the one before.

    The strategy only ever evaluates the NEWEST timestamp; the older snapshot
    exists to prove the poller cycled at least once.
    """
    return (_cluster_rows(NOW - 30, side, spot, n, bps, size_usd, symbol)
            + _cluster_rows(NOW, side, spot, n, bps, size_usd, symbol))


def _book(token, asks=(), bids=()):
    return Orderbook(
        token_id=token,
        asks=tuple(PriceLevel(p, s) for p, s in sorted(asks)),
        bids=tuple(PriceLevel(p, s) for p, s in sorted(bids, reverse=True)),
    )


def _market(slug='btc-updown-5m-x'):
    return Market(id=slug, question=slug, slug=slug, condition_id='c',
                  outcomes=(Outcome('Up', 'tok-up'),
                            Outcome('Down', 'tok-down')))


def _ctx(spot=SPOT, with_book=True, ask=0.45, size=500.0,
         seconds_into_window=60.0):
    market = _market() if with_book else None
    books = {}
    if with_book:
        books = {'tok-up': _book('tok-up', asks=[(ask, size)]),
                 'tok-down': _book('tok-down', asks=[(ask, size)])}
    windows = [Window(ts=WINDOW_TS - 300 * i, open=SPOT, close=SPOT,
                      direction='UP', source='price') for i in range(16)][::-1]
    return MarketContext(window_ts=WINDOW_TS, windows=windows, market=market,
                         books=books, spot=spot,
                         seconds_into_window=seconds_into_window)


def _strategy(db_path, **kw):
    kw.setdefault('now_fn', lambda: float(NOW))
    return NearLiqTrigger(db_path=db_path, **kw)


# ---------------------------------------------------------------------------
# 1. the contract
# ---------------------------------------------------------------------------
def test_strategy_name_and_zero_arg_construction():
    s = NearLiqTrigger()
    assert s.strategy_name == 'PM_near_liq_trigger'
    assert s.name == 'PM_near_liq_trigger'
    assert s.paper_mode is True
    assert s.uses_maker_orders is False


def test_every_skip_reason_is_a_distinct_string():
    assert len(SKIP_REASONS) == len(set(SKIP_REASONS))
    assert all(isinstance(r, str) and r for r in SKIP_REASONS)
    # The feed reasons are a subset, and they are distinct from each other too.
    assert len(FEED_SKIP_REASONS) == len(set(FEED_SKIP_REASONS))
    assert set(FEED_SKIP_REASONS) <= set(SKIP_REASONS)
    # And the per-position drop counters share no name with a skip reason:
    # a dropped position and a skipped window are different facts.
    assert not (set(POSITION_DROP_REASONS) & set(SKIP_REASONS))
    assert len(POSITION_DROP_REASONS) == len(set(POSITION_DROP_REASONS))


def test_no_spot_is_its_own_reason(tmp_path):
    db = _make_db(tmp_path, _two_snapshots('LONG'))
    d = _strategy(db).evaluate(_ctx(spot=None))
    assert d.action == 'SKIP'
    assert d.reason == 'no_spot'


# ---------------------------------------------------------------------------
# 2. the feed degrades honestly - four different words, no crash
# ---------------------------------------------------------------------------
def test_database_file_missing(tmp_path):
    d = _strategy(str(tmp_path / 'nope.db')).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'hyperliquid_db_missing'
    assert d.features['feed_live'] is False


def test_table_missing_when_the_client_has_never_run(tmp_path):
    db = _make_db(tmp_path, create_table=False)
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'hyperliquid_table_missing'
    assert d.features['feed_live'] is False
    assert 'never run' in d.features['feed_detail']


def test_table_exists_but_empty(tmp_path):
    db = _make_db(tmp_path, rows=())
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'hyperliquid_feed_empty'
    assert d.features['feed_total_rows'] == 0


def test_one_poll_of_history_is_not_a_cycling_poller(tmp_path):
    db = _make_db(tmp_path, _cluster_rows(NOW, 'LONG'))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'hyperliquid_single_snapshot_only'
    assert d.features['feed_distinct_snapshot_ts'] == 1


def test_stale_feed_cites_the_age_in_seconds(tmp_path):
    # Two snapshots, both far older than FEED_MAX_AGE_SEC: the poller died.
    old = NOW - 3600
    rows = (_cluster_rows(old - 30, 'LONG') + _cluster_rows(old, 'LONG'))
    d = _strategy(_make_db(tmp_path, rows)).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'hyperliquid_feed_stale'
    assert d.features['feed_age_sec'] == pytest.approx(3600.0, abs=1.0)
    assert '3600s old' in d.features['feed_detail']


def test_the_four_feed_failures_are_four_different_reasons(tmp_path):
    """The point of the previous four tests, asserted as one fact."""
    reasons = set()
    reasons.add(_strategy(str(tmp_path / 'gone.db')).evaluate(_ctx()).reason)
    reasons.add(_strategy(_make_db(tmp_path, create_table=False, name='a.db'))
                .evaluate(_ctx()).reason)
    reasons.add(_strategy(_make_db(tmp_path, rows=(), name='b.db'))
                .evaluate(_ctx()).reason)
    reasons.add(_strategy(_make_db(tmp_path, _cluster_rows(NOW, 'LONG'),
                                   name='c.db')).evaluate(_ctx()).reason)
    old = NOW - 3600
    reasons.add(_strategy(_make_db(
        tmp_path, _cluster_rows(old - 30, 'LONG') + _cluster_rows(old, 'LONG'),
        name='d.db')).evaluate(_ctx()).reason)
    assert len(reasons) == 5
    assert reasons <= set(FEED_SKIP_REASONS)


def test_a_file_that_is_not_a_database_does_not_raise(tmp_path):
    path = str(tmp_path / 'garbage.db')
    with open(path, 'w') as fh:
        fh.write('this is not sqlite\n')
    d = _strategy(path).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'hyperliquid_db_unreadable'


def test_read_feed_never_writes(tmp_path):
    """The read-only URI is the guard, so prove the connection really is ro."""
    db = _make_db(tmp_path, _two_snapshots('LONG'))
    assert read_feed(db, now=float(NOW)).status == 'ok'
    from strategies.polymarket.near_liq_trigger import _ro_uri
    conn = sqlite3.connect(_ro_uri(db), uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute('DELETE FROM hyperliquid_positions')
    conn.close()


# ---------------------------------------------------------------------------
# 3. DIRECTION. These two are one test. Neither means anything alone.
# ---------------------------------------------------------------------------
def test_long_cluster_below_spot_buys_down(tmp_path):
    """LONG liqs sit BELOW spot -> forced SELLING -> price DOWN -> buy Down."""
    db = _make_db(tmp_path, _two_snapshots('LONG', bps=20.0))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'ENTER', d.reason
    assert d.primary_leg.outcome_side == 'Down'
    assert d.features['cluster_side'] == 'LONG'
    assert d.features['cluster_positions'] == 3
    assert d.features['cluster_usd'] == pytest.approx(450_000.0)
    assert d.features['cluster_nearest_bps'] == pytest.approx(20.0, abs=0.01)
    assert d.features['feed_live'] is True


def test_short_cluster_above_spot_buys_up(tmp_path):
    """SHORT liqs sit ABOVE spot -> forced BUYING -> price UP -> buy Up."""
    db = _make_db(tmp_path, _two_snapshots('SHORT', bps=20.0))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'ENTER', d.reason
    assert d.primary_leg.outcome_side == 'Up'
    assert d.features['cluster_side'] == 'SHORT'


def test_the_direction_map_is_not_symmetric(tmp_path):
    """Belt and braces: the two sides must not produce the same outcome.

    A map that returned 'Down' for both would pass the LONG test and fail only
    this one.
    """
    long_d = _strategy(_make_db(tmp_path, _two_snapshots('LONG'), name='l.db')
                       ).evaluate(_ctx())
    short_d = _strategy(_make_db(tmp_path, _two_snapshots('SHORT'), name='s.db')
                        ).evaluate(_ctx())
    assert long_d.primary_leg.outcome_side != short_d.primary_leg.outcome_side
    assert CLUSTER_SIDE_TO_OUTCOME == {'LONG': 'Down', 'SHORT': 'Up'}


def test_liq_distance_bps_signs():
    # A long's liq below spot is a POSITIVE distance (not reached yet).
    assert liq_distance_bps('LONG', 60_000.0, 59_700.0) == pytest.approx(50.0)
    # Spot already through it -> negative -> the position is gone.
    assert liq_distance_bps('LONG', 60_000.0, 60_300.0) < 0
    # A short's liq above spot is positive.
    assert liq_distance_bps('SHORT', 60_000.0, 60_300.0) == pytest.approx(50.0)
    assert liq_distance_bps('SHORT', 60_000.0, 59_700.0) < 0
    # Unusable inputs are None, never 0.0 (which would read as "at the price").
    assert liq_distance_bps('LONG', 60_000.0, None) is None
    assert liq_distance_bps('LONG', 0.0, 100.0) is None
    assert liq_distance_bps('SIDEWAYS', 60_000.0, 100.0) is None


# ---------------------------------------------------------------------------
# 4. the real evaluation: ran, found nothing
# ---------------------------------------------------------------------------
def test_cluster_too_far_from_spot_is_a_real_evaluation(tmp_path):
    # 200 bps away, well beyond NEAR_BPS (50).
    db = _make_db(tmp_path, _two_snapshots('LONG', bps=200.0))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'no_liq_cluster_near_spot'
    assert d.reason not in FEED_SKIP_REASONS       # this one RAN
    assert d.features['feed_live'] is True
    assert d.features['dropped_not_near_spot'] == 3
    assert d.features['positions_near'] == 0


def test_one_whale_is_not_a_cluster(tmp_path):
    db = _make_db(tmp_path, _two_snapshots('LONG', n=1))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'no_liq_cluster_near_spot'
    assert d.features['long_cluster_positions'] == 1


def test_fuel_on_both_sides_is_not_a_direction(tmp_path):
    rows = (_two_snapshots('LONG', n=3, size_usd=150_000.0)
            + _two_snapshots('SHORT', n=3, size_usd=150_000.0))
    # Same notional both sides -> dominance 1.0 < CLUSTER_DOMINANCE_RATIO.
    d = _strategy(db := _make_db(tmp_path, rows)).evaluate(_ctx())
    assert db  # silence linters about the walrus
    assert d.action == 'SKIP'
    assert d.reason == 'liq_clusters_balanced'
    assert d.features['cluster_dominance'] == pytest.approx(1.0)


def test_a_dominant_side_still_fires_when_the_other_side_is_small(tmp_path):
    rows = (_two_snapshots('LONG', n=3, size_usd=400_000.0)
            + _two_snapshots('SHORT', n=2, size_usd=100_000.0))
    d = _strategy(_make_db(tmp_path, rows)).evaluate(_ctx())
    assert d.action == 'ENTER', d.reason
    assert d.primary_leg.outcome_side == 'Down'
    assert d.features['cluster_dominance'] == pytest.approx(6.0)


def test_position_already_liquidated_is_dropped_not_counted_as_closest(tmp_path):
    """His documented bug: spot through the liq price means the whale is gone."""
    ts = NOW
    rows = _cluster_rows(NOW - 30, 'LONG') + [
        # liq price ABOVE spot for a LONG: price already blew through it.
        _position(ts, 'LONG', _bps_above(SPOT, 10.0), wallet='0xdead'),
        _position(ts, 'LONG', _bps_below(SPOT, 20.0), wallet='0xalive'),
    ]
    d = _strategy(_make_db(tmp_path, rows)).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'no_liq_cluster_near_spot'
    assert d.features['dropped_liq_price_passed'] == 1
    assert d.features['positions_near'] == 1


def test_null_liq_price_is_its_own_drop_reason(tmp_path):
    ts = NOW
    rows = _cluster_rows(NOW - 30, 'LONG') + [
        _position(ts, 'LONG', None, wallet='0xnoliq'),
        _position(ts, 'LONG', _bps_below(SPOT, 20.0), wallet='0xa'),
        _position(ts, 'LONG', _bps_below(SPOT, 25.0), wallet='0xb'),
    ]
    d = _strategy(_make_db(tmp_path, rows)).evaluate(_ctx())
    assert d.action == 'ENTER', d.reason
    assert d.features['dropped_null_liq_price'] == 1
    assert d.features['cluster_positions'] == 2


def test_other_symbols_never_reach_the_cluster(tmp_path):
    rows = (_two_snapshots('SHORT', n=3, symbol='ETH')
            + _cluster_rows(NOW - 30, 'LONG', n=3)
            + _cluster_rows(NOW, 'LONG', n=3))
    d = _strategy(_make_db(tmp_path, rows)).evaluate(_ctx())
    assert d.action == 'ENTER', d.reason
    assert d.primary_leg.outcome_side == 'Down'
    # The ETH rows kept the snapshot fresh but never entered the maths.
    assert d.features['positions_symbol_match'] == 3


def test_small_positions_are_dropped_below_the_whale_floor(tmp_path):
    rows = _cluster_rows(NOW - 30, 'LONG') + _cluster_rows(
        NOW, 'LONG', size_usd=WHALE_MIN_USD - 1.0)
    d = _strategy(_make_db(tmp_path, rows)).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'no_liq_cluster_near_spot'
    assert d.features['dropped_below_whale_min'] == 3


# ---------------------------------------------------------------------------
# 5. accounting identity (convention 20)
# ---------------------------------------------------------------------------
def test_position_accounting_identity_holds_on_a_mixed_snapshot():
    rows = [
        {'side': 'LONG', 'size_usd': 150_000.0,
         'liq_price': _bps_below(SPOT, 10.0)},                # near
        {'side': 'SHORT', 'size_usd': 150_000.0,
         'liq_price': _bps_above(SPOT, 10.0)},                # near
        {'side': 'LONG', 'size_usd': 150_000.0,
         'liq_price': _bps_below(SPOT, 900.0)},               # too far
        {'side': 'LONG', 'size_usd': 10.0,
         'liq_price': _bps_below(SPOT, 10.0)},                # too small
        {'side': 'LONG', 'size_usd': 150_000.0,
         'liq_price': None},                                  # null liq
        {'side': 'LONG', 'size_usd': 150_000.0,
         'liq_price': _bps_above(SPOT, 10.0)},                # already passed
        {'side': 'sideways', 'size_usd': 150_000.0,
         'liq_price': 1.0},                                   # unusable side
        {'side': 'LONG', 'size_usd': 'not-a-number',
         'liq_price': 1.0},                                   # unparseable
    ]
    clusters, counts = build_clusters(rows, SPOT)
    dropped = sum(v for k, v in counts.items() if k.startswith('dropped_'))
    assert counts['positions_considered'] == 8
    assert counts['positions_near'] == 2
    assert counts['positions_considered'] - dropped == counts['positions_near']
    # Every drop cause got its own counter; none pooled.
    assert counts['dropped_not_near_spot'] == 1
    assert counts['dropped_below_whale_min'] == 1
    assert counts['dropped_null_liq_price'] == 1
    assert counts['dropped_liq_price_passed'] == 1
    assert counts['dropped_unusable_side'] == 1
    assert counts['dropped_unparseable'] == 1
    assert set(clusters) == {'LONG', 'SHORT'}
    assert isinstance(clusters['LONG'], LiqCluster)


def test_near_bps_boundary_is_inclusive_and_the_next_bp_is_not():
    inside = [{'side': 'LONG', 'size_usd': 150_000.0,
               'liq_price': _bps_below(SPOT, NEAR_BPS)}]
    outside = [{'side': 'LONG', 'size_usd': 150_000.0,
                'liq_price': _bps_below(SPOT, NEAR_BPS + 1.0)}]
    assert build_clusters(inside, SPOT)[1]['positions_near'] == 1
    assert build_clusters(outside, SPOT)[1]['positions_near'] == 0


# ---------------------------------------------------------------------------
# 5b. THE SECOND LOCK (D-288)
#
# His bot arms on the whale and then refuses to trade until a real liquidation
# print of >= $5,000 lands on the SAME SIDE within 120 seconds. Four things have
# to be true and each is its own test, because three of them fail silently:
#
#   - a qualifying print lets the armed trade through and stamps second_lock_ok
#   - a silent tape blocks it, and says `no_recent_liquidation` (a RESULT)
#   - a print 121 seconds ago is not a print (the window is the lock)
#   - a missing/short/stale recorder degrades through the FEED's own four
#     NOT_TESTED names, never through a result name (convention 11)
#
# The side map gets a test of its own for the same reason the direction pair
# does: matching the WRONG side does not raise, does not drop a row and does not
# move a counter. It just confirms every arm with the flow that refutes it.
# ---------------------------------------------------------------------------
def test_the_two_side_maps_agree(tmp_path):
    """cluster side -> liquidated side -> outcome must equal the direct map.

    This is the wiring test for the second lock's direction. A flip in EITHER
    module - `CLUSTER_SIDE_TO_LIQUIDATED_SIDE` here or `continuation_outcome`
    in liquidation_feed - breaks it. Reading the two files and agreeing they
    look right is convention 22, not a test.
    """
    for cluster_side, outcome in CLUSTER_SIDE_TO_OUTCOME.items():
        liquidated = CLUSTER_SIDE_TO_LIQUIDATED_SIDE[cluster_side]
        assert continuation_outcome(liquidated) == outcome, cluster_side
    # And it is not degenerate: the two sides map to two different values.
    assert (CLUSTER_SIDE_TO_LIQUIDATED_SIDE['LONG']
            != CLUSTER_SIDE_TO_LIQUIDATED_SIDE['SHORT'])


def test_a_qualifying_liquidation_lets_the_armed_trade_through(tmp_path):
    """(a) arm passes + a matching print inside 120s -> ENTER."""
    db = _make_db(tmp_path, _two_snapshots('LONG'),
                  liq_rows=((200.0, 'long', 1.0),
                            (30.0, 'long', 12_500.0)))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'ENTER', d.reason
    assert d.features['second_lock_wired'] is True
    assert d.features['second_lock_ok'] is True
    assert d.features['second_lock_wanted_side'] == 'long'
    assert d.features['second_lock_matched_usd'] == pytest.approx(12_500.0)
    assert d.features['second_lock_matched_count'] == 1
    assert d.features['second_lock_window_sec'] == SECOND_LOCK_WINDOW_SEC
    assert d.features['second_lock_min_usd'] == SECOND_LOCK_MIN_USD


def test_an_armed_whale_with_a_silent_tape_does_not_trade(tmp_path):
    """(b) arm passes, nothing printed -> declined, and it is a RESULT."""
    db = _make_db(tmp_path, _two_snapshots('LONG'),
                  # A live recorder with two minutes of history and no BTC
                  # prints on the long side at all. The short-side rows keep
                  # the feed healthy so this is not a feed failure in disguise.
                  liq_rows=((200.0, 'short', 40_000.0),
                            (5.0, 'short', 40_000.0)))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'no_recent_liquidation'
    assert d.reason not in NO_DATA_REASONS       # this one RAN
    assert d.features['second_lock_ok'] is False
    assert d.features['second_lock_matched_usd'] == 0.0
    assert d.features['second_lock_matched_count'] == 0
    # The arm itself was real and is still on the row, so an analyst can count
    # "armed but unconfirmed" separately from "never armed".
    assert d.features['cluster_side'] == 'LONG'
    assert d.features['outcome_side'] == 'Down'


def test_a_liquidation_outside_the_window_is_not_a_liquidation(tmp_path):
    """(c) the same print, 121s ago instead of 119s, blocks the entry.

    The filler sits 400s back rather than the usual 200s: `history_span_sec` is
    newest-minus-oldest, so a 119s-old newest row and a 200s-old filler span
    only 81 seconds and would trip `liquidation_history_too_short` before the
    window test could run. That would still fail the assertion below, for
    entirely the wrong reason.
    """
    inside = _make_db(tmp_path, _two_snapshots('LONG'), name='in.db',
                      liq_rows=((400.0, 'long', 1.0),
                                (119.0, 'long', 50_000.0)))
    outside = _make_db(tmp_path, _two_snapshots('LONG'), name='out.db',
                       liq_rows=((400.0, 'long', 1.0),
                                 (121.0, 'long', 50_000.0)))
    assert _strategy(inside).evaluate(_ctx()).action == 'ENTER'
    d = _strategy(outside).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'no_recent_liquidation'


def test_the_wrong_side_printing_does_not_confirm_the_arm(tmp_path):
    """A LONG cluster is not confirmed by SHORTS being liquidated.

    The pair to `..._silent_tape`: there the tape was empty on our side, here
    it is loud on the other one. An inverted side map passes both of the
    single-sided tests and fails this.
    """
    db = _make_db(tmp_path, _two_snapshots('SHORT'),
                  liq_rows=((200.0, 'long', 1.0),
                            (10.0, 'long', 500_000.0)))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'no_recent_liquidation'
    assert d.features['second_lock_wanted_side'] == 'short'
    # The flow WAS seen, it was just the wrong side - so the feature row proves
    # the query ran rather than that the tape was empty.
    assert d.features['liq_long_usd'] == pytest.approx(500_000.0)


def test_a_print_under_the_floor_is_its_own_reason(tmp_path):
    """$4,999 is not $5,000, and it is not the same fact as a silent tape.

    Two reasons because only one of them moves when we change our mind about
    the floor (convention 20).
    """
    db = _make_db(tmp_path, _two_snapshots('LONG'),
                  liq_rows=((200.0, 'long', 1.0),
                            (10.0, 'long', SECOND_LOCK_MIN_USD - 1.0)))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == 'liquidation_below_second_lock_min'
    assert d.reason != 'no_recent_liquidation'
    assert d.features['second_lock_ok'] is False
    assert d.features['second_lock_matched_count'] == 1


def test_the_floor_is_inclusive_at_exactly_the_minimum(tmp_path):
    db = _make_db(tmp_path, _two_snapshots('LONG'),
                  liq_rows=((200.0, 'long', 1.0),
                            (10.0, 'long', SECOND_LOCK_MIN_USD)))
    assert _strategy(db).evaluate(_ctx()).action == 'ENTER'


@pytest.mark.parametrize('liq_rows,create_liq_table,expected', [
    # (d) the recorder has never run: the table is not there.
    ((), False, 'liquidation_table_missing'),
    # The table exists and holds nothing for our symbol.
    ((), True, 'liquidation_feed_empty'),
    # 30 seconds of tape cannot answer a question about 120 seconds.
    (((30.0, 'long', 50_000.0), (0.0, 'long', 50_000.0)), True,
     'liquidation_history_too_short'),
    # The recorder died an hour ago. Staleness is checked BEFORE history
    # length, so this must NOT report as too-short.
    (((4000.0, 'long', 50_000.0), (3600.0, 'long', 50_000.0)), True,
     'liquidation_feed_stale'),
])
def test_a_broken_recorder_degrades_through_its_own_named_reasons(
        tmp_path, liq_rows, create_liq_table, expected):
    db = _make_db(tmp_path, _two_snapshots('LONG'), liq_rows=liq_rows,
                  create_liq_table=create_liq_table,
                  name='r{}.db'.format(abs(hash(expected)) % 10_000))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'SKIP'
    assert d.reason == expected
    # NOT_TESTED, never a result. This is the whole point of reusing the feed
    # module's names instead of inventing local ones.
    assert d.reason in NO_DATA_REASONS
    assert d.reason in SKIP_REASONS
    assert d.features['second_lock_ok'] is False
    # The arm still happened, so a dead recorder is distinguishable from a
    # quiet market in the log rather than only in the reason string.
    assert d.features['cluster_side'] == 'LONG'


def test_the_second_lock_is_not_reached_before_the_arm_forms(tmp_path):
    """No cluster, no lock. The DB is never even consulted.

    Ordering matters: if the lock ran first, every quiet hour would log a
    liquidation reason and `no_liq_cluster_near_spot` would stop being the
    honest headline count of "the whale feed found nothing".
    """
    db = _make_db(tmp_path, _two_snapshots('LONG', bps=200.0),
                  create_liq_table=False)
    d = _strategy(db).evaluate(_ctx())
    assert d.reason == 'no_liq_cluster_near_spot'
    assert 'second_lock_ok' not in d.features


def test_the_second_lock_runs_before_the_timing_and_book_gates(tmp_path):
    """A late window with no confirming print reports the missing print.

    `late_in_window` would read as "we had a signal and ran out of time". We
    did not have a signal.
    """
    db = _make_db(tmp_path, _two_snapshots('LONG'),
                  liq_rows=((200.0, 'short', 40_000.0),
                            (5.0, 'short', 40_000.0)))
    d = _strategy(db).evaluate(_ctx(seconds_into_window=290.0))
    assert d.reason == 'no_recent_liquidation'


def test_the_second_lock_asks_about_its_own_symbol(tmp_path):
    """An ETH instance must not be confirmed by a BTC cascade.

    `hyperliquid_positions.symbol` is a bare coin and `liquidations.symbol` is a
    venue contract name, so the join is a prefix match built from the strategy's
    own symbol. Hardcoding 'BTC%' would make every asset read BTC's tape and
    every ETH signal would be confirmed by something that has nothing to do
    with it.
    """
    rows = _two_snapshots('LONG', symbol='ETH')
    db = _make_db(tmp_path, rows, liq_symbol='BTCUSDT',
                  liq_rows=((200.0, 'long', 1.0), (10.0, 'long', 500_000.0)))
    d = _strategy(db, symbol='ETH').evaluate(_ctx())
    assert d.action == 'SKIP'
    # BTC's tape is invisible to an ETH instance, so this is 'the table holds
    # nothing for our symbol', not a confirmation.
    assert d.reason == 'liquidation_feed_empty'
    assert d.features['liq_symbol_like'] == 'ETH%'


def test_the_second_lock_never_writes(tmp_path):
    """Same guard as the whale feed: the handle is read-only by URI."""
    db = _make_db(tmp_path, _two_snapshots('LONG'))
    assert _strategy(db).evaluate(_ctx()).action == 'ENTER'
    conn = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
    with pytest.raises(sqlite3.OperationalError):
        conn.execute('DELETE FROM liquidations')
    conn.close()


# ---------------------------------------------------------------------------
# 6. the price cap and the book gates
# ---------------------------------------------------------------------------
def test_ask_above_the_cap_is_refused(tmp_path):
    db = _make_db(tmp_path, _two_snapshots('LONG'))
    d = _strategy(db).evaluate(_ctx(ask=MAX_ENTRY_PRICE + 0.01))
    assert d.action == 'SKIP'
    assert d.reason == 'ask_above_cap'
    assert d.features['cluster_side'] == 'LONG'   # the signal was real


def test_thin_book_is_refused(tmp_path):
    db = _make_db(tmp_path, _two_snapshots('LONG'))
    d = _strategy(db).evaluate(_ctx(ask=0.40, size=1.0))
    assert d.action == 'SKIP'
    assert d.reason == 'insufficient_ask_depth'


def test_no_market_and_no_book_are_separate_reasons(tmp_path):
    db = _make_db(tmp_path, _two_snapshots('LONG'))
    no_market = _strategy(db).evaluate(_ctx(with_book=False))
    assert no_market.reason == 'no_market'

    ctx = _ctx()
    ctx.books = {}                     # market present, book absent
    assert _strategy(db).evaluate(ctx).reason == 'no_orderbook'

    ctx2 = _ctx()
    ctx2.books = {'tok-down': _book('tok-down', asks=[], bids=[(0.3, 100)])}
    assert _strategy(db).evaluate(ctx2).reason == 'no_asks'


def test_late_in_window_is_refused(tmp_path):
    db = _make_db(tmp_path, _two_snapshots('LONG'))
    d = _strategy(db).evaluate(_ctx(seconds_into_window=290.0))
    assert d.action == 'SKIP'
    assert d.reason == 'late_in_window'
    assert d.features['seconds_remaining'] == pytest.approx(10.0)


def test_entry_premium_is_the_walked_book_not_the_cap(tmp_path):
    """Two levels: the effective premium must sit between them, not at the cap."""
    db = _make_db(tmp_path, _two_snapshots('LONG'))
    ctx = _ctx()
    ctx.books['tok-down'] = _book('tok-down',
                                  asks=[(0.40, 4.0), (0.50, 100.0)])
    d = _strategy(db).evaluate(ctx)
    assert d.action == 'ENTER', d.reason
    leg = d.primary_leg
    assert leg.limit_price == MAX_ENTRY_PRICE
    assert 0.40 < leg.premium < 0.50
    assert leg.expected_price == pytest.approx(leg.premium)
    assert d.features['breakeven_win_rate'] == pytest.approx(leg.premium,
                                                             abs=1e-4)


# ---------------------------------------------------------------------------
# 7. the Signal mapping and convention 8
# ---------------------------------------------------------------------------
def test_signal_carries_a_zero_stop_strictly_below_entry(tmp_path):
    db = _make_db(tmp_path, _two_snapshots('SHORT'))
    strat = _strategy(db)
    d = strat.evaluate(_ctx())
    sig = strat.decision_to_signal(d)
    assert sig is not None
    assert sig.pattern == 'PM_near_liq_trigger'
    assert sig.direction == 'bullish'          # Up side
    assert sig.stop == 0.0
    assert sig.target == 1.0
    assert sig.stop < sig.entry <= sig.target


def test_a_skip_never_becomes_a_signal(tmp_path):
    strat = _strategy(str(tmp_path / 'gone.db'))
    d = strat.evaluate(_ctx())
    assert strat.decision_to_signal(d) is None


# ---------------------------------------------------------------------------
# 8. stamping and non-crashing
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('rows,name', [
    ((), 'empty.db'),
    (None, 'notable.db'),
])
def test_vendor_stamps_ride_on_skips_too(tmp_path, rows, name):
    db = (_make_db(tmp_path, create_table=False, name=name) if rows is None
          else _make_db(tmp_path, rows=rows, name=name))
    d = _strategy(db).evaluate(_ctx())
    assert d.features['thresholds_are_unverified_vendor_numbers'] is True
    assert d.features['vendor_claims_no_backtest_exists'] is True
    # A VERSION STAMP, not a verdict. These rows never reach the second lock -
    # the whale feed is missing - and it is still True, because it says which
    # code emitted the row (D-288). `second_lock_ok` is what says whether the
    # lock passed, and it is ABSENT here rather than False.
    assert d.features['second_lock_wired'] is True
    assert 'second_lock_ok' not in d.features
    assert d.features['feed_is_watchlist_not_census'] is True
    assert d.features['paper_mode'] is True


def test_an_entry_stamps_every_auditable_feature(tmp_path):
    db = _make_db(tmp_path, _two_snapshots('LONG'))
    d = _strategy(db).evaluate(_ctx())
    assert d.action == 'ENTER', d.reason
    for key in ('cluster_usd', 'cluster_positions', 'cluster_nearest_bps',
                'cluster_mean_bps', 'cluster_side', 'feed_age_sec',
                'feed_live', 'outcome_side', 'direction_rationale',
                'breakeven_win_rate', 'spot'):
        assert key in d.features, key
    assert d.features['feed_age_sec'] == pytest.approx(0.0, abs=1.0)
    assert d.features['confidence_is_base_rate_not_a_measurement'] is True


def test_features_are_json_safe(tmp_path):
    """Convention 19: no Infinity/NaN may reach a signals row."""
    import json
    db = _make_db(tmp_path, _two_snapshots('LONG'))
    d = _strategy(db).evaluate(_ctx())
    json.dumps(d.to_dict(), allow_nan=False)


@pytest.mark.parametrize('ctx', [
    MarketContext(window_ts=WINDOW_TS),
    MarketContext(window_ts=WINDOW_TS, spot=0.0),
    MarketContext(window_ts=WINDOW_TS, spot=float('nan')),
    MarketContext(window_ts=WINDOW_TS, spot=SPOT),
])
def test_garbage_contexts_never_raise(tmp_path, ctx):
    d = _strategy(_make_db(tmp_path, _two_snapshots('LONG'))).evaluate(ctx)
    assert d.action == 'SKIP'
    assert d.reason in SKIP_REASONS


def test_scan_adapter_returns_none_without_a_book(tmp_path):
    """The scanner contract: no book, no fill price, no signal."""
    strat = _strategy(_make_db(tmp_path, _two_snapshots('LONG')))
    candles = {'closes': [SPOT] * 20, 'opens': [SPOT] * 20,
               'timestamps': [WINDOW_TS - 300 * i for i in range(20)][::-1]}
    assert strat.scan(candles) is None


# ---------------------------------------------------------------------------
# D-296: the kill clock does not run against an empty tape
#
# This is the single most consequential test in this file, because it is the
# only one whose failure mode is a WRONG VERDICT rather than a lost trade. With
# `liquidations` at 0 rows, an unguarded 30-day clause fires with certainty in
# 30 days and writes "killed: under 10 entries" into the record. That reads as
# a measurement of the idea and is entirely a measurement of a dead feed.
# ---------------------------------------------------------------------------
from strategies.polymarket.near_liq_trigger import (  # noqa: E402
    KILL_CLOCK_DAYS, KILL_CLOCK_DEFERRED_EMPTY, KILL_CLOCK_DEFERRED_NO_TABLE,
    KILL_CLOCK_MIN_ENTRIES, KILL_CLOCK_RUNNING, kill_clock_status,
    liquidation_row_count)

LIQ_DDL = """
CREATE TABLE IF NOT EXISTS liquidations (
    id TEXT PRIMARY KEY, ts INTEGER NOT NULL, exchange TEXT NOT NULL,
    symbol TEXT NOT NULL, side TEXT NOT NULL, price REAL NOT NULL,
    qty REAL NOT NULL, value_usd REAL NOT NULL)
"""
DAY_MS = 86_400_000


def _liq_db(tmp_path, rows=(), name='liq.db'):
    path = str(tmp_path / name)
    conn = sqlite3.connect(path)
    conn.execute(LIQ_DDL)
    for i, ts_ms in enumerate(rows):
        conn.execute('insert into liquidations values (?,?,?,?,?,?,?,?)',
                     ('l%d' % i, ts_ms, 'bybit', 'BTCUSDT', 'long',
                      50_000.0, 1.0, 50_000.0))
    conn.commit()
    conn.close()
    return path


def test_empty_tape_defers_the_clock_and_fires_nothing(tmp_path):
    """The state on 2026-08-18: table present, zero rows, forever so far."""
    st = kill_clock_status(_liq_db(tmp_path), entries_to_date=0,
                           now=2_000_000_000.0)
    assert st['clock_running'] is False
    assert st['fired'] is False
    assert st['evaluated'] is False
    assert st['status'] == KILL_CLOCK_DEFERRED_EMPTY
    assert st['clock_started_ms'] is None
    assert st['days_elapsed'] is None


def test_a_missing_table_is_a_different_word_from_an_empty_one(tmp_path):
    """Convention 20. Same consequence today, different owners: one is a
    schema/db problem, the other is a quiet tape."""
    bare = str(tmp_path / 'bare.db')
    sqlite3.connect(bare).close()
    assert liquidation_row_count(bare) is None
    st = kill_clock_status(bare, entries_to_date=0, now=2_000_000_000.0)
    assert st['status'] == KILL_CLOCK_DEFERRED_NO_TABLE
    assert st['status'] != KILL_CLOCK_DEFERRED_EMPTY
    assert (st['clock_running'], st['fired'], st['evaluated']) \
        == (False, False, False)


def test_a_missing_database_file_defers_rather_than_raising(tmp_path):
    st = kill_clock_status(str(tmp_path / 'nope.db'), entries_to_date=0)
    assert st['status'] == KILL_CLOCK_DEFERRED_NO_TABLE
    assert st['fired'] is False


def test_a_hundred_days_of_silence_still_does_not_fire(tmp_path):
    """The exact failure D-296 exists to prevent, at 100 days rather than 30.

    Time alone must never be enough. If this ever goes red, the record has
    started blaming the strategy for the recorder.
    """
    st = kill_clock_status(_liq_db(tmp_path), entries_to_date=0,
                           now=2_000_000_000.0 + 100 * 86_400.0)
    assert st['fired'] is False
    assert st['clock_running'] is False


def test_the_clock_starts_at_the_first_print_not_at_deployment(tmp_path):
    """`min(ts)`, not `max(ts)` and not "now". The tape's first print is the
    first moment the second lock could have had an input."""
    now = 2_000_000_000.0
    first_ms = int((now - 10 * 86_400.0) * 1000)
    db = _liq_db(tmp_path, rows=[first_ms, first_ms + DAY_MS,
                                 first_ms + 5 * DAY_MS])
    st = kill_clock_status(db, entries_to_date=0, now=now)
    assert st['clock_running'] is True
    assert st['status'] == KILL_CLOCK_RUNNING
    assert st['clock_started_ms'] == first_ms
    assert st['days_elapsed'] == pytest.approx(10.0, abs=0.01)
    # Ten days in, the 30-day window has not closed: nothing was evaluated,
    # so nothing fired - and `evaluated` says which of those two it is.
    assert st['evaluated'] is False
    assert st['fired'] is False


def test_it_fires_once_the_tape_has_run_and_the_entries_did_not_come(tmp_path):
    """The clause must still work. A guard that never lets it fire would be
    the opposite error: a strategy that can never be killed."""
    now = 2_000_000_000.0
    first_ms = int((now - (KILL_CLOCK_DAYS + 1) * 86_400.0) * 1000)
    st = kill_clock_status(_liq_db(tmp_path, rows=[first_ms]),
                           entries_to_date=KILL_CLOCK_MIN_ENTRIES - 1, now=now)
    assert st['clock_running'] is True
    assert st['evaluated'] is True
    assert st['fired'] is True


def test_enough_entries_clears_the_clause(tmp_path):
    now = 2_000_000_000.0
    first_ms = int((now - (KILL_CLOCK_DAYS + 1) * 86_400.0) * 1000)
    st = kill_clock_status(_liq_db(tmp_path, rows=[first_ms]),
                           entries_to_date=KILL_CLOCK_MIN_ENTRIES, now=now)
    assert st['evaluated'] is True
    assert st['fired'] is False


def test_an_uncounted_entry_total_is_unevaluated_not_a_pass(tmp_path):
    """Convention 11 again, one level down. `entries_to_date=None` means the
    caller never counted; reporting that as `fired=False, evaluated=True`
    would be a strategy surviving a test nobody ran."""
    now = 2_000_000_000.0
    first_ms = int((now - (KILL_CLOCK_DAYS + 1) * 86_400.0) * 1000)
    st = kill_clock_status(_liq_db(tmp_path, rows=[first_ms]),
                           entries_to_date=None, now=now)
    assert st['clock_running'] is True
    assert st['evaluated'] is False
    assert st['fired'] is False


def test_the_deferral_reason_is_not_in_the_strategys_skip_vocabulary():
    """It is a property of the KILL EVALUATION, not of a market cycle.

    Filing it as a skip reason would put an infrastructure fact into the
    per-window decision log 57 times a cycle and pollute the skip histogram
    the classification table is read from.
    """
    assert KILL_CLOCK_DEFERRED_EMPTY not in SKIP_REASONS
    assert KILL_CLOCK_DEFERRED_NO_TABLE not in SKIP_REASONS


def test_the_status_dict_is_strict_json_serialisable(tmp_path):
    """Convention 19."""
    import json
    json.dumps(kill_clock_status(_liq_db(tmp_path), entries_to_date=0),
               allow_nan=False)


# ---------------------------------------------------------------------------
# D-296, second half: the guard state has to reach the ROW
#
# `kill_clock_status()` above is what an evaluator calls. But the evaluator
# runs weeks later, against a log, and "was the clock running when this row was
# written?" is a property of the row. If it is only ever computed at scoring
# time it gets computed against TODAY's tape, and a month of rows written
# against an empty feed silently inherit a clock that started afterwards.
# ---------------------------------------------------------------------------
from strategies.polymarket.near_liq_trigger import (  # noqa: E402
    kill_clock_row_features)
from strategies.polymarket.liquidation_feed import (  # noqa: E402
    REASON_FEED_EMPTY, REASON_FEED_STALE, REASON_HISTORY_TOO_SHORT,
    REASON_TABLE_MISSING, LiquidationWindow)


def _window(reason=None, rows_total=0, ok=False):
    return LiquidationWindow(ok=ok, reason=reason, lookback_sec=120.0,
                             now_s=1_787_000_000.0, rows_total=rows_total)


def test_an_empty_tape_stamps_the_row_as_deferred():
    """The state the feed is actually in today: table present, zero rows."""
    feats = kill_clock_row_features(_window(REASON_FEED_EMPTY, rows_total=0))
    assert feats['kill_clock_running'] is False
    assert feats['kill_clock_status'] == KILL_CLOCK_DEFERRED_EMPTY
    assert feats['kill_clock_liq_rows_for_symbol'] == 0


def test_no_table_stamps_unknown_and_never_zero():
    """`rows_total` is a dataclass DEFAULT on this path - the query never ran.

    Reporting 0 would claim we read the tape and found it empty. We did not
    read it at all, and the two have different owners: a schema problem and a
    quiet market.
    """
    feats = kill_clock_row_features(_window(REASON_TABLE_MISSING))
    assert feats['kill_clock_status'] == KILL_CLOCK_DEFERRED_NO_TABLE
    assert feats['kill_clock_liq_rows_for_symbol'] is None
    assert kill_clock_row_features(None)['kill_clock_liq_rows_for_symbol'] is None


def test_one_row_to_date_starts_the_clock():
    """The ruling is ">= 1 row TO DATE", so this is the whole boundary."""
    assert kill_clock_row_features(
        _window(REASON_FEED_EMPTY, rows_total=1))['kill_clock_running'] is True
    assert kill_clock_row_features(
        _window(REASON_FEED_EMPTY, rows_total=0))['kill_clock_running'] is False


def test_a_stale_or_short_tape_that_has_printed_keeps_the_clock_running():
    """Deferral is about "has it EVER printed", not "is it printing now".

    A recorder that ran for a week and died has produced evidence. Pausing the
    clock for staleness would make the guard un-expirable: any feed outage
    would rewind it, and the clause could never fire on a strategy whose feed
    is merely unreliable.
    """
    for reason in (REASON_FEED_STALE, REASON_HISTORY_TOO_SHORT):
        feats = kill_clock_row_features(_window(reason, rows_total=412))
        assert feats['kill_clock_running'] is True, reason
        assert feats['kill_clock_status'] == KILL_CLOCK_RUNNING
        assert feats['kill_clock_liq_rows_for_symbol'] == 412


def test_an_ok_window_carries_the_thresholds_too():
    """The numbers ride on the row so a reader never reads them off a docstring."""
    feats = kill_clock_row_features(_window(None, rows_total=9, ok=True))
    assert feats['kill_clock_days_required'] == KILL_CLOCK_DAYS
    assert feats['kill_clock_min_entries'] == KILL_CLOCK_MIN_ENTRIES


def test_the_row_features_are_strict_json_serialisable():
    """Convention 19: these go into `features_json`."""
    import json
    for w in (None, _window(REASON_TABLE_MISSING),
              _window(REASON_FEED_EMPTY, rows_total=0),
              _window(None, rows_total=5, ok=True)):
        json.dumps(kill_clock_row_features(w), allow_nan=False)


def test_the_strategy_stamps_the_clock_on_every_second_lock_row():
    """Convention 22: the helper existing is not the helper being wired.

    Stamped from the window the strategy ALREADY read, so the guard adds no
    query to a path that runs 57 times a cycle.
    """
    import os
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'strategies', 'polymarket', 'near_liq_trigger.py')
    src = open(path).read()
    assert 'feats.update(kill_clock_row_features(window))' in src
    # ...and it must sit ABOVE the `not window.ok` early return, or the one
    # state the guard exists for - an empty tape - is the one it never stamps.
    assert (src.index('feats.update(kill_clock_row_features(window))')
            < src.index('if not window.ok:'))
