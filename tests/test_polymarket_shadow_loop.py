"""The Polymarket shadow loop: wiring, accounting, and persistence.

Everything here is OFFLINE. The Polymarket client is a fake, the BTC spot fetch
is monkeypatched, and the candle source is a plain function returning a list.
No test in this file opens a socket: a test suite that needs the venue up is a
test suite that reports the venue's weather as a code regression, and it would
also mean the assertions below only hold when a 5-minute market happens to be
open.

`engine.halt.HALT_FILE` is redirected to a tmp path for EVERY test by an autouse
fixture. Touching the real kill switch from a test suite is how you end up with
a HALT file left behind by a crashed run, which then silently blocks a real
session.

The load-bearing assertions:

  * $1,000.00 starting equity, and it lands in `equity_snapshots`.
  * HALT blocks an entry that would otherwise fill, and the halted window is
    COUNTED as `halted` rather than vanishing.
  * An empty book is `no_liquidity`; a failed read is `api_error`. Never merged.
  * An API outage backs off and the loop survives it, then recovers.
  * The accounting identity `evaluations == entries + skips` and
    `evaluations == cycles * n_strategies` holds on every path tested.
  * An entry writes signals + orders + fills + positions, and a resolution
    closes the position row.
"""
import json
import sqlite3
import time

from collections import Counter

import pytest

from engine import halt
from engine.polymarket import shadow_loop
from engine.polymarket.shadow_loop import PolymarketShadowLoop, ShadowStore
from strategies.polymarket import build_strategies
from strategies.polymarket.base import MARKET_TYPE_CRYPTO_UPDOWN

WINDOW = 300

#: Derived, never hardcoded. The identity under test is
#: `evaluations == cycles * len(strategies)`, and a literal here would turn a
#: real accounting assertion into an assertion that nobody has added a strategy
#: since the file was written (this is exactly what happened at 4 -> 7).
#:
#: The CRYPTO-ROUTED subset, not the whole registry. Since D-312 a strategy
#: joins the crypto cycle by DECLARING `crypto_updown` in
#: `supported_market_types`, and `PM_weather_arb` declares only `weather`, so
#: it is polled by the weather cycle and never by this one. Using the registry
#: total here asserted the denominator of a cycle that does not run every
#: member of it.
N_STRATEGIES = len([
    s for s in build_strategies()
    if MARKET_TYPE_CRYPTO_UPDOWN in getattr(s, 'supported_market_types',
                                            (MARKET_TYPE_CRYPTO_UPDOWN,))])


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeClient:
    """Read-only stand-in for PolymarketClient. GET verbs only, like the real one.

    `gamma_payload` and `book_payload` are callables so a test can flip the
    venue's behaviour mid-run (outage, then recovery) without rebuilding the
    loop.
    """

    def __init__(self, gamma_payload, book_payload):
        self._gamma_payload = gamma_payload
        self._book_payload = book_payload
        self.stats = {'requests': 0, 'failures': 0}
        self.gamma_calls = []
        self.clob_calls = []

    def gamma(self, path, params=None):
        self.gamma_calls.append((path, dict(params or {})))
        self.stats['requests'] += 1
        return self._gamma_payload(path, params or {})

    def clob(self, path, params=None):
        self.clob_calls.append((path, dict(params or {})))
        self.stats['requests'] += 1
        return self._book_payload(path, params or {})

    def data(self, path, params=None):
        return None


def market_row(slug, up_price=None, down_price=None):
    """One Gamma market object, double-encoded exactly as Gamma sends it."""
    row = {
        'id': '12345',
        'question': 'Bitcoin Up or Down?',
        'slug': slug,
        'conditionId': '0xcondition',
        'outcomes': json.dumps(['Up', 'Down']),
        'clobTokenIds': json.dumps(['tok_up', 'tok_down']),
        'active': True,
        'closed': False,
    }
    if up_price is not None:
        row['outcomePrices'] = json.dumps([str(up_price), str(down_price)])
    return row


def deep_book(ask=0.50, size=500):
    return {
        'bids': [{'price': str(round(ask - 0.02, 2)), 'size': str(size)}],
        'asks': [{'price': str(ask), 'size': str(size)}],
        'tick_size': 0.01,
        'min_order_size': 5,
    }


EMPTY_BOOK = {'bids': [], 'asks': [], 'tick_size': 0.01, 'min_order_size': 5}


def gamma_ok(resolved=False):
    """Gamma always answers with a usable 5m market."""
    def _payload(path, params):
        slug = params.get('slug') or 'btc-updown-5m-0'
        if resolved:
            return [market_row(slug, up_price=1, down_price=0)]
        return [market_row(slug)]
    return _payload


def gamma_empty(path, params):
    """A 200 carrying an empty list. The market is not indexed: not an outage."""
    return []


def gamma_down(path, params):
    """A failed read. `PolymarketClient.get` returns None for these."""
    return None


def books_ok(path, params):
    return deep_book()


def books_empty(path, params):
    return EMPTY_BOOK


def books_down(path, params):
    return None


# -- candles ----------------------------------------------------------------

def streak_candles(now):
    """16 completed 5m windows ending in a 4-window DOWN streak, stretched.

    Built so streak_snapper's gates pass on real arithmetic rather than on a
    mocked strategy: 12 quiet alternating windows then 4 x -$100. That gives
    ATR(12) = (8*1 + 400)/12 = 34.0 and |cum(4)| = 400 > 3.0 * 34.0, which is
    exactly the stretch filter the strategy lives on.
    """
    base = 60000.0
    opens, closes, timestamps = [], [], []
    start_ts = (int(now) // WINDOW) * WINDOW - 16 * WINDOW
    for i in range(16):
        o = base
        if i >= 12:
            c = o - 100.0            # the streak: four DOWN windows
        elif i == 11:
            c = o + 1.0              # the window that terminates the streak
        else:
            c = o + (1.0 if i % 2 == 0 else -1.0)
        opens.append(o)
        closes.append(c)
        timestamps.append((start_ts + i * WINDOW) * 1000)
    return {
        'opens': opens,
        'highs': [max(o, c) for o, c in zip(opens, closes)],
        'lows': [min(o, c) for o, c in zip(opens, closes)],
        'closes': closes,
        'volumes': [1.0] * 16,
        'timestamps': timestamps,
    }


def quiet_candles(now):
    """16 alternating windows: no streak, so streak_snapper skips `no_streak`."""
    base = 60000.0
    opens, closes, timestamps = [], [], []
    start_ts = (int(now) // WINDOW) * WINDOW - 16 * WINDOW
    for i in range(16):
        opens.append(base)
        closes.append(base + (1.0 if i % 2 == 0 else -1.0))
        timestamps.append((start_ts + i * WINDOW) * 1000)
    return {
        'opens': opens,
        'highs': [o + 1 for o in opens],
        'lows': [o - 1 for o in opens],
        'closes': closes,
        'volumes': [1.0] * 16,
        'timestamps': timestamps,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def halt_file(tmp_path, monkeypatch):
    """Redirect the kill switch. Never touch the repository's real HALT."""
    path = tmp_path / 'HALT'
    monkeypatch.setattr(halt, 'HALT_FILE', str(path))
    return path


class OfflineStrikeProxy:
    """A StrikeProxy that answers from a constant instead of Binance.

    Defaults to `strike=None`, which is the pre-proxy behaviour, so a test that
    says nothing about the strike keeps testing exactly what it did before.
    Tests that care about the strike inject one with a value and assert on it.
    """

    def __init__(self, strike=None):
        self._strike = strike
        self.health = Counter()

    def twap60(self, at_ts, now=None):
        return self._strike

    def strike_for(self, window_ts, now=None):
        return {
            'strike': self._strike,
            'source': None if self._strike is None else 'stub_proxy',
            'is_proxy': True,
            'noise_floor_bps': shadow_loop.STRIKE_PROXY_NOISE_FLOOR_BPS,
            'bar_age_sec': 0.0,
            'window_ts': window_ts,
        }


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Spot comes from a public exchange over HTTP. Not in a test it doesn't.

    The strike proxy pulls 1m klines over HTTP too, so it is stubbed for the
    same reason. Left un-stubbed, every test in this file would depend on what
    BTC did in the last sixty seconds.
    """
    monkeypatch.setattr(shadow_loop, 'fetch_spot_checked',
                        lambda client, asset='btc': {
                            'spot': 60000.0, 'source': 'stub',
                            'failures': {}, 'asset': asset})
    monkeypatch.setattr(shadow_loop, 'fetch_btc_spot_checked',
                        lambda client: {'spot': 60000.0, 'source': 'stub',
                                        'failures': {}, 'asset': 'btc'})
    monkeypatch.setattr(shadow_loop, 'StrikeProxy',
                        lambda *a, **kw: OfflineStrikeProxy())


@pytest.fixture
def entry_time():
    """A `now` five seconds into a 5-minute window.

    streak_snapper's ENTRY_WINDOW_SEC is 20, so a decision at t+5 is inside the
    entry window and a decision at t+120 is not. Pinning it makes the test
    independent of the wall clock's position within the current window.
    """
    return float((int(time.time()) // WINDOW) * WINDOW + 5)


def build_loop(tmp_path, client, candles=None, **kw):
    """A loop pinned to ONE asset unless the caller says otherwise.

    `assets=('btc',)` is the default here on purpose. The loop's own default is
    all three (btc, eth, sol), which is the right production behaviour, but the
    FakeClient answers every slug with the same market - so three assets would
    turn every exact count in this file into three times itself and the
    assertions would stop describing what they were written to describe.

    Multi-asset behaviour has its own tests at the bottom of this file. Keeping
    the two apart means a change to the multi-asset wiring cannot quietly
    weaken the single-asset accounting assertions.
    """
    store = ShadowStore(str(tmp_path / 'trading.db'))
    kw.setdefault('assets', ('btc',))
    loop = PolymarketShadowLoop(
        client=client, store=store,
        log_dir=str(tmp_path / 'paperlog'),
        candle_source=(lambda: candles) if candles is not None else None,
        include_15m=kw.pop('include_15m', False),
        **kw)
    return loop


def rows(loop, sql, params=()):
    return loop.store.conn.execute(sql, params).fetchall()


# ---------------------------------------------------------------------------
# Paper mode
# ---------------------------------------------------------------------------

def test_paper_mode_is_unconditional(tmp_path, monkeypatch):
    """A falsy PAPER_MODE is a hard failure, not a quietly live loop."""
    client = FakeClient(gamma_ok(), books_ok)
    monkeypatch.setattr(shadow_loop, 'PAPER_MODE', False)
    with pytest.raises(RuntimeError, match='PAPER_MODE'):
        build_loop(tmp_path, client)


def test_mode_constant_is_paper():
    assert shadow_loop.MODE == 'paper'
    assert shadow_loop.PAPER_MODE is True


# ---------------------------------------------------------------------------
# Equity
# ---------------------------------------------------------------------------

def test_equity_starts_at_1000_and_snapshots_persist(tmp_path, entry_time):
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=quiet_candles(entry_time))

    assert loop.starting_equity == 1000.0
    assert loop.adapter.starting_equity == 1000.0
    # The gate's bankroll must not disagree with the adapter's bankroll.
    assert loop.gate.bankroll_usdc == 1000.0

    loop.run(max_cycles=2, sleeper=lambda _s: None)

    snaps = rows(loop, 'SELECT ts, equity, cash, open_risk, mode '
                       'FROM equity_snapshots ORDER BY ts')
    assert snaps, 'no equity snapshot was written'
    assert snaps[0]['equity'] == 1000.0
    assert snaps[0]['mode'] == 'paper'
    assert snaps[0]['open_risk'] == 0.0
    # ms, not seconds. Every ts column in db/schema.sql is milliseconds and the
    # dashboard parses them with unit='ms'.
    assert snaps[0]['ts'] > 1_000_000_000_000


def test_starting_equity_override_reaches_adapter_and_gate(tmp_path):
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, starting_equity=250.0)
    assert loop.adapter.starting_equity == 250.0
    assert loop.gate.bankroll_usdc == 250.0
    assert loop.adapter.get_equity() == 250.0


# ---------------------------------------------------------------------------
# The entry path
# ---------------------------------------------------------------------------

def test_entry_writes_signal_order_fill_and_position(tmp_path, entry_time):
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    loop.run_cycle(now=entry_time)

    assert loop.counts['entry'] == 1, dict(loop.counts)

    positions = rows(loop, 'SELECT * FROM positions')
    assert len(positions) == 1
    pos = positions[0]
    assert pos['strategy_id'] == 'PM_streak_snapper'
    assert pos['mode'] == 'paper'
    assert pos['qty'] == 19          # floor($10 / 0.52), his flat stake
    assert pos['entry_px'] == pytest.approx(0.50)
    # A losing binary share is worth exactly 0.00 and that IS the stop.
    assert pos['stop_px'] == 0.0
    assert pos['target_px'] == 1.0
    assert pos['closed_ts'] is None

    orders = rows(loop, 'SELECT * FROM orders')
    assert len(orders) == 1
    assert orders[0]['side'] == 'buy'
    assert orders[0]['status'] == 'filled'
    assert orders[0]['limit_price'] == pytest.approx(0.52)
    assert orders[0]['cl_ord_id'] == 'PM-{}'.format(pos['id'])

    fills = rows(loop, 'SELECT * FROM fills WHERE order_id = ?', (orders[0]['id'],))
    assert len(fills) == 1
    assert fills[0]['qty'] == 19
    assert fills[0]['price'] == pytest.approx(0.50)

    acted = rows(loop, 'SELECT * FROM signals WHERE acted = 1')
    assert len(acted) == 1
    assert acted[0]['strategy_id'] == 'PM_streak_snapper'
    assert acted[0]['skip_reason'] is None
    feats = json.loads(acted[0]['features_json'])
    assert feats['outcome_side'] == 'Up'      # fade a DOWN streak
    assert feats['legs_filled'] == 1

    audit = rows(loop, "SELECT * FROM audit_log WHERE event_type = 'position_opened'")
    assert len(audit) == 1


def test_every_evaluation_writes_a_signals_row(tmp_path, entry_time):
    """Convention 20: a window with no row is a window nobody can audit."""
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=quiet_candles(entry_time))

    loop.run_cycle(now=entry_time)

    signal_rows = rows(loop, 'SELECT strategy_id, skip_reason FROM signals')
    assert len(signal_rows) == N_STRATEGIES
    # The crypto-routed population, for the same D-312 reason N_STRATEGIES is.
    # `PM_weather_arb` writes its rows from the weather cycle, not this one.
    assert ({r['strategy_id'] for r in signal_rows}
            == {s.strategy_name for s in build_strategies()
                if MARKET_TYPE_CRYPTO_UPDOWN
                in getattr(s, 'supported_market_types',
                           (MARKET_TYPE_CRYPTO_UPDOWN,))})
    assert all(r['skip_reason'] for r in signal_rows)


# ---------------------------------------------------------------------------
# The kill switch
# ---------------------------------------------------------------------------

def test_halt_blocks_an_entry_that_would_otherwise_fill(tmp_path, entry_time,
                                                        halt_file):
    """The control: the same inputs enter when not halted (test above)."""
    halt_file.write_text(json.dumps({'halt_id': 'abc', 'reason': 'drill'}))

    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))
    loop.run_cycle(now=entry_time)

    assert loop.counts['entry'] == 0
    assert loop.counts['halted'] == 1, dict(loop.counts)
    assert rows(loop, 'SELECT * FROM positions') == []

    halted_signal = rows(loop, "SELECT * FROM signals WHERE skip_reason = 'halted'")
    assert len(halted_signal) == 1
    feats = json.loads(halted_signal[0]['features_json'])
    assert 'ENTRIES only' in feats['halt_note']


def test_unreadable_halt_still_blocks(tmp_path, entry_time, halt_file):
    """`is_halted()` is fail-safe: presence is the whole test."""
    halt_file.write_text('this is not json')

    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))
    loop.run_cycle(now=entry_time)

    assert loop.counts['halted'] == 1
    assert rows(loop, 'SELECT * FROM positions') == []


def test_halt_blocks_every_entry_window_not_just_the_first(tmp_path,
                                                          entry_time,
                                                          halt_file):
    """Three entry-shaped windows, three blocks. The halt does not wear off."""
    halt_file.write_text('{}')
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    for _ in range(3):
        loop.run_cycle(now=entry_time)

    assert loop.counts['halted'] == 3
    assert loop.counts['entry'] == 0
    assert rows(loop, 'SELECT * FROM positions') == []
    assert loop.check_identity()


def test_halt_does_not_stop_the_loop(tmp_path, entry_time, halt_file):
    """A halt blocks entries and keeps polling. It never exits the process.

    This one runs on the WALL CLOCK deliberately, so it also covers the case
    where the poll lands outside streak_snapper's 20-second entry window. The
    assertion is therefore about the loop surviving and staying accountable,
    not about which gate fired: `test_halt_blocks_every_entry_window...` above
    pins the halt count on a fixed timestamp.
    """
    halt_file.write_text('{}')
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    loop.run(max_cycles=3, sleeper=lambda _s: None)

    assert loop.cycles == 3
    assert loop.counts['entry'] == 0
    assert rows(loop, 'SELECT * FROM positions') == []
    assert loop.check_identity()


def test_halt_transition_is_audited_once(tmp_path, entry_time, halt_file):
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=quiet_candles(entry_time))
    loop._halt_state = False
    halt_file.write_text('{}')

    loop._note_halt_transition()
    loop._note_halt_transition()      # level unchanged: no second row

    audit = rows(loop, "SELECT * FROM audit_log WHERE event_type = 'halt'")
    assert len(audit) == 1
    payload = json.loads(audit[0]['payload_json'])
    assert 'cannot flatten' in payload['note']

    risk = rows(loop, "SELECT * FROM risk_events WHERE type = 'kill_switch'")
    assert len(risk) == 1


# ---------------------------------------------------------------------------
# Failure taxonomy: every skip is counted AND categorised, separately
# ---------------------------------------------------------------------------

def test_empty_book_is_no_liquidity(tmp_path, entry_time):
    client = FakeClient(gamma_ok(), books_empty)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    detail = loop.run_cycle(now=entry_time)

    assert detail['status'] == 'no_liquidity'
    assert loop.counts['no_liquidity'] == N_STRATEGIES
    assert loop.counts['api_error'] == 0
    assert loop.counts['entry'] == 0
    assert loop.check_identity()


def test_missing_market_is_no_market_not_api_error(tmp_path, entry_time):
    client = FakeClient(gamma_empty, books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    detail = loop.run_cycle(now=entry_time)

    assert detail['status'] == 'no_market'
    assert loop.counts['no_market'] == N_STRATEGIES
    assert loop.counts['api_error'] == 0


def test_failed_read_is_api_error_not_no_market(tmp_path, entry_time):
    """The pair of assertions that matter: these two must never share a bucket.

    "Gamma says this market does not exist" means wait for the next window.
    "We could not reach Gamma" means back off. Merging them makes an outage
    read as a quiet market (convention 11).
    """
    client = FakeClient(gamma_down, books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    detail = loop.run_cycle(now=entry_time)

    assert detail['status'] == 'api_error'
    assert loop.counts['api_error'] == N_STRATEGIES
    assert loop.counts['no_market'] == 0
    assert detail['api_error_attempt'] == 1


def test_book_read_failure_is_api_error_not_no_liquidity(tmp_path, entry_time):
    client = FakeClient(gamma_ok(), books_down)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    detail = loop.run_cycle(now=entry_time)

    assert detail['status'] == 'api_error'
    assert loop.counts['no_liquidity'] == 0


def test_api_error_backs_off_and_the_loop_survives(tmp_path):
    """Bounded exponential backoff, and no exception escapes the loop."""
    slept = []
    client = FakeClient(gamma_down, books_down)
    loop = build_loop(tmp_path, client)

    loop.run(max_cycles=4, sleeper=slept.append)

    assert loop.cycles == 4
    assert loop._consecutive_api_errors == 4
    # 5 -> 10 -> 20 -> 40, all under the 60s cap.
    assert slept == [10.0, 20.0, 40.0, 60.0]
    assert max(slept) <= shadow_loop.MAX_BACKOFF_SEC
    assert loop.check_identity()

    reason_keys = [k for k in loop.counts if k == 'api_error']
    assert reason_keys == ['api_error']
    csv_reasons = rows(loop, 'SELECT DISTINCT skip_reason FROM signals')
    # The attempt number rides in the reason string so a retry storm is visible.
    assert any('attempt_' in r['skip_reason'] for r in csv_reasons)


def test_backoff_is_capped(tmp_path):
    client = FakeClient(gamma_down, books_down)
    loop = build_loop(tmp_path, client)
    loop._consecutive_api_errors = 50
    assert loop.backoff_sec() == shadow_loop.MAX_BACKOFF_SEC


def test_api_error_recovers(tmp_path, entry_time):
    """After the venue comes back the counter resets to the normal poll."""
    state = {'down': True}

    def flaky_gamma(path, params):
        if state['down']:
            return None
        return gamma_ok()(path, params)

    client = FakeClient(flaky_gamma, books_ok)
    loop = build_loop(tmp_path, client, candles=quiet_candles(entry_time))

    loop.run_cycle(now=entry_time)
    assert loop._consecutive_api_errors == 1
    assert loop.backoff_sec() == 10.0

    state['down'] = False
    loop.run_cycle(now=entry_time)
    assert loop._consecutive_api_errors == 0
    assert loop.backoff_sec() == loop.poll_sec
    assert loop.counts['api_error'] == N_STRATEGIES
    assert loop.check_identity()


def test_a_raising_strategy_does_not_take_the_others_with_it(tmp_path,
                                                             entry_time):
    class Exploding:
        strategy_name = 'PM_exploding'
        paper_mode = True

        def evaluate(self, ctx):
            raise RuntimeError('boom')

    client = FakeClient(gamma_ok(), books_ok)
    store = ShadowStore(str(tmp_path / 'trading.db'))
    from strategies.polymarket import StreakSnapper
    loop = PolymarketShadowLoop(
        client=client, store=store, log_dir=str(tmp_path / 'paperlog'),
        candle_source=lambda: streak_candles(entry_time), include_15m=False,
        assets=('btc',), strategies=[Exploding(), StreakSnapper()])

    loop.run_cycle(now=entry_time)

    assert loop.counts['cycle_exception'] == 1
    assert loop.counts['entry'] == 1
    assert loop.evaluations == 2
    assert loop.check_identity()


def test_risk_gate_rejection_reason_is_verbatim(tmp_path, entry_time):
    class BlockingGate:
        def check_adapter_order(self, adapter, slug, side, **kw):
            class V:
                approved = False
                reason = 'max_total_exposure: 100.00 open (limit: 100.00)'
                shares = 0
            return V()

    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time),
                      risk_gate=BlockingGate())

    loop.run_cycle(now=entry_time)

    key = 'risk_gate:max_total_exposure: 100.00 open (limit: 100.00)'
    assert loop.counts[key] == 1, dict(loop.counts)
    assert loop.counts['entry'] == 0
    assert rows(loop, 'SELECT * FROM positions') == []


def test_maker_quote_never_becomes_an_entry(tmp_path, entry_time):
    """box_builder rests bids. A resting fill cannot be simulated as a taker
    lift without manufacturing the very fills its edge depends on.

    Updated 2026-08-18 when the maker path was wired: the quote is now RESTED
    rather than dropped, so the disposition moved from
    `maker_quote_not_simulable` to `maker_quote_rested`. The claim this test
    exists to defend is unchanged and is the second assert: resting is not
    entering, and the decision cycle must not open a position either way.
    """
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    loop.run_cycle(now=entry_time)

    box = rows(loop, "SELECT * FROM signals WHERE strategy_id = 'PM_box_builder'")
    assert len(box) == 1
    assert box[0]['acted'] == 0
    # Either it rested a quote, its own gate stopped it first, or a maker-path
    # gate refused the rest. None of those is an entry.
    assert box[0]['skip_reason']
    assert loop.counts.get(shadow_loop.SKIP_MAKER_RESTED, 0) + sum(
        v for k, v in loop.counts.items()
        if k.startswith('strategy:') or k.startswith('maker_')
        or k.startswith(shadow_loop.MAKER_ADAPTER_PREFIX)) >= 1
    # And no box_builder POSITION exists after the decision cycle. Other
    # strategies may have entered in this same cycle; this one may not, because
    # a rest is not a fill and its fill can only be decided by a later book.
    assert [p for p in loop.adapter.open_positions()
            if p.strategy == 'PM_box_builder'] == []


def test_strategies_blocked_by_missing_strike_are_named_not_silent(tmp_path,
                                                                   entry_time):
    """With NO strike available at all, the block must stay visible.

    A strategy that cannot run is NOT_TESTED, never tested-and-found-nothing
    (convention 11), so it must appear as its own named reason and not as a
    silent zero. This is still the behaviour when the proxy cannot produce a
    reading - klines unavailable, or the bar missing.
    """
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time),
                      strike_proxy=OfflineStrikeProxy(strike=None))

    ctx, status, _detail = loop.build_context(int(entry_time) // WINDOW * WINDOW,
                                              entry_time)
    assert status == 'ok'
    assert ctx.strike is None
    assert ctx.lead_bps is None
    assert ctx.atr14 is not None      # the ATR half of the gate IS supplied

    loop.run_cycle(now=entry_time)
    # EVERY strike-gated strategy, naming the SAME root cause. Before the
    # proxy, corridor_collector reported `no_lead_or_atr` instead, which read
    # as a second independent problem and was really this one downstream: the
    # ATR was always supplied, it was the lead that was missing, and the lead
    # needs a strike. Two symptom names for one cause is how a single fix looks
    # like two (convention 20 in reverse).
    #
    # The expected count is DERIVED from the population rather than hardcoded.
    # It was literally 2 when this was written; PM_grid_hedge arriving with
    # `needs_strike = True` made it 3, and the hardcoded 2 then failed for a
    # reason that had nothing to do with what this test checks. Deriving it is
    # not a loosening: `== len(strike_gated) * len(loop.assets)` still asserts
    # that every one of them names the reason and that NONE of them is silent,
    # which is the whole point (convention 11).
    strike_gated = [s.strategy_name for s in loop.strategies
                    if getattr(s, 'needs_strike', False)]
    assert len(strike_gated) >= 2, strike_gated
    expected = len(strike_gated) * len(loop.assets)
    assert loop.counts['strategy:no_spot_or_strike'] == expected, (
        strike_gated, dict(loop.counts))
    assert loop.counts['strategy:no_lead_or_atr'] == 0


def test_a_lead_inside_the_proxy_noise_floor_is_its_own_reason(tmp_path,
                                                               entry_time):
    """Measurement error must never be logged as a market condition.

    The proxy strike disagreed with the oracle 42% of the time below 1 bp. A
    strategy declining on a lead that small has NOT been tested on that window,
    and pooling it with a real skip reason would record noise as a result
    (conventions 11 and 20).
    """
    client = FakeClient(gamma_ok(), books_ok)
    # spot is stubbed at 60000.0; a strike of 60000.0 is a lead of 0 bps.
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time),
                      strike_proxy=OfflineStrikeProxy(strike=60000.0))

    ctx, status, _detail = loop.build_context(int(entry_time) // WINDOW * WINDOW,
                                              entry_time)
    assert status == 'ok'
    assert ctx.strike == 60000.0
    assert ctx.lead_bps == pytest.approx(0.0)

    loop.run_cycle(now=entry_time)
    assert loop.counts['strategy:' + shadow_loop.SKIP_PROXY_NOISE] >= 1
    # and NOT as the missing-data reason, which would mean something else
    assert loop.counts['strategy:no_spot_or_strike'] == 0


def test_the_gated_row_carries_where_the_floor_came_from_and_what_it_costs(
        tmp_path, entry_time):
    """A gated row must be self-describing about the floor that gated it.

    The floor is ONE constant applied to btc, eth and sol. Its per-asset error
    spans ~3x (5.1% / 9.3% / 15.8%), so a row that records only
    `noise_floor_bps: 5.0` understates what it cost on SOL by a factor of three.
    Convention 22: the docstring in `strike.py` saying so is not a wiring test,
    this is. The old stamp was `noise_floor_measured_on`, which read as "this
    row was measured on BTC" - it was not; the FLOOR was.
    """
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time),
                      strike_proxy=OfflineStrikeProxy(strike=60000.0))
    loop.run_cycle(now=entry_time)

    gated = rows(loop, 'SELECT features_json FROM signals WHERE skip_reason = ?',
                 (shadow_loop.SKIP_PROXY_NOISE,))
    assert gated, 'no row was gated on proxy noise'

    for row in gated:
        feats = json.loads(row['features_json'])
        # The floor on the row is the ACTIVE, PER-ASSET one, not the module
        # default. These are different numbers since the shadow-mode
        # loosening, and the row has to carry the one that actually gated it -
        # a row stamped with a floor it was not judged against is worse than
        # an unstamped row, because it looks checked.
        assert feats['noise_floor_bps'] == shadow_loop.noise_floor_bps_for(
            feats['asset'])
        assert feats['noise_floor_default_bps'] == (
            shadow_loop.STRIKE_PROXY_NOISE_FLOOR_BPS)
        # The 5.0-bps measurement must NOT be reported as the error at a floor
        # that is no longer 5.0. None here means UNMEASURED at the active
        # floor, and that is the honest answer until it is re-measured.
        if feats['noise_floor_bps'] == feats[
                'noise_floor_error_measured_at_bps']:
            assert feats['strike_proxy_error_at_active_floor_pct'] == feats[
                'strike_proxy_error_at_floor_pct']
        else:
            assert feats['strike_proxy_error_at_active_floor_pct'] is None
        assert feats['noise_floor_source'] == 'btc'
        assert feats['noise_floor_measured_error_by_asset'] == {
            'btc': 0.051, 'eth': 0.093, 'sol': 0.158}
        assert feats['strike_is_proxy'] is True
        # The rename must be complete, not additive. Two names for one field
        # is how a downstream query silently reads zero rows.
        assert 'noise_floor_measured_on' not in feats


def test_a_lead_outside_the_noise_floor_reaches_the_strategy(tmp_path,
                                                             entry_time):
    """The whole point of the proxy: the strategy gets to decide for itself.

    Outside the measured floor the strategy must run and report its OWN reason.
    Whether it enters is a market condition and is not asserted here - only
    that it is no longer dying at the data gate.
    """
    client = FakeClient(gamma_ok(), books_ok)
    # spot 60000 against a strike of 59940 is +10.01 bps: (60/59940)*10_000.
    # Comfortably outside the 5 bp floor, and inside the >= 10 bps bucket the
    # harness measured at 0% disagreement over 32 windows.
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time),
                      strike_proxy=OfflineStrikeProxy(strike=59940.0))

    ctx, _status, _detail = loop.build_context(
        int(entry_time) // WINDOW * WINDOW, entry_time)
    assert ctx.lead_bps == pytest.approx(10.01, rel=1e-3)
    assert not shadow_loop.is_inside_noise_floor(ctx.lead_bps)

    loop.run_cycle(now=entry_time)
    assert loop.counts['strategy:no_spot_or_strike'] == 0
    assert loop.counts['strategy:' + shadow_loop.SKIP_PROXY_NOISE] == 0


def test_the_proxy_strike_is_never_silently_spot(tmp_path, entry_time):
    """The refusal the previous session made still holds.

    Spot is stubbed at 60000.0 here. If the strike ever equals it by default,
    something has started substituting spot for the Chainlink TWAP, which is
    the exact failure this whole module exists to avoid.
    """
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))
    ctx, _status, detail = loop.build_context(
        int(entry_time) // WINDOW * WINDOW, entry_time)
    assert ctx.strike is None
    assert ctx.strike != ctx.spot
    # and when a strike IS supplied it is labelled a proxy, permanently
    loop2 = build_loop(tmp_path, client, candles=streak_candles(entry_time),
                       strike_proxy=OfflineStrikeProxy(strike=59940.0))
    _ctx2, _s2, detail2 = loop2.build_context(
        int(entry_time) // WINDOW * WINDOW, entry_time)
    assert detail2['strike_is_proxy'] is True


# ---------------------------------------------------------------------------
# Accounting identity
# ---------------------------------------------------------------------------

def test_identity_holds_across_mixed_conditions(tmp_path, entry_time):
    """Cycle through outage, empty book, quiet market and an entry."""
    scenarios = [
        (gamma_down, books_down),
        (gamma_ok(), books_empty),
        (gamma_ok(), books_ok),
        (gamma_empty, books_ok),
    ]
    state = {'i': 0}

    client = FakeClient(
        lambda p, q: scenarios[state['i']][0](p, q),
        lambda p, q: scenarios[state['i']][1](p, q))
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    for i in range(len(scenarios)):
        state['i'] = i
        loop.run_cycle(now=entry_time)

    assert loop.check_identity()
    assert loop.evaluations == loop.cycles * N_STRATEGIES
    assert loop.evaluations == loop._entries() + loop._skips()
    # And the DB agrees with the counters, which is the point of counting.
    total_signals = rows(loop, 'SELECT COUNT(*) AS n FROM signals')[0]['n']
    assert total_signals == loop.evaluations


def test_identity_violation_is_loud_not_silent(tmp_path, entry_time, caplog):
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=quiet_candles(entry_time))
    loop.run_cycle(now=entry_time)

    # Corrupt the books by hand, the way a missing `_count` call would.
    loop.evaluations += 1

    with caplog.at_level('ERROR'):
        assert loop.check_identity() is False
    assert 'ACCOUNTING IDENTITY VIOLATED' in caplog.text
    assert loop.identity_violations == 1

    violations = rows(loop,
                      "SELECT * FROM audit_log WHERE event_type = 'accounting_violation'")
    assert len(violations) == 1


def test_stats_reports_the_identity(tmp_path, entry_time):
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=quiet_candles(entry_time))
    loop.run_cycle(now=entry_time)

    stats = loop.stats()
    assert stats['identity_ok'] is True
    assert stats['mode'] == 'paper'
    assert stats['evaluations'] == N_STRATEGIES
    assert stats['equity_usdc'] == 1000.0
    assert sum(stats['counts'].values()) == stats['evaluations']


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

def test_resolution_settles_the_position_row(tmp_path, entry_time):
    resolved = {'on': False}

    def flip_gamma(path, params):
        return gamma_ok(resolved=resolved['on'])(path, params)

    client = FakeClient(flip_gamma, books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    loop.run_cycle(now=entry_time)
    assert loop.counts['entry'] == 1
    position_id = rows(loop, 'SELECT id FROM positions')[0]['id']

    resolved['on'] = True             # the oracle speaks: Up = 1.00
    settled = loop.resolve()
    assert len(settled) == 1
    assert settled[0].resolution == 'WIN'

    row = rows(loop, 'SELECT * FROM positions WHERE id = ?', (position_id,))[0]
    assert row['closed_ts'] is not None
    assert row['exit_px'] == 1.0
    assert row['exit_reason'] == 'target'
    # 19 shares bought at 0.50 redeem at 1.00: +$9.50 on $9.50 risked, R = 1.0.
    assert row['pnl_net'] == pytest.approx(9.5)
    assert row['r_multiple'] == pytest.approx(1.0)

    closed = rows(loop, "SELECT * FROM audit_log WHERE event_type = 'position_closed'")
    assert len(closed) == 1


def test_resolution_runs_during_a_halt(tmp_path, entry_time, halt_file):
    """A halt blocks entries. It does not un-decide a resolved window.

    Skipping resolution while halted would leave an operator reading a halted
    session's PnL with the losses missing.
    """
    resolved = {'on': False}
    client = FakeClient(lambda p, q: gamma_ok(resolved=resolved['on'])(p, q),
                        books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    loop.run_cycle(now=entry_time)
    assert loop.counts['entry'] == 1

    halt_file.write_text('{}')
    resolved['on'] = True
    settled = loop.resolve()

    assert len(settled) == 1
    assert rows(loop, 'SELECT closed_ts FROM positions')[0]['closed_ts'] is not None


def test_equity_reflects_realized_pnl_after_resolution(tmp_path, entry_time):
    resolved = {'on': False}
    client = FakeClient(lambda p, q: gamma_ok(resolved=resolved['on'])(p, q),
                        books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    loop.run_cycle(now=entry_time)
    # Open positions are held at ZERO by the adapter, so equity DIPS by the
    # premium at risk while the window is live. Documented, not a bug.
    assert loop.adapter.get_equity() == pytest.approx(1000.0 - 9.5)

    resolved['on'] = True
    loop.resolve()
    assert loop.adapter.get_equity() == pytest.approx(1009.5)

    snap = loop.snapshot_equity()
    assert snap['equity'] == pytest.approx(1009.5)
    assert snap['open_risk'] == 0.0


# ---------------------------------------------------------------------------
# Persistence plumbing
# ---------------------------------------------------------------------------

def test_store_uses_wal_so_the_dashboard_never_blocks(tmp_path):
    """R-010: dashboard/ opens this same file mode=ro."""
    store = ShadowStore(str(tmp_path / 'trading.db'))
    mode = store.conn.execute('PRAGMA journal_mode').fetchone()[0]
    assert mode.lower() == 'wal'

    ro = sqlite3.connect('file:{}?mode=ro'.format(tmp_path / 'trading.db'),
                         uri=True)
    assert ro.execute('SELECT COUNT(*) FROM signals').fetchone()[0] == 0
    ro.close()
    store.close()


def test_ensure_schema_is_idempotent(tmp_path):
    path = str(tmp_path / 'trading.db')
    store = ShadowStore(path)
    store.record_equity(1000.0, 1000.0, 0.0, ts_ms=1)
    store.ensure_schema()
    store.ensure_schema()
    assert store.conn.execute(
        'SELECT COUNT(*) FROM equity_snapshots').fetchone()[0] == 1
    store.close()


def test_non_finite_features_are_named_not_written_as_nan(tmp_path):
    """Convention 19: `NaN` is not portable JSON. Fail loudly, keep the row."""
    store = ShadowStore(str(tmp_path / 'trading.db'))
    payload = store._json({'ok': 1.0, 'bad': float('inf'), 'worse': float('nan')})
    assert 'Infinity' not in payload and 'NaN' not in payload
    decoded = json.loads(payload)
    assert sorted(decoded['_non_finite_keys']) == ['bad', 'worse']
    assert decoded['ok'] == 1.0
    store.close()


def test_shutdown_flushes_a_final_snapshot(tmp_path, entry_time):
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=quiet_candles(entry_time))

    stats = loop.run(max_cycles=1, sleeper=lambda _s: None)

    assert stats['cycles'] == 1
    stop = rows(loop, "SELECT * FROM audit_log WHERE event_type = 'shadow_stop'")
    assert len(stop) == 1
    start = rows(loop, "SELECT * FROM audit_log WHERE event_type = 'shadow_start'")
    assert len(start) == 1
    assert json.loads(start[0]['payload_json'])['paper_mode'] is True
    assert rows(loop, 'SELECT COUNT(*) AS n FROM equity_snapshots')[0]['n'] >= 1


def test_request_stop_ends_the_loop(tmp_path, entry_time):
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=quiet_candles(entry_time))

    def stop_after_first(_seconds):
        loop.request_stop()

    loop.run(max_cycles=100, sleeper=stop_after_first)
    assert loop.cycles == 1


def test_csv_decision_log_is_written(tmp_path, entry_time):
    import csv
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))
    loop.run_cycle(now=entry_time)

    with open(loop.adapter.log_path) as f:
        log_rows = list(csv.DictReader(f))
    assert log_rows
    actions = [r['action'] for r in log_rows]
    assert 'ENTER' in actions
    # One CSV row per evaluation: three skips plus the entry. A refused entry is
    # never logged twice (the adapter writes its own row and we do not add a
    # second).
    assert len(log_rows) == N_STRATEGIES


# ---------------------------------------------------------------------------
# Exit-management CAPABILITY DISPATCH
# ---------------------------------------------------------------------------

class FairValueLessManager:
    """A manager that decides its own exits and publishes NO fair value.

    A REAL strategy shape, not a mock of a broken one: an exit rule keyed on
    price, time or its own tape needs no model estimate. `PM_dip_arb` was
    exactly this until 2026-08-18 and may be again depending on which of the two
    competing rationales is retired (see `DipArb.estimate` and the
    `exit_no_fair_value_protocol` block in `shadow_loop.__init__`).

    Declared here rather than reached for in `strategies.polymarket` on purpose:
    the loop's dispatch is a property of the LOOP, and a test that stops
    exercising it the moment a strategy gains an `estimate()` is not testing the
    loop at all.
    """

    strategy_name = 'PM_test_no_fair_value'
    manages_exits = True
    needs_strike = False

    def evaluate(self, ctx):
        from strategies.polymarket.base import Decision
        return Decision(action='SKIP', reason='test_stub',
                        strategy=self.strategy_name,
                        window_ts=ctx.window_ts,
                        market_slug=getattr(ctx.market, 'slug', None),
                        legs=[], features={})

    def manage_exit(self, position, book, now=None, fair_value=None):
        raise AssertionError('no position should exist in this test')


def test_a_manager_without_a_fair_value_is_not_an_exception(tmp_path,
                                                            entry_time):
    """`manages_exits` does not imply `estimate()`, and the gap is not an error.

    The loop used to call `estimate()` on every exit manager and swallow the
    AttributeError into `health['exit_fair_value_exceptions']` - every cycle, on
    every asset. Exits were never affected (`manage_exit` is called afterwards
    with `fair_value=None` either way), but the COUNTER was: at three assets on
    a 5s poll that is roughly 51,000 spurious increments a day, which buries any
    genuine fair-value exception completely.

    Convention 20: "has no estimate()" and "estimate() raised" must not share a
    number. The first is a wiring fact, gauged once; the second stays a
    per-occurrence counter.
    """
    from strategies.polymarket import StreakSnapper

    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time),
                      strategies=[StreakSnapper(), FairValueLessManager()])

    loop.run_cycle(now=entry_time)
    loop.run_cycle(now=entry_time + 5)

    assert loop.health['exit_fair_value_exceptions'] == 0, dict(loop.health)
    assert ({name for _asset, name in loop.exit_no_fair_value_protocol}
            == {'PM_test_no_fair_value'})
    # A GAUGE over a set, recorded once at setup: two cycles must not double it.
    assert loop.health['exit_no_fair_value_protocol'] == len(loop.assets)


def test_the_real_strategy_set_logs_no_fair_value_exceptions(tmp_path,
                                                             entry_time):
    """The same claim against whatever `build_strategies()` actually returns.

    The test above pins the loop's dispatch with a shape it controls. This one
    is the live check: with the REAL strategy list, two clean cycles must move
    `exit_fair_value_exceptions` not at all. It holds whether a manager has no
    `estimate()` (dispatch skips it) or has a never-usable one (the try/except
    passes), which is the point - it cannot be satisfied by the counter being
    wrong in a new way.
    """
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    managers = [s for s in loop.strategies
                if getattr(s, 'manages_exits', False)]
    assert managers, 'no exit managers at all; this test would be vacuous'

    loop.run_cycle(now=entry_time)
    loop.run_cycle(now=entry_time + 5)

    assert loop.health['exit_fair_value_exceptions'] == 0, dict(loop.health)
    # The two populations partition the managers, per asset. Neither number is
    # asserted to a constant - the split is what matters, not where it sits.
    without = {s.strategy_name for s in managers if not hasattr(s, 'estimate')}
    assert ({name for _asset, name in loop.exit_no_fair_value_protocol}
            == without)


def test_a_real_estimate_failure_is_still_caught_and_counted(tmp_path,
                                                             entry_time):
    """The fix must not be "we stopped counting".

    Capability dispatch removes the calls that could only ever raise
    AttributeError. It must leave the try/except intact for the strategies that
    DO publish a fair value, because a genuine failure inside `estimate()` is a
    real signal - and the whole reason the counter was worth cleaning up.
    """
    client = FakeClient(gamma_ok(), books_ok)
    loop = build_loop(tmp_path, client, candles=streak_candles(entry_time))

    broken = [s for s in loop.strategies
              if getattr(s, 'manages_exits', False) and hasattr(s, 'estimate')]
    assert broken

    def _raise(_ctx):
        raise RuntimeError('deliberate')

    broken[0].estimate = _raise
    loop.run_cycle(now=entry_time)

    assert loop.health['exit_fair_value_exceptions'] == 1, dict(loop.health)
    # And it did NOT leak into the wiring gauge.
    assert (broken[0].strategy_name
            not in {name for _asset, name in loop.exit_no_fair_value_protocol})
