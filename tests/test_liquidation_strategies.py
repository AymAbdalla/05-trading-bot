"""Tests for the two liquidation-driven Polymarket strategies.

Offline only. No network, no live database, no recorder. Every test builds its
own sqlite file under `tmp_path` with the recorder's exact schema and writes
synthetic rows, so nothing here depends on whether the recorder has ever run.

Three jobs, in order of how much damage failing them does:

  1. **The side semantic is not flipped.** `liquidations.side` is WHICH SIDE GOT
     LIQUIDATED - the recorder already inverted the exchange's order side. A
     second inversion inside a strategy raises nothing, drops no rows and moves
     no counter; it just makes every signal exactly backwards.
     `test_opposite_liquidated_side_produces_the_opposite_outcome` runs the SAME
     context twice with only the liquidated side (and the price move) flipped
     and asserts the outcome side flips with it. That is the wiring test; a
     docstring claiming the mapping is right would not be (convention 22).

  2. **The four no-data reasons are four reasons.** Table missing, feed empty,
     history too short and feed stale are distinct strings and none of them is
     `no_cascade`. Convention 11: NOT_TESTED means "could not run", never "ran
     and found nothing". Convention 20: two drop causes never share one number.

  3. **Both strategies are provably ALIVE.** A synthetic context that satisfies
     every rule produces an ENTER. Without this, "it never fired" and "it fired
     and lost" are indistinguishable in the graveyard.

There is deliberately NO harness sweep here. Per D-268 these are NOT_TESTED
until a resolution-PnL harness exists.
"""
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.types import (Market, Orderbook, Outcome,  # noqa: E402
                                     PriceLevel)
from strategies.polymarket.base import MarketContext, Window  # noqa: E402
from strategies.polymarket.liq_cascade_chaser import \
    LiqCascadeChaser  # noqa: E402
from strategies.polymarket.small_liq_continuation import \
    SmallLiqContinuation  # noqa: E402
import strategies.polymarket.liquidation_feed as feed  # noqa: E402

# The recorder's schema, copied verbatim so a divergence shows up as a failing
# test here rather than as an empty result in the shadow log.
SCHEMA_SQL = """
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

WINDOW_TS = 1699999800          # divisible by 300
INTO_WINDOW = 120.0             # elapsed 120s: inside chaser's 10-180s band and
                                # leaves 180s remaining, inside small-liq's
                                # 60-240s band. One number satisfies both.
NOW_S = WINDOW_TS + INTO_WINDOW
OPEN_PX = 60000.0
UP_TOKEN = 'tok-up'
DOWN_TOKEN = 'tok-down'


# ---------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------

def _make_db(path, rows=(), create_table=True, now_s=NOW_S):
    """Write a liquidations table with `rows` = (offset_sec, side, usd) tuples.

    `offset_sec` is seconds BEFORE `now_s`, so a test reads as "a $50k long
    flush ten seconds ago" rather than as a raw epoch.

    `now_s` must match the context the strategy will be handed. A strategy's
    clock is `window_ts + seconds_into_window`, so a test that moves
    `seconds_into_window` and leaves the tape alone has written rows in the
    FUTURE relative to that clock - and the query correctly refuses to see them.
    That is a real property worth stating: no decision may read a liquidation
    that had not printed yet.
    """
    conn = sqlite3.connect(str(path))
    try:
        if create_table:
            conn.executescript(SCHEMA_SQL)
            for i, (offset_sec, side, usd) in enumerate(rows):
                ts_ms = int((now_s - offset_sec) * 1000)
                conn.execute(
                    'INSERT INTO liquidations '
                    '(id, ts, exchange, symbol, side, price, qty, value_usd) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                    ('row-%d' % i, ts_ms, 'binance', 'BTCUSDT', side,
                     OPEN_PX, usd / OPEN_PX, usd))
        else:
            # A database that exists for other reasons (the shadow loop makes
            # one) but that the recorder has never touched.
            conn.execute('CREATE TABLE IF NOT EXISTS unrelated (x INTEGER)')
        conn.commit()
    finally:
        conn.close()
    return str(path)


def _book(token, asks=(), bids=()):
    return Orderbook(
        token_id=token,
        asks=tuple(PriceLevel(p, s) for p, s in sorted(asks)),
        bids=tuple(PriceLevel(p, s) for p, s in sorted(bids, reverse=True)),
    )


def _market():
    return Market(
        id='btc-updown-5m', question='BTC up or down?', slug='btc-updown-5m',
        condition_id='c-1',
        outcomes=(Outcome('Up', UP_TOKEN), Outcome('Down', DOWN_TOKEN)),
    )


def _windows(n=16):
    """`n` contiguous 5m bars, the last of which OPENED at WINDOW_TS.

    The in-progress window has to be present and matched on timestamp:
    `LiqCascadeChaser.window_open` refuses to substitute the previous bar's
    open, which would measure the move from the wrong place.
    """
    out = []
    for i in range(n - 1, -1, -1):
        ts = WINDOW_TS - i * 300
        out.append(Window(ts=ts, open=OPEN_PX, close=OPEN_PX,
                          direction='UP', source='price'))
    return out


def _ctx(spot=OPEN_PX, books=None, market=True, into=INTO_WINDOW,
         windows=None):
    """A context with BOTH books present, so a missing book can never be the
    accidental reason a side-flip test 'passes'."""
    if books is None:
        books = {
            UP_TOKEN: _book(UP_TOKEN, asks=[(0.60, 100.0), (0.62, 100.0)],
                            bids=[(0.57, 100.0)]),
            DOWN_TOKEN: _book(DOWN_TOKEN, asks=[(0.60, 100.0), (0.62, 100.0)],
                              bids=[(0.57, 100.0)]),
        }
    return MarketContext(
        window_ts=WINDOW_TS,
        windows=_windows() if windows is None else windows,
        market=_market() if market else None,
        books=books,
        spot=spot,
        seconds_into_window=into,
    )


def _small_books(ask=0.35, size=100.0):
    """Books priced inside small-liq's 0.30-0.45 band, on both sides."""
    return {
        UP_TOKEN: _book(UP_TOKEN, asks=[(ask, size)], bids=[(ask - 0.02, size)]),
        DOWN_TOKEN: _book(DOWN_TOKEN, asks=[(ask, size)],
                          bids=[(ask - 0.02, size)]),
    }


def _bps(base, bps):
    return base * (1.0 + bps / 10_000.0)


def _tape(side, usd=50_000.0):
    """One big burst 10s ago, one crumb 10 minutes ago.

    The crumb exists only so `history_span_sec` clears the 120s lookback - it
    sits OUTSIDE the window and never contributes to the sum.
    """
    return [(10.0, side, usd), (600.0, side, 1.0)]


# ---------------------------------------------------------------------------
# 1. the four no-data reasons
# ---------------------------------------------------------------------------

def test_missing_database_file_is_table_missing(tmp_path):
    path = str(tmp_path / 'nope.db')
    for strat in (LiqCascadeChaser(db_path=path),
                  SmallLiqContinuation(db_path=path)):
        d = strat.evaluate(_ctx())
        assert d.action == 'SKIP'
        assert d.reason == feed.REASON_TABLE_MISSING
        assert d.features['liq_feed_ok'] is False


def test_database_without_the_table_is_table_missing(tmp_path):
    path = _make_db(tmp_path / 'no_table.db', create_table=False)
    for strat in (LiqCascadeChaser(db_path=path),
                  SmallLiqContinuation(db_path=path)):
        d = strat.evaluate(_ctx())
        assert d.reason == feed.REASON_TABLE_MISSING


def test_missing_file_and_missing_table_stay_separable(tmp_path):
    """One operator-facing reason, two causes, still countable (convention 20)."""
    absent = feed.read_liquidation_window(
        NOW_S, 120.0, db_path=str(tmp_path / 'absent.db'))
    empty_schema = feed.read_liquidation_window(
        NOW_S, 120.0, db_path=_make_db(tmp_path / 'x.db', create_table=False))
    assert absent.reason == empty_schema.reason == feed.REASON_TABLE_MISSING
    assert absent.missing_cause == 'no_such_file'
    assert empty_schema.missing_cause == 'no_such_table'


def test_empty_table_is_feed_empty(tmp_path):
    path = _make_db(tmp_path / 'empty.db', rows=())
    for strat in (LiqCascadeChaser(db_path=path),
                  SmallLiqContinuation(db_path=path)):
        d = strat.evaluate(_ctx())
        assert d.reason == feed.REASON_FEED_EMPTY
        assert d.features['liq_rows_total'] == 0


def test_thirty_seconds_of_history_is_history_too_short(tmp_path):
    # Recorder started 30s ago. The rows it has are huge; the point is that a
    # 120s question cannot be answered from 30s of tape, whatever is in it.
    path = _make_db(tmp_path / 'short.db',
                    rows=[(5.0, 'short', 400_000.0), (30.0, 'short', 400_000.0)])
    for strat in (LiqCascadeChaser(db_path=path),
                  SmallLiqContinuation(db_path=path)):
        d = strat.evaluate(_ctx(spot=_bps(OPEN_PX, 30)))
        assert d.reason == feed.REASON_HISTORY_TOO_SHORT
        assert d.features['liq_history_span_sec'] < 120.0


def test_dead_recorder_is_feed_stale_not_no_cascade(tmp_path):
    # Two hours of good tape that stopped an hour ago.
    path = _make_db(tmp_path / 'stale.db',
                    rows=[(3600.0, 'short', 90_000.0),
                          (7200.0, 'short', 90_000.0)])
    for strat in (LiqCascadeChaser(db_path=path),
                  SmallLiqContinuation(db_path=path)):
        d = strat.evaluate(_ctx(spot=_bps(OPEN_PX, 30)))
        assert d.reason == feed.REASON_FEED_STALE
        assert d.features['liq_newest_age_sec'] > 900.0


def test_stale_beats_history_too_short_when_both_apply(tmp_path):
    """A recorder that lived 20 seconds an hour ago trips both. The one an
    operator has to act on is 'it is dead', so that is the reason reported."""
    path = _make_db(tmp_path / 'both.db',
                    rows=[(3600.0, 'short', 90_000.0),
                          (3620.0, 'short', 90_000.0)])
    w = feed.read_liquidation_window(NOW_S, 120.0, db_path=path)
    assert w.reason == feed.REASON_FEED_STALE
    assert w.history_span_sec < 120.0     # the other condition really is true


def test_the_no_data_reasons_are_all_distinct_strings():
    assert len(set(feed.NO_DATA_REASONS)) == 4
    assert 'no_cascade' not in feed.NO_DATA_REASONS
    for r in feed.NO_DATA_REASONS:
        assert r.startswith('liquidation_')


# ---------------------------------------------------------------------------
# 2. no_cascade is a RESULT, not a no-data reason
# ---------------------------------------------------------------------------

def test_quiet_tape_is_no_cascade_and_the_feed_reads_live(tmp_path):
    path = _make_db(tmp_path / 'quiet.db',
                    rows=[(10.0, 'short', 500.0), (600.0, 'short', 500.0)])
    for strat in (LiqCascadeChaser(db_path=path),
                  SmallLiqContinuation(db_path=path)):
        d = strat.evaluate(_ctx(spot=_bps(OPEN_PX, 30)))
        assert d.reason == 'no_cascade'
        assert d.features['liq_feed_live'] is True
        assert d.features['liq_total_usd'] == 500.0


def test_perfectly_balanced_tape_is_its_own_reason(tmp_path):
    path = _make_db(tmp_path / 'balanced.db',
                    rows=[(10.0, 'short', 80_000.0), (11.0, 'long', 80_000.0),
                          (600.0, 'short', 1.0)])
    for strat in (LiqCascadeChaser(db_path=path),
                  SmallLiqContinuation(db_path=path)):
        d = strat.evaluate(_ctx(spot=_bps(OPEN_PX, 30)))
        assert d.reason == 'balanced_liq_tape'
        assert d.features['liq_dominant_side'] is None


# ---------------------------------------------------------------------------
# 3. the strategies are alive
# ---------------------------------------------------------------------------

def test_cascade_chaser_enters_up_on_a_short_flush(tmp_path):
    path = _make_db(tmp_path / 'short_flush.db', rows=_tape('short'))
    d = LiqCascadeChaser(db_path=path).evaluate(_ctx(spot=_bps(OPEN_PX, 30)))
    assert d.action == 'ENTER', d.reason
    leg = d.primary_leg
    # shorts liquidated -> forced BUYING -> price up -> buy Up
    assert leg.outcome_side == 'Up'
    assert leg.order_type == 'taker'
    assert 0.50 <= leg.premium <= 0.85
    assert d.features['liq_dominant_side'] == 'short'
    assert d.features['move_in_liq_direction_bps'] > 15.0
    # The dropped tick-rate gate is stamped so nobody compares this population
    # to moondevonyt's 95%/58.8% figures by accident.
    assert d.features['tick_rate_confirmation_applied'] is False
    assert d.features['claimed_win_rate_is_unverified_vendor_number'] is True


def test_small_liq_enters_down_on_a_long_flush(tmp_path):
    path = _make_db(tmp_path / 'long_flush.db', rows=_tape('long'))
    d = SmallLiqContinuation(db_path=path).evaluate(
        _ctx(spot=_bps(OPEN_PX, -30), books=_small_books()))
    assert d.action == 'ENTER', d.reason
    leg = d.primary_leg
    # longs liquidated -> forced SELLING -> price down -> buy Down
    assert leg.outcome_side == 'Down'
    assert 0.30 <= leg.premium <= 0.45
    assert d.features['liq_dominant_side'] == 'long'
    assert d.features['size_kicker_applied'] is False


def test_opposite_liquidated_side_produces_the_opposite_outcome(tmp_path):
    """THE wiring test. Same context, same books on both sides, only the
    liquidated side (and the price move it caused) flipped.

    If anything in this chain inverts `side` a second time, both rows come back
    with the same outcome and this fails. Nothing else in the suite would."""
    short_db = _make_db(tmp_path / 'shorts.db', rows=_tape('short'))
    long_db = _make_db(tmp_path / 'longs.db', rows=_tape('long'))

    up = LiqCascadeChaser(db_path=short_db).evaluate(
        _ctx(spot=_bps(OPEN_PX, 30)))
    down = LiqCascadeChaser(db_path=long_db).evaluate(
        _ctx(spot=_bps(OPEN_PX, -30)))
    assert (up.action, down.action) == ('ENTER', 'ENTER')
    assert up.primary_leg.outcome_side == 'Up'
    assert down.primary_leg.outcome_side == 'Down'

    s_up = SmallLiqContinuation(db_path=short_db).evaluate(
        _ctx(spot=_bps(OPEN_PX, 30), books=_small_books()))
    s_down = SmallLiqContinuation(db_path=long_db).evaluate(
        _ctx(spot=_bps(OPEN_PX, -30), books=_small_books()))
    assert (s_up.action, s_down.action) == ('ENTER', 'ENTER')
    assert s_up.primary_leg.outcome_side == 'Up'
    assert s_down.primary_leg.outcome_side == 'Down'

    # And the mapping itself, stated once, asserted directly.
    assert feed.continuation_outcome('short') == 'Up'
    assert feed.continuation_outcome('long') == 'Down'
    assert feed.continuation_outcome(None) is None


def test_signal_becomes_a_signal_with_a_stop_strictly_below_entry(tmp_path):
    """Convention 8, through the real `decision_to_signal` path."""
    path = _make_db(tmp_path / 'sig.db', rows=_tape('short'))
    strat = LiqCascadeChaser(db_path=path)
    sig = strat.decision_to_signal(strat.evaluate(_ctx(spot=_bps(OPEN_PX, 30))))
    assert sig is not None
    assert sig.direction == 'bullish'
    assert sig.stop < sig.entry <= sig.target == 1.00
    assert sig.pattern == 'PM_liq_cascade_chaser'


# ---------------------------------------------------------------------------
# 4. the gates that are the strategy
# ---------------------------------------------------------------------------

def test_cascade_chaser_refuses_a_move_against_the_liquidations(tmp_path):
    """Shorts were flushed but price went DOWN: the book absorbed the flow, so
    the continuation signature is not there."""
    path = _make_db(tmp_path / 'against.db', rows=_tape('short'))
    d = LiqCascadeChaser(db_path=path).evaluate(_ctx(spot=_bps(OPEN_PX, -30)))
    assert d.reason == 'move_not_confirming_liq_direction'
    assert d.features['move_in_liq_direction_bps'] < 0


def test_cascade_chaser_refuses_a_move_that_is_too_small(tmp_path):
    path = _make_db(tmp_path / 'small_move.db', rows=_tape('short'))
    d = LiqCascadeChaser(db_path=path).evaluate(_ctx(spot=_bps(OPEN_PX, 5)))
    assert d.reason == 'move_not_confirming_liq_direction'


def test_cascade_chaser_refuses_a_wide_book(tmp_path):
    path = _make_db(tmp_path / 'wide.db', rows=_tape('short'))
    books = {UP_TOKEN: _book(UP_TOKEN, asks=[(0.60, 100.0)],
                             bids=[(0.40, 100.0)]),
             DOWN_TOKEN: _book(DOWN_TOKEN, asks=[(0.60, 100.0)],
                               bids=[(0.40, 100.0)])}
    d = LiqCascadeChaser(db_path=path).evaluate(
        _ctx(spot=_bps(OPEN_PX, 30), books=books))
    assert d.reason == 'spread_too_wide'


def test_cascade_chaser_refuses_below_its_own_band(tmp_path):
    """Below 0.50 the book flatly disagrees the cascade side is winning."""
    path = _make_db(tmp_path / 'cheap.db', rows=_tape('short'))
    d = LiqCascadeChaser(db_path=path).evaluate(
        _ctx(spot=_bps(OPEN_PX, 30), books=_small_books()))
    assert d.reason == 'effective_ask_below_band'


def test_cascade_chaser_stamps_the_vendor_breakeven_split(tmp_path):
    """At 0.62 the entry is above moondevonyt's own 58.8% directional rate, so
    his own arithmetic makes it negative EV. We do not move his band; we stamp
    the split so the scorer can test it without a re-run."""
    path = _make_db(tmp_path / 'expensive.db', rows=_tape('short'))
    books = {UP_TOKEN: _book(UP_TOKEN, asks=[(0.62, 100.0)],
                             bids=[(0.60, 100.0)]),
             DOWN_TOKEN: _book(DOWN_TOKEN, asks=[(0.62, 100.0)],
                               bids=[(0.60, 100.0)])}
    d = LiqCascadeChaser(db_path=path).evaluate(
        _ctx(spot=_bps(OPEN_PX, 30), books=books))
    assert d.action == 'ENTER', d.reason
    assert d.features['entry_above_vendor_breakeven'] is True
    assert d.features['breakeven_win_rate'] == 0.62


def test_small_liq_hands_a_mega_cascade_to_the_other_bot(tmp_path):
    path = _make_db(tmp_path / 'mega.db', rows=_tape('long', usd=750_000.0))
    d = SmallLiqContinuation(db_path=path).evaluate(
        _ctx(spot=_bps(OPEN_PX, -30), books=_small_books()))
    assert d.reason == 'mega_liq_belongs_to_cascade_chaser'


def test_small_liq_applies_the_size_kicker_above_100k(tmp_path):
    path = _make_db(tmp_path / 'kicker.db', rows=_tape('long', usd=250_000.0))
    d = SmallLiqContinuation(db_path=path).evaluate(
        _ctx(spot=_bps(OPEN_PX, -30), books=_small_books()))
    assert d.action == 'ENTER', d.reason
    assert d.features['size_kicker_applied'] is True
    assert d.features['stake_usd'] == 7.5


def test_small_liq_refuses_outside_its_time_band(tmp_path):
    """The tape is rebuilt against each context's own clock. A strategy's clock
    is `window_ts + seconds_into_window`, so moving the second moves the first,
    and the burst has to be re-dated with it or it lands in the future."""
    early_ctx = _ctx(spot=_bps(OPEN_PX, -30), books=_small_books(), into=10.0)
    late_ctx = _ctx(spot=_bps(OPEN_PX, -30), books=_small_books(), into=280.0)
    early_db = _make_db(tmp_path / 'early.db', rows=_tape('long'),
                        now_s=WINDOW_TS + 10.0)
    late_db = _make_db(tmp_path / 'late.db', rows=_tape('long'),
                       now_s=WINDOW_TS + 280.0)

    early = SmallLiqContinuation(db_path=early_db).evaluate(early_ctx)
    late = SmallLiqContinuation(db_path=late_db).evaluate(late_ctx)
    assert early.reason == 'window_not_open'          # 290s left, band tops at 240
    assert late.reason == 'too_close_to_resolution'   # 20s left, band floors at 60


def test_a_decision_never_reads_a_liquidation_from_the_future(tmp_path):
    """The lookback is closed at `now`. A row stamped after the context's own
    clock is not in the window, whatever the wall clock says."""
    path = _make_db(tmp_path / 'future.db',
                    rows=[(-30.0, 'short', 90_000.0),   # 30s AHEAD of now
                          (600.0, 'short', 1.0)])
    d = LiqCascadeChaser(db_path=path).evaluate(_ctx(spot=_bps(OPEN_PX, 30)))
    assert d.reason == 'no_cascade'
    assert d.features['liq_total_usd'] == 0.0


def test_one_entry_per_window(tmp_path):
    """The shadow loop polls a window many times. Without the ledger this would
    re-enter on every poll and the position would be sized by poll frequency."""
    path = _make_db(tmp_path / 'once.db', rows=_tape('short'))
    strat = LiqCascadeChaser(db_path=path)
    first = strat.evaluate(_ctx(spot=_bps(OPEN_PX, 30)))
    second = strat.evaluate(_ctx(spot=_bps(OPEN_PX, 30)))
    assert first.action == 'ENTER'
    assert second.action == 'SKIP'
    assert second.reason == 'already_entered_this_window'
    assert second.features['entry_attempts_are_not_fills'] is True


def test_cascade_chaser_will_not_substitute_the_previous_windows_open(tmp_path):
    """No bar matching this window's timestamp is a skip, never the last bar."""
    path = _make_db(tmp_path / 'noopen.db', rows=_tape('short'))
    stale_bars = [Window(ts=WINDOW_TS - 300 * (i + 1), open=OPEN_PX,
                         close=OPEN_PX, direction='UP', source='price')
                  for i in range(16, 0, -1)]
    d = LiqCascadeChaser(db_path=path).evaluate(
        _ctx(spot=_bps(OPEN_PX, 30), windows=stale_bars))
    assert d.reason == 'no_window_open_bar'


# ---------------------------------------------------------------------------
# 5. nothing raises, and the reasons never collide
# ---------------------------------------------------------------------------

def test_neither_strategy_raises_on_a_bare_context(tmp_path):
    path = _make_db(tmp_path / 'bare.db', rows=_tape('short'))
    bare = MarketContext(window_ts=WINDOW_TS)
    for strat in (LiqCascadeChaser(db_path=path),
                  SmallLiqContinuation(db_path=path)):
        d = strat.evaluate(bare)
        assert d.action == 'SKIP'
        assert d.reason
        assert d.strategy == strat.strategy_name


def test_no_market_and_no_book_are_different_reasons(tmp_path):
    path = _make_db(tmp_path / 'nb.db', rows=_tape('short'))
    no_market = LiqCascadeChaser(db_path=path).evaluate(
        _ctx(spot=_bps(OPEN_PX, 30), market=False))
    no_book = LiqCascadeChaser(db_path=path).evaluate(
        _ctx(spot=_bps(OPEN_PX, 30), books={}))
    assert no_market.reason == 'no_market'
    assert no_book.reason == 'no_orderbook'


def test_every_reason_this_suite_produces_is_distinct(tmp_path):
    """The reasons collected across the suite must be a set, not a bag with
    two meanings sharing a name (convention 20)."""
    cases = [
        ('table_missing', str(tmp_path / 'gone.db')),
        ('feed_empty', _make_db(tmp_path / 'e.db', rows=())),
        ('too_short', _make_db(tmp_path / 's.db',
                               rows=[(5.0, 'short', 90_000.0)])),
        ('stale', _make_db(tmp_path / 'st.db',
                           rows=[(3600.0, 'short', 90_000.0),
                                 (7200.0, 'short', 90_000.0)])),
        ('quiet', _make_db(tmp_path / 'q.db',
                           rows=[(10.0, 'short', 100.0),
                                 (600.0, 'short', 100.0)])),
    ]
    seen = {}
    for label, path in cases:
        d = LiqCascadeChaser(db_path=path).evaluate(_ctx(spot=_bps(OPEN_PX, 30)))
        seen[label] = d.reason
    assert len(set(seen.values())) == len(seen), seen
    assert seen['quiet'] == 'no_cascade'
    assert set(seen.values()) - {'no_cascade'} == set(feed.NO_DATA_REASONS)


def test_a_one_sided_cascade_stays_strict_json(tmp_path):
    """A fully one-sided tape makes `dominance_ratio` infinite, which is the
    shape a real cascade has. `json.loads` would happily accept `Infinity` back
    (convention 19), so it is rendered as a string and the whole row is asserted
    to survive `allow_nan=False`."""
    path = _make_db(tmp_path / 'onesided.db', rows=_tape('short'))
    d = LiqCascadeChaser(db_path=path).evaluate(_ctx(spot=_bps(OPEN_PX, 30)))
    assert d.action == 'ENTER', d.reason
    assert d.features['liq_dominance_ratio'] == 'inf'
    json.dumps(d.to_dict(), allow_nan=False)


def test_the_read_is_genuinely_read_only(tmp_path):
    """`mode=ro` is a wiring fact, not a promise in a docstring (convention 22)."""
    path = _make_db(tmp_path / 'ro.db', rows=_tape('short'))
    conn = feed._connect_ro(path)
    try:
        raised = False
        try:
            conn.execute('DELETE FROM liquidations')
        except sqlite3.OperationalError as exc:
            raised = 'readonly' in str(exc).lower()
        assert raised
    finally:
        conn.close()
