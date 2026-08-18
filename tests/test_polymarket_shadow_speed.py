"""Speed work on the shadow loop: the spot cache, parallel fetches, timings.

These are in their own file rather than appended to
`test_polymarket_shadow_loop.py` for a reason that is not style: this working
directory is shared (convention 21) and that file is edited by other sessions.
A new file cannot lose somebody else's work to a merge.

Everything here is OFFLINE. Nothing opens a socket.

What these tests are actually defending
---------------------------------------
1. **A cache without an age stamp is a lie.** Every cached spot must carry its
   age, that age must reach the decision's features, and a FAILED read must
   never be cached - caching a `None` would turn one flaky request into a
   guaranteed outage for the whole TTL and the log would then describe a longer
   outage than happened (convention 11).
2. **Parallelism must not touch the taxonomy.** `ok` / `api_error` /
   `no_liquidity` mean the same three things they meant sequentially, and the
   ADDED fourth value `fetch_exception` exists precisely so a thread that raised
   can never be recorded as a venue outage (convention 20).
3. **Determinism.** Thread completion order must not reach the output. The
   assertions below force the SECOND token's fetch to finish first and still
   require the market's own order.
4. **A raising fetch must not kill the cycle**, and the accounting identity has
   to survive it.
5. **`timings` holds seconds and stays outside the identity.** It is a Counter
   living next to two other Counters that DO get summed, which is exactly the
   shape of a future accident.
"""
import json
import threading
import time

from collections import Counter

import pytest

from engine import halt
from engine.polymarket import shadow_loop
from engine.polymarket.shadow_loop import PolymarketShadowLoop, ShadowStore

WINDOW = 300


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeClient:
    """GET-only stand-in, same shape as the real client's read surface."""

    def __init__(self, gamma_payload, book_payload):
        self._gamma_payload = gamma_payload
        self._book_payload = book_payload
        self.stats = {'requests': 0, 'failures': 0}
        self.clob_calls = []
        self._lock = threading.Lock()

    def gamma(self, path, params=None):
        with self._lock:
            self.stats['requests'] += 1
        return self._gamma_payload(path, dict(params or {}))

    def clob(self, path, params=None):
        with self._lock:
            self.stats['requests'] += 1
            self.clob_calls.append(dict(params or {}))
        return self._book_payload(path, dict(params or {}))

    def data(self, path, params=None):
        return None


def market_row(slug):
    return {
        'id': '12345',
        'question': 'Bitcoin Up or Down?',
        'slug': slug,
        'conditionId': '0xcondition',
        'outcomes': json.dumps(['Up', 'Down']),
        'clobTokenIds': json.dumps(['tok_up', 'tok_down']),
        'active': True,
        'closed': False,
    }


def deep_book(ask=0.50, size=500):
    return {
        'bids': [{'price': str(round(ask - 0.02, 2)), 'size': str(size)}],
        'asks': [{'price': str(ask), 'size': str(size)}],
        'tick_size': 0.01,
        'min_order_size': 5,
    }


EMPTY_BOOK = {'bids': [], 'asks': [], 'tick_size': 0.01, 'min_order_size': 5}


def gamma_ok(path, params):
    return [market_row(params.get('slug') or 'btc-updown-5m-0')]


def books_ok(path, params):
    return deep_book()


class OfflineStrikeProxy:
    def __init__(self, strike=None):
        self._strike = strike
        self.health = Counter()

    def twap60(self, at_ts, now=None):
        return self._strike

    def strike_for(self, window_ts, now=None):
        return {'strike': self._strike, 'source': None, 'is_proxy': True,
                'noise_floor_bps': shadow_loop.STRIKE_PROXY_NOISE_FLOOR_BPS,
                'bar_age_sec': 0.0, 'window_ts': window_ts}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def halt_file(tmp_path, monkeypatch):
    """Never touch the repository's real kill switch from a test."""
    monkeypatch.setattr(halt, 'HALT_FILE', str(tmp_path / 'HALT'))


@pytest.fixture(autouse=True)
def offline_strike(monkeypatch):
    monkeypatch.setattr(shadow_loop, 'StrikeProxy',
                        lambda *a, **kw: OfflineStrikeProxy())


class SpotCounter:
    """A `fetch_spot_checked` replacement that counts calls, per asset.

    The point of the spot cache is that this counter does NOT move on a hit. A
    test that only inspected the returned value could not tell a cache hit from
    a second identical fetch.
    """

    def __init__(self, spot=60000.0):
        self.spot = spot
        self.calls = Counter()

    def __call__(self, client, asset='btc'):
        self.calls[asset] += 1
        return {'spot': self.spot, 'source': 'stub', 'failures': {},
                'asset': asset}


@pytest.fixture
def spot(monkeypatch):
    counter = SpotCounter()
    monkeypatch.setattr(shadow_loop, 'fetch_spot_checked', counter)
    return counter


@pytest.fixture
def now_ts():
    """Five seconds into a 5-minute window."""
    return float((int(time.time()) // WINDOW) * WINDOW + 5)


def build_loop(tmp_path, client=None, **kw):
    """One asset unless the caller says otherwise, for the same reason the
    sibling file pins it: the FakeClient answers every slug identically, so
    three assets would silently triple every count under test."""
    kw.setdefault('assets', ('btc',))
    kw.setdefault('include_15m', False)
    return PolymarketShadowLoop(
        client=client if client is not None else FakeClient(gamma_ok, books_ok),
        store=ShadowStore(str(tmp_path / 'trading.db')),
        log_dir=str(tmp_path / 'paperlog'),
        candle_source=None,
        **kw)


# ---------------------------------------------------------------------------
# The spot cache
# ---------------------------------------------------------------------------

def test_two_reads_inside_the_ttl_share_one_http_fetch(tmp_path, spot):
    loop = build_loop(tmp_path, spot_cache_ttl_sec=2.0)

    first = loop.spot_checked('btc', now_mono=100.0)
    second = loop.spot_checked('btc', now_mono=101.0)

    assert spot.calls['btc'] == 1, 'the second read went to the network'
    assert first['spot'] == second['spot'] == 60000.0
    assert loop.health['spot_cache_hit:btc'] == 1
    assert loop.health['spot_cache_miss:btc'] == 1


def test_a_cached_spot_carries_its_age_and_says_it_is_cached(tmp_path, spot):
    """A cache without an age stamp is a lie: a stale-context decision has to
    stay identifiable after the fact, exactly as `candles_age_sec` makes it."""
    loop = build_loop(tmp_path, spot_cache_ttl_sec=5.0)

    fresh = loop.spot_checked('btc', now_mono=100.0)
    assert fresh['cached'] is False
    assert fresh['age_sec'] == 0.0

    cached = loop.spot_checked('btc', now_mono=103.5)
    assert cached['cached'] is True
    assert cached['age_sec'] == pytest.approx(3.5)


def test_the_cache_expires_and_refetches(tmp_path, spot):
    loop = build_loop(tmp_path, spot_cache_ttl_sec=2.0)

    loop.spot_checked('btc', now_mono=100.0)
    loop.spot_checked('btc', now_mono=101.9)      # inside
    assert spot.calls['btc'] == 1

    loop.spot_checked('btc', now_mono=102.1)      # outside
    assert spot.calls['btc'] == 2


def test_the_cache_is_keyed_per_asset(tmp_path, spot):
    """BTC's spot handed to ETH's strike does not fail loudly - it produces a
    lead near -97% that every gate rejects, so the wiring error would present
    as a quiet market rather than as a bug."""
    loop = build_loop(tmp_path, assets=('btc', 'eth'), spot_cache_ttl_sec=5.0)

    btc = loop.spot_checked('btc', now_mono=100.0)
    eth = loop.spot_checked('eth', now_mono=100.0)

    assert spot.calls['btc'] == 1
    assert spot.calls['eth'] == 1, 'eth was served from btc\'s cache entry'
    assert btc['asset'] == 'btc'
    assert eth['asset'] == 'eth'


def test_a_failed_spot_read_is_never_cached(tmp_path, monkeypatch):
    """Caching `spot=None` would turn one flaky request into a guaranteed
    outage for the whole TTL, and the log would describe a longer outage than
    actually happened (convention 11)."""
    calls = Counter()

    def flaky(client, asset='btc'):
        calls[asset] += 1
        if calls[asset] == 1:
            return {'spot': None, 'source': None, 'asset': asset,
                    'failures': {'stub': 'timeout'}}
        return {'spot': 60000.0, 'source': 'stub', 'failures': {},
                'asset': asset}

    monkeypatch.setattr(shadow_loop, 'fetch_spot_checked', flaky)
    loop = build_loop(tmp_path, spot_cache_ttl_sec=60.0)

    assert loop.spot_checked('btc', now_mono=100.0)['spot'] is None
    # Immediately inside the TTL, and it must still go back to the network.
    assert loop.spot_checked('btc', now_mono=100.1)['spot'] == 60000.0
    assert calls['btc'] == 2


def test_a_zero_ttl_disables_the_cache_entirely(tmp_path, spot):
    """The switch the timing harness uses as its control. Without it, a spot
    cache hit is invisible to `client.stats` (the spot read bypasses
    `client.get`) and a measurement credits parallelism with fewer reads."""
    loop = build_loop(tmp_path, spot_cache_ttl_sec=0.0)

    loop.spot_checked('btc', now_mono=100.0)
    loop.spot_checked('btc', now_mono=100.0)

    assert spot.calls['btc'] == 2
    assert loop.health['spot_cache_hit:btc'] == 0


def test_a_negative_age_is_treated_as_a_miss(tmp_path, spot):
    """An injected clock can precede the stamp. Without the `age >= 0` guard a
    negative age passes `< ttl` forever and the entry never expires."""
    loop = build_loop(tmp_path, spot_cache_ttl_sec=5.0)

    loop.spot_checked('btc', now_mono=100.0)
    loop.spot_checked('btc', now_mono=90.0)

    assert spot.calls['btc'] == 2


def test_spot_age_rides_into_every_decision_feature(tmp_path, spot, now_ts):
    """Same contract as `candles_age_sec`: the age must be ON the decision row,
    not merely computable from it."""
    loop = build_loop(tmp_path, spot_cache_ttl_sec=2.0)
    loop.run_cycle(now=now_ts)

    rows = loop.store.conn.execute(
        'SELECT features_json FROM signals').fetchall()
    assert rows, 'the cycle recorded no decisions'
    for row in rows:
        feats = json.loads(row['features_json'])
        assert 'spot_age_sec' in feats
        assert 'spot_cached' in feats


# ---------------------------------------------------------------------------
# Parallel fetches: the taxonomy must survive them
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('payload,expected', [
    (books_ok, 'ok'),
    (lambda p, q: EMPTY_BOOK, 'no_liquidity'),
    (lambda p, q: None, 'api_error'),
])
def test_the_three_status_values_are_preserved_exactly(tmp_path, spot,
                                                       now_ts, payload,
                                                       expected):
    """`ok` / `api_error` / `no_liquidity` mean what they meant sequentially.

    "We could not reach the venue" and "nobody is quoting" must never merge
    into one number (convention 20), and parallelising the reads is exactly the
    kind of change that quietly merges them.
    """
    loop = build_loop(tmp_path, FakeClient(gamma_ok, payload))
    _ctx, _status, detail = loop.build_context(int(now_ts // WINDOW * WINDOW),
                                               now_ts, 'btc')
    assert set(detail['book_status'].values()) == {expected}


def test_parallel_and_sequential_agree_exactly(tmp_path, spot, now_ts):
    """The control that makes the speedup believable (convention 17). If the
    two paths disagree about anything, the fast one is not the same work."""
    window_ts = int(now_ts // WINDOW * WINDOW)

    seq = build_loop(tmp_path / 'seq', FakeClient(gamma_ok, books_ok),
                     parallel_fetches=False)
    par = build_loop(tmp_path / 'par', FakeClient(gamma_ok, books_ok),
                     parallel_fetches=True)

    ctx_s, status_s, detail_s = seq.build_context(window_ts, now_ts, 'btc')
    ctx_p, status_p, detail_p = par.build_context(window_ts, now_ts, 'btc')

    assert status_s == status_p == 'ok'
    assert detail_s['book_status'] == detail_p['book_status']
    assert sorted(ctx_s.books) == sorted(ctx_p.books)
    assert seq.client.stats['requests'] == par.client.stats['requests']


def test_ordering_does_not_depend_on_which_thread_finishes_first(tmp_path,
                                                                 spot, now_ts):
    """Forces the FIRST token to finish LAST and still requires market order.

    Without this the status dict's key order would be completion order, and a
    diff of two cycles' logs would be noise rather than signal.
    """
    def slow_first(path, params):
        if params.get('token_id') == 'tok_up':
            time.sleep(0.15)          # Up finishes well after Down
        return deep_book()

    loop = build_loop(tmp_path, FakeClient(gamma_ok, slow_first))
    _ctx, status, detail = loop.build_context(int(now_ts // WINDOW * WINDOW),
                                              now_ts, 'btc')

    assert status == 'ok'
    assert list(detail['book_status']) == ['Up', 'Down']


def test_the_reads_genuinely_overlap(tmp_path, spot, now_ts):
    """Proves concurrency with a BARRIER, not with a stopwatch.

    Both book fetches wait on a `Barrier(2)`. If the two reads are serialised
    the first one waits alone, the barrier times out, `BrokenBarrierError`
    propagates, and `_run_parallel` records `fetch_exception` - so a status of
    `ok` on both sides is a proof that two threads were inside the fetch at the
    same instant.

    Written this way on purpose. The first version of this test asserted a wall
    clock bound and failed on this machine while three other processes were
    pinned at 100% CPU - a raw `ThreadPoolExecutor` running three 0.15s sleeps
    took 0.28s under that load. A timing threshold tight enough to prove
    overlap is tight enough to report the machine's load as a code regression.
    """
    barrier = threading.Barrier(2, timeout=10.0)

    def rendezvous(path, params):
        barrier.wait()
        return deep_book()

    loop = build_loop(tmp_path, FakeClient(gamma_ok, rendezvous))
    _ctx, status, detail = loop.build_context(int(now_ts // WINDOW * WINDOW),
                                              now_ts, 'btc')

    assert status == 'ok'
    assert set(detail['book_status'].values()) == {'ok'}


def test_the_overlap_test_has_teeth(tmp_path, spot, now_ts):
    """The control for the test above. The SAME barrier, executor OFF.

    A rendezvous that cannot be reached sequentially must actually fail
    sequentially, or the test above proves nothing. This also pins the
    behaviour that a fetch which raises - here, a broken barrier - is caught,
    categorised, and does not escape.
    """
    barrier = threading.Barrier(2, timeout=0.5)

    def rendezvous(path, params):
        barrier.wait()
        return deep_book()

    loop = build_loop(tmp_path, FakeClient(gamma_ok, rendezvous),
                      parallel_fetches=False)
    ctx, status, detail = loop.build_context(int(now_ts // WINDOW * WINDOW),
                                             now_ts, 'btc')

    assert ctx is None
    assert status == shadow_loop.SKIP_CYCLE_EXCEPTION
    assert set(detail['book_status'].values()) == {
        shadow_loop.STATUS_FETCH_EXCEPTION}


def test_parallelism_is_switchable_off_without_a_code_edit(tmp_path, spot):
    """So a suspected concurrency bug can be bisected by flipping a flag mid
    incident rather than by reverting a patch."""
    assert build_loop(tmp_path / 'a').parallel_fetches is True
    assert build_loop(tmp_path / 'b',
                      parallel_fetches=False).parallel_fetches is False


# ---------------------------------------------------------------------------
# A thread that raises
# ---------------------------------------------------------------------------

def test_a_raising_fetch_is_its_own_status_never_api_error(tmp_path, spot,
                                                           now_ts):
    """Our code throwing and the venue being unreachable need OPPOSITE
    responses. Backing off is right for an outage and useless for a bug, so
    they must not share a bucket (convention 20)."""
    loop = build_loop(tmp_path)

    def boom(token_id):
        if token_id == 'tok_down':
            raise RuntimeError('synthetic fetch fault')
        return loop.__class__._fetch_book_checked(loop, token_id)

    loop._fetch_book_checked = boom
    _ctx, status, detail = loop.build_context(int(now_ts // WINDOW * WINDOW),
                                              now_ts, 'btc')

    assert status == 'ok', 'one good book is still a usable context'
    assert detail['book_status']['Up'] == 'ok'
    assert detail['book_status']['Down'] == shadow_loop.STATUS_FETCH_EXCEPTION
    assert detail['book_status']['Down'] != shadow_loop.SKIP_API_ERROR
    assert detail['book_status']['Down'] != shadow_loop.SKIP_NO_LIQUIDITY
    assert loop.health['book_fetch_exception'] == 1


def test_every_book_raising_is_a_cycle_exception_not_an_outage(tmp_path, spot,
                                                               now_ts):
    """With no usable book at all, the reported reason still has to name the
    right cause. `api_error` would tell the loop to back off from a venue that
    is answering perfectly well."""
    loop = build_loop(tmp_path)
    loop._fetch_book_checked = lambda token_id: (_ for _ in ()).throw(
        RuntimeError('synthetic fetch fault'))

    ctx, status, _detail = loop.build_context(int(now_ts // WINDOW * WINDOW),
                                              now_ts, 'btc')

    assert ctx is None
    assert status == shadow_loop.SKIP_CYCLE_EXCEPTION


def test_a_raising_fetch_does_not_kill_the_cycle_or_the_identity(tmp_path,
                                                                 spot, now_ts):
    """The identity is the whole accounting contract. A fetch fault is allowed
    to lose a book; it is not allowed to lose an evaluation."""
    loop = build_loop(tmp_path)
    loop._fetch_book_checked = lambda token_id: (_ for _ in ()).throw(
        RuntimeError('synthetic fetch fault'))

    loop.run_cycle(now=now_ts)

    assert loop.evaluations == len(loop.strategies)
    assert loop.check_identity() is True


def test_a_raising_spot_read_is_counted_and_does_not_escape(tmp_path,
                                                            monkeypatch,
                                                            now_ts):
    """`fetch_spot_checked` returns a dict on every failure it anticipates, so
    a raise means OUR code broke and must be named as such."""
    def boom(client, asset='btc'):
        raise RuntimeError('synthetic spot fault')

    monkeypatch.setattr(shadow_loop, 'fetch_spot_checked', boom)
    loop = build_loop(tmp_path)

    ctx, status, detail = loop.build_context(int(now_ts // WINDOW * WINDOW),
                                             now_ts, 'btc')

    assert status == 'ok', 'a missing spot is a strategy gate, not a dead cycle'
    assert ctx.spot is None
    assert loop.health['spot_fetch_exception:btc'] == 1
    assert 'fetch_exception' in detail['spot_failures']


def test_a_raising_15m_lookup_is_named_not_swallowed(tmp_path, spot, now_ts):
    loop = build_loop(tmp_path, include_15m=True)
    monkey = shadow_loop.get_market_by_slug_checked

    def gamma_or_boom(client, slug, *a, **kw):
        if '15m' in slug:
            raise RuntimeError('synthetic 15m fault')
        return monkey(client, slug, *a, **kw)

    shadow_loop.get_market_by_slug_checked = gamma_or_boom
    try:
        ctx, status, detail = loop.build_context(
            int(now_ts // WINDOW * WINDOW), now_ts, 'btc')
    finally:
        shadow_loop.get_market_by_slug_checked = monkey

    assert status == 'ok'
    assert ctx.market_15m is None
    assert detail['market_15m_status'] == shadow_loop.STATUS_FETCH_EXCEPTION
    assert loop.health['market_15m_exception:btc'] == 1


# ---------------------------------------------------------------------------
# timings: seconds, and outside the identity
# ---------------------------------------------------------------------------

def test_timings_are_populated_but_stay_out_of_the_identity(tmp_path, spot,
                                                            now_ts):
    """`timings` is a Counter sitting next to two Counters that DO get summed.
    That is the shape of a future accident, so the separation is asserted."""
    loop = build_loop(tmp_path)
    loop.run_cycle(now=now_ts)

    assert loop.timings['cycle_total'] > 0
    assert loop.timings['cycle_contexts'] > 0
    assert loop.timings['cycle_total_calls'] == 1

    # The identity is untouched by any of it.
    assert loop.check_identity() is True
    assert loop.evaluations == len(loop.strategies)
    # And no timing key leaked into either counter space.
    assert not any(k.startswith('cycle_') for k in loop.counts)
    assert not any(k.startswith('cycle_contexts') for k in loop.health)


def test_timing_report_splits_total_from_calls_from_average(tmp_path, spot,
                                                            now_ts):
    """A step that is slow and a step that merely ran a lot produce the same
    total and need opposite fixes. A bare total cannot tell them apart."""
    loop = build_loop(tmp_path)
    loop.run_cycle(now=now_ts)
    loop.run_cycle(now=now_ts + 1)

    report = loop.timing_report()
    assert report['cycle_total']['calls'] == 2
    assert report['cycle_total']['total_sec'] > 0
    assert report['cycle_total']['avg_sec'] == pytest.approx(
        report['cycle_total']['total_sec'] / 2, rel=0.05)
    # `_calls` companions are consumed, not surfaced as their own rows.
    assert not any(k.endswith('_calls') for k in report)


def test_timings_survive_a_step_that_raised(tmp_path, spot, now_ts):
    """A step that blew up still took time. Dropping its sample would make an
    exploding step look like a fast one."""
    loop = build_loop(tmp_path)

    with pytest.raises(RuntimeError):
        loop._timed('synthetic', lambda: (_ for _ in ()).throw(
            RuntimeError('boom')))

    assert loop.timings['synthetic_calls'] == 1
    assert loop.timings['synthetic'] >= 0


def test_stats_carries_timings_without_disturbing_the_identity(tmp_path, spot,
                                                               now_ts):
    loop = build_loop(tmp_path)
    loop.run_cycle(now=now_ts)

    stats = loop.stats()
    assert stats['identity_ok'] is True
    assert 'timings' in stats
    assert 'cycle_total' in stats['timings']
    # It must be JSON-serialisable with allow_nan=False, because `stats()` is
    # written into `audit_log` on every flush (convention 19).
    json.dumps(stats['timings'], allow_nan=False)


def test_run_parallel_returns_caller_order_and_never_raises(tmp_path, spot):
    """The primitive itself, directly. Both properties in one assertion set."""
    loop = build_loop(tmp_path)

    def boom():
        raise ValueError('nope')

    tasks = [('c', lambda: 3), ('a', boom), ('b', lambda: 2)]
    out = loop._run_parallel(tasks)

    assert list(out) == ['c', 'a', 'b']
    assert out['c'] == (3, None)
    assert out['b'] == (2, None)
    assert out['a'][0] is None
    assert isinstance(out['a'][1], ValueError)


def test_run_parallel_is_identical_with_the_executor_off(tmp_path, spot):
    loop = build_loop(tmp_path, parallel_fetches=False)

    tasks = [('c', lambda: 3), ('a', lambda: 1), ('b', lambda: 2)]
    out = loop._run_parallel(tasks)

    assert list(out) == ['c', 'a', 'b']
    assert [v for v, _ in out.values()] == [3, 1, 2]
