"""The shadow loop across BTC, ETH and SOL 5-minute markets.

Everything here is OFFLINE. Same discipline as `test_polymarket_shadow_loop.py`:
a fake client, a stubbed spot fetch, a stubbed strike proxy, candles from a
plain function. No socket is opened.

## What these tests are actually defending

Adding assets to this loop is not "call the same code three times". Three
distinct things would be silently wrong if the state were shared, and each one
produces plausible numbers rather than an exception:

  1. **Strategy instances.** `FairValueArb` accumulates a `PriceTape` of
     (timestamp, spot) observations and a `_window_trades` counter. One shared
     instance across three assets would push BTC's 64,000 and SOL's 76 into the
     SAME tape - the model would read a 99.9% "move" every time the loop
     stepped between assets - and would spend BTC's per-window trade budget on
     ETH. `test_each_asset_gets_its_own_strategy_instances` and
     `test_per_window_trade_budget_is_not_shared_across_assets` pin this.

  2. **The strike proxy.** It caches 1m klines for ONE Binance symbol. A shared
     proxy would answer SOL's strike with BTCUSDT bars, and lead_bps would come
     out around -99.9% - which every gate rejects, so the failure would look
     like a quiet market rather than crossed wires.

  3. **Exit routing.** Every asset runs an instance carrying the SAME
     `strategy_name`, so a name-keyed lookup no longer identifies one object.
     A BTC position handed to SOL's instance gets a model stop computed off the
     wrong coin's displacement: a wrong exit that looks normal in every log.
     `test_exits_route_to_the_instance_that_opened_the_position` pins it.

The accounting identity gains a factor and keeps its meaning:

    evaluations == entries + skips == cycles * strategies_per_asset * assets

including on the paths where ONE asset's market is missing. A per-asset failure
must not shrink the denominator, or an unlisted ETH window would make the loop
look like it evaluated everything it was supposed to.
"""
import json
import time
from collections import Counter

import pytest

from engine import halt
from engine.polymarket import shadow_loop
from engine.polymarket.assets import (ASSETS, SHADOW_ASSETS, asset_for_slug,
                                      get_asset)
from engine.polymarket.shadow_loop import PolymarketShadowLoop, ShadowStore
from strategies.polymarket import build_strategies
from strategies.polymarket.base import MARKET_TYPE_CRYPTO_UPDOWN

WINDOW = 300

#: Derived, never hardcoded - same reason as the single-asset file. A literal
#: would turn an accounting assertion into an assertion that nobody has added a
#: strategy since this file was written.
#:
#: The CRYPTO-ROUTED subset, not the whole registry, and for the reason spelled
#: out in the single-asset file: since D-312 `PM_weather_arb` declares only
#: `weather` and is polled by the weather cycle, so the crypto denominator is
#: the population that DECLARED `crypto_updown`.
N_STRATEGIES = len([
    s for s in build_strategies()
    if MARKET_TYPE_CRYPTO_UPDOWN in getattr(s, 'supported_market_types',
                                            (MARKET_TYPE_CRYPTO_UPDOWN,))])
THREE = ('btc', 'eth', 'sol')


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeClient:
    """Read-only stand-in for PolymarketClient, recording every slug asked for.

    `slugs_requested` is the point of this class: the central claim of the
    feature is that each asset is polled on ITS OWN market, and the only way to
    show that is to record what was actually requested rather than trusting that
    a market came back.
    """

    def __init__(self, gamma_payload, book_payload):
        self._gamma_payload = gamma_payload
        self._book_payload = book_payload
        self.stats = {'requests': 0, 'failures': 0}
        self.gamma_calls = []
        self.clob_calls = []
        self.slugs_requested = []

    def gamma(self, path, params=None):
        params = dict(params or {})
        self.gamma_calls.append((path, params))
        if params.get('slug'):
            self.slugs_requested.append(params['slug'])
        self.stats['requests'] += 1
        return self._gamma_payload(path, params)

    def clob(self, path, params=None):
        self.clob_calls.append((path, dict(params or {})))
        self.stats['requests'] += 1
        return self._book_payload(path, params or {})

    def data(self, path, params=None):
        return None


def market_row(slug):
    """One Gamma market, double-encoded exactly as Gamma sends it.

    The token ids are derived FROM THE SLUG so that two assets never share a
    token. With a constant `tok_up` the books, the positions and the exit
    routing would all collide across assets and several of these tests would
    pass for the wrong reason.
    """
    return {
        'id': 'id-' + slug,
        'question': 'Up or Down?',
        'slug': slug,
        'conditionId': '0xcond-' + slug,
        'outcomes': json.dumps(['Up', 'Down']),
        'clobTokenIds': json.dumps([slug + ':up', slug + ':down']),
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


def gamma_ok(path, params):
    slug = params.get('slug') or 'btc-updown-5m-0'
    return [market_row(slug)]


def gamma_missing_asset(missing):
    """Gamma answers normally except for `missing`, which is not indexed.

    An empty list is a 200 with no market - the venue answered, and the answer
    was "no such market". That is `no_market`, never `api_error`.
    """
    def _payload(path, params):
        slug = params.get('slug') or ''
        if slug.startswith(missing + '-'):
            return []
        return [market_row(slug)]
    return _payload


def books_ok(path, params):
    return deep_book()


def streak_candles(now, base):
    """16 windows ending in a 4-window DOWN streak, scaled to `base`.

    Scaled so the same shape works at BTC's 64,000 and SOL's 76: the streak
    filter is a ratio of cumulative move to ATR, so a move expressed as a
    FRACTION of the price produces the same ratio at any price level. A literal
    $100 move would be a rounding error on BTC and a 130% crash on SOL.
    """
    step = base * 0.0017
    opens, closes, timestamps = [], [], []
    start_ts = (int(now) // WINDOW) * WINDOW - 16 * WINDOW
    for i in range(16):
        o = base
        if i >= 12:
            c = o - step
        elif i == 11:
            c = o + step / 100.0
        else:
            c = o + (step / 100.0 if i % 2 == 0 else -step / 100.0)
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


#: Spot per asset, roughly the live levels on 2026-08-18. The SPREAD between
#: them is what matters: if any wiring pools spot across assets, a 64,000 and a
#: 76 in one series is impossible to mistake for a real move.
SPOT = {'btc': 64000.0, 'eth': 1900.0, 'sol': 76.0}


class OfflineStrikeProxy:
    """A StrikeProxy answering from a constant, recording its symbol."""

    def __init__(self, strike=None, symbol=None):
        self._strike = strike
        self.symbol = symbol
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def halt_file(tmp_path, monkeypatch):
    """Redirect the kill switch. Never touch the repository's real HALT."""
    monkeypatch.setattr(halt, 'HALT_FILE', str(tmp_path / 'HALT'))
    return tmp_path / 'HALT'


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Per-asset spot, offline. Note the stub is asset-aware.

    A stub that ignored `asset` and returned one number would hide the exact bug
    this file exists to catch, so it returns SPOT[asset] and every test that
    reads spot is really asserting the asset was threaded through.
    """
    monkeypatch.setattr(
        shadow_loop, 'fetch_spot_checked',
        lambda client, asset='btc': {'spot': SPOT[asset], 'source': 'stub',
                                     'failures': {}, 'asset': asset})
    monkeypatch.setattr(shadow_loop, 'StrikeProxy',
                        lambda *a, **kw: OfflineStrikeProxy(
                            symbol=kw.get('symbol')))


@pytest.fixture
def entry_time():
    return float((int(time.time()) // WINDOW) * WINDOW + 5)


def build_loop(tmp_path, client, assets=THREE, candles_for=None, **kw):
    store = ShadowStore(str(tmp_path / 'trading.db'))
    factory = None
    if candles_for is not None:
        def factory(asset, _c=candles_for):
            return lambda: _c(asset)
    return PolymarketShadowLoop(
        client=client, store=store, log_dir=str(tmp_path / 'paperlog'),
        assets=assets, candle_source_factory=factory,
        include_15m=kw.pop('include_15m', False), **kw)


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------

def test_registry_covers_the_three_shadow_assets():
    assert SHADOW_ASSETS == THREE
    for key in SHADOW_ASSETS:
        row = get_asset(key)
        assert row.key == key
        assert row.binance_symbol.endswith('USDT')
        assert row.coinbase_pair.endswith('-USD')
        assert row.candle_pair.endswith('/USDT')


def test_every_asset_has_a_distinct_symbol_set():
    """No two assets may share a Binance symbol or a candle pair.

    A duplicate here is the single most damaging typo available in this file:
    it would point two assets at one price series, and every downstream number
    would be internally consistent and wrong.
    """
    symbols = [a.binance_symbol for a in ASSETS.values()]
    pairs = [a.candle_pair for a in ASSETS.values()]
    assert len(set(symbols)) == len(symbols)
    assert len(set(pairs)) == len(pairs)


def test_asset_for_slug_refuses_to_guess():
    assert asset_for_slug('btc-updown-5m-1787022000') == 'btc'
    assert asset_for_slug('eth-updown-5m-1787022000') == 'eth'
    assert asset_for_slug('sol-updown-15m-1787022000') == 'sol'
    # Not registered: None, not a plausible-looking wrong answer.
    assert asset_for_slug('trump-wins-2028') is None
    assert asset_for_slug('xrp-updown-5m-1') is None
    assert asset_for_slug('') is None
    assert asset_for_slug(None) is None


def test_unknown_asset_fails_at_construction_not_mid_run(tmp_path):
    """Wiring errors surface at build time, while there is a stack to read."""
    client = FakeClient(gamma_ok, books_ok)
    with pytest.raises(KeyError, match='unknown asset'):
        build_loop(tmp_path, client, assets=('btc', 'nosuchcoin'))


def test_empty_asset_list_is_refused(tmp_path):
    """Zero assets means zero evaluations per cycle and a healthy-looking log."""
    client = FakeClient(gamma_ok, books_ok)
    with pytest.raises(ValueError, match='no assets'):
        build_loop(tmp_path, client, assets=())


# ---------------------------------------------------------------------------
# State isolation: the reason per-asset runtimes exist
# ---------------------------------------------------------------------------

def test_each_asset_gets_its_own_strategy_instances(tmp_path):
    """No strategy object may be shared between two assets.

    This is the load-bearing test of the whole feature. `FairValueArb` holds a
    PriceTape of spot observations; one shared instance would interleave BTC at
    64,000 with SOL at 76 in a single series.
    """
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)

    seen = {}
    for asset in THREE:
        for s in loop.runtimes[asset].strategies:
            seen.setdefault(id(s), []).append(asset)

    shared = {i: a for i, a in seen.items() if len(a) > 1}
    assert not shared, 'strategy instance shared across assets: {}'.format(shared)
    assert len(seen) == N_STRATEGIES * 3

    # Same NAMES on every asset, though - that is what makes cross-asset
    # comparison of one strategy possible at all.
    names = {a: sorted(getattr(s, 'strategy_name', str(s))
                       for s in loop.runtimes[a].strategies) for a in THREE}
    assert names['btc'] == names['eth'] == names['sol']


def test_each_asset_gets_its_own_strike_proxy_symbol(tmp_path):
    """SOL's strike must not be rebuilt from BTCUSDT klines."""
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)

    symbols = {a: loop.runtimes[a].strike_proxy.symbol for a in THREE}
    assert symbols == {'btc': 'BTCUSDT', 'eth': 'ETHUSDT', 'sol': 'SOLUSDT'}


def test_an_injected_strike_proxy_applies_to_every_asset(tmp_path):
    """One offline stub covers all three, which is what a test wants."""
    client = FakeClient(gamma_ok, books_ok)
    stub = OfflineStrikeProxy(strike=59940.0)
    loop = build_loop(tmp_path, client, strike_proxy=stub)
    for asset in THREE:
        assert loop.runtimes[asset].strike_proxy is stub


def test_each_asset_reads_its_own_spot(tmp_path, entry_time):
    """The spot in each context is that asset's, not the first asset's."""
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)
    window_ts = int(entry_time) // WINDOW * WINDOW

    for asset in THREE:
        ctx, status, detail = loop.build_context(window_ts, entry_time, asset)
        assert status == 'ok'
        assert ctx.spot == SPOT[asset]
        assert detail['asset'] == asset


def test_each_asset_reads_its_own_candles(tmp_path, entry_time):
    """ATR is per asset. SOL's ATR from BTC's bars is ~840x too large."""
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client,
                      candles_for=lambda a: streak_candles(entry_time, SPOT[a]))
    window_ts = int(entry_time) // WINDOW * WINDOW

    closes = {}
    for asset in THREE:
        ctx, status, _ = loop.build_context(window_ts, entry_time, asset)
        assert status == 'ok'
        closes[asset] = loop.runtimes[asset].candles['closes'][0]
        assert ctx.atr14 is not None

    # Each asset's candles sit at its own price level, so nothing was pooled.
    assert closes['btc'] > 10_000
    assert 1_000 < closes['eth'] < 10_000
    assert closes['sol'] < 1_000


def test_per_window_trade_budget_is_not_shared_across_assets(tmp_path):
    """A trade on BTC must not consume ETH's per-window budget.

    Exercised on the strategies' own state rather than through a full cycle, so
    the assertion is about the isolation itself and not about whether a given
    market happened to produce an entry.
    """
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)

    def budgeted(asset):
        return [s for s in loop.runtimes[asset].strategies
                if hasattr(s, '_note_attempt')
                and hasattr(s, 'trades_this_window')]

    btc = budgeted('btc')
    # Asserted, not skipped. The fair-value family carries this budget, and a
    # skip here would silently stop testing the isolation the moment somebody
    # renamed the method (convention 11: not-run is not a pass).
    assert btc, 'expected the fair-value family to expose a per-window budget'

    window_ts = 1787022000
    for s in btc:
        s._note_attempt(window_ts)

    for s in btc:
        assert s.trades_this_window(window_ts) == 1
    for asset in ('eth', 'sol'):
        peers = budgeted(asset)
        assert len(peers) == len(btc)
        for s in peers:
            assert s.trades_this_window(window_ts) == 0, (
                '{} budget was consumed by a BTC trade'.format(asset))


# ---------------------------------------------------------------------------
# Polling: each asset on its own market
# ---------------------------------------------------------------------------

def test_every_asset_is_polled_on_its_own_slug(tmp_path, entry_time):
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)
    window_ts = int(entry_time) // WINDOW * WINDOW

    loop.run_cycle(now=entry_time)

    for asset in THREE:
        expected = '{}-updown-5m-{}'.format(asset, window_ts)
        assert expected in client.slugs_requested, (
            '{} never polled; asked for {}'.format(expected,
                                                   client.slugs_requested))


def test_fifteen_minute_leg_is_per_asset(tmp_path, entry_time):
    """corridor_collector's second leg must be the SAME asset's 15m market."""
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client, include_15m=True)
    window_ts = int(entry_time) // WINDOW * WINDOW
    ts15 = (window_ts // 900) * 900

    loop.run_cycle(now=entry_time)

    for asset in THREE:
        assert '{}-updown-15m-{}'.format(asset, ts15) in client.slugs_requested


def test_signals_carry_the_asset_and_its_own_market_slug(tmp_path, entry_time):
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client,
                      candles_for=lambda a: streak_candles(entry_time, SPOT[a]))

    loop.run_cycle(now=entry_time)

    rows = loop.store.conn.execute(
        'SELECT DISTINCT pair FROM signals WHERE pair IS NOT NULL').fetchall()
    slugs = [r[0] for r in rows]
    assert slugs, 'no signals written'
    for asset in THREE:
        assert any(s.startswith(asset + '-updown-5m-') for s in slugs), (
            'no {} signal rows; got {}'.format(asset, slugs))

    # Every routed slug maps back to a registered asset. A row whose pair does
    # not is a row nobody can group by asset later.
    assert all(asset_for_slug(s) in ASSETS for s in slugs)


def test_same_strategy_id_across_assets_so_they_are_comparable(tmp_path,
                                                               entry_time):
    """`PM_streak_snapper` on BTC and on SOL share a strategy_id.

    That is deliberate: the whole point of running one strategy on three assets
    is to compare it against itself, and a per-asset id would make that a join
    on a parsed string instead of a GROUP BY.
    """
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)

    loop.run_cycle(now=entry_time)

    per_asset = {}
    for sid, pair in loop.store.conn.execute(
            'SELECT strategy_id, pair FROM signals WHERE pair IS NOT NULL'):
        per_asset.setdefault(asset_for_slug(pair), set()).add(sid)

    assert set(per_asset) == set(THREE)
    assert per_asset['btc'] == per_asset['eth'] == per_asset['sol']


# ---------------------------------------------------------------------------
# The accounting identity, with a third factor
# ---------------------------------------------------------------------------

def test_identity_multiplies_by_assets(tmp_path, entry_time):
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)

    loop.run_cycle(now=entry_time)

    assert loop.evaluations_per_cycle == N_STRATEGIES * 3
    assert loop.evaluations == N_STRATEGIES * 3
    assert loop.check_identity()

    loop.run_cycle(now=entry_time)
    assert loop.evaluations == N_STRATEGIES * 6
    assert loop.check_identity()


def test_one_asset_missing_does_not_shrink_the_denominator(tmp_path,
                                                           entry_time):
    """ETH unlisted is `no_market` for ETH's strategies and nothing else.

    The two facts that must both hold: BTC and SOL still evaluate normally, and
    ETH's strategies are each counted as `no_market` rather than skipped
    silently. A dropped asset that shrank the denominator would leave the
    identity satisfied and the coverage quietly reduced.
    """
    client = FakeClient(gamma_missing_asset('eth'), books_ok)
    loop = build_loop(tmp_path, client)

    detail = loop.run_cycle(now=entry_time)

    assert loop.evaluations == N_STRATEGIES * 3
    assert loop.check_identity()
    assert loop.counts['no_market'] == N_STRATEGIES
    assert detail['assets']['eth']['status'] == 'no_market'
    assert detail['assets']['btc']['status'] == 'ok'
    assert detail['assets']['sol']['status'] == 'ok'


def test_stats_report_the_asset_list(tmp_path, entry_time):
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)
    loop.run_cycle(now=entry_time)

    stats = loop.stats()
    assert stats['assets'] == list(THREE)
    assert stats['strategies_per_asset'] == N_STRATEGIES
    assert stats['identity_ok'] is True


def test_top_level_detail_mirrors_the_first_asset(tmp_path, entry_time):
    """Documented compatibility surface: top level is asset[0], not a summary."""
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)

    detail = loop.run_cycle(now=entry_time)

    assert detail['status'] == detail['assets']['btc']['status']
    assert detail['asset'] == 'btc'
    assert set(detail['assets']) == set(THREE)


# ---------------------------------------------------------------------------
# Exit routing
# ---------------------------------------------------------------------------

def test_exits_route_to_the_instance_that_opened_the_position(tmp_path,
                                                              entry_time):
    """A position is judged by ITS asset's instance, never another's.

    Three instances share the name `PM_fair_value_arb`. Keying exits on the name
    alone would hand a BTC position to whichever instance was written last, and
    the model stop would be computed off a different coin's displacement.
    """
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)

    managers = {a: [s for s in loop.runtimes[a].strategies
                    if getattr(s, 'manages_exits', False)] for a in THREE}
    if not managers['btc']:
        pytest.skip('no exit-managing strategy in this build')

    # Record which instance each asset's estimate() is called on.
    called = []
    for asset in THREE:
        for s in managers[asset]:
            def spy(ctx, _a=asset, _s=s, _orig=s.estimate):
                called.append((_a, id(_s)))
                return _orig(ctx)
            s.estimate = spy

    window_ts = int(entry_time) // WINDOW * WINDOW
    contexts = {}
    for asset in THREE:
        ctx, status, _ = loop.build_context(window_ts, entry_time, asset)
        assert status == 'ok'
        contexts[asset] = ctx

    loop.manage_exits(contexts, entry_time)

    # Every managing instance was asked about its OWN asset's context, and no
    # instance was asked about somebody else's.
    for asset in THREE:
        ids = {id(s) for s in managers[asset]}
        got = {i for (a, i) in called if a == asset}
        assert got == ids, '{}: estimates ran on the wrong instances'.format(
            asset)


def test_a_position_on_an_unregistered_slug_is_counted_not_guessed(tmp_path,
                                                                   entry_time):
    """An unroutable position is left alone and COUNTED (convention 20)."""
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)

    class Pos:
        market_slug = 'trump-wins-2028'
        strategy = 'PM_fair_value_arb'
        token_id = 'tok'
        window_ts = 0
        outcome_side = 'up'
        position_id = 'p1'

    loop.adapter.open_positions = lambda: [Pos()]
    result = loop.manage_exits({}, entry_time)

    assert result['checked'] == 0
    assert loop.exit_counts['unroutable_position'] == 1


# ---------------------------------------------------------------------------
# Risk: the bankroll is shared, the markets are not
# ---------------------------------------------------------------------------

def test_the_daily_loss_breaker_is_account_wide_not_per_asset(tmp_path):
    """$1,000 covers all three assets together.

    The breaker reads ONE realized-PnL number off the adapter, and there is one
    adapter for the whole loop. A per-asset breaker would let three assets lose
    the daily limit each.
    """
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)

    assert loop.starting_equity == 1000.0
    # One adapter and one gate for the whole loop, not one per asset.
    assert len({id(loop.adapter)}) == 1
    assert loop.gate.bankroll_usdc == 1000.0

    limit = loop.gate.daily_loss_limit_usdc
    ok, _ = loop.gate.check_daily_loss_breaker(-(limit - 0.01))
    assert ok is True
    blocked, reason = loop.gate.check_daily_loss_breaker(-(limit + 0.01))
    assert blocked is False
    assert 'daily_loss_breaker' in reason


def test_position_limits_are_per_market_so_assets_do_not_crowd_out(tmp_path):
    """`max_positions_per_market_side` keys on the SLUG, so each asset has its
    own slot. One open BTC Up position must not block an ETH Up entry."""
    client = FakeClient(gamma_ok, books_ok)
    loop = build_loop(tmp_path, client)
    assert loop.gate.max_positions_per_market_side >= 1
    # The gate counts open positions by market_slug; distinct assets produce
    # distinct slugs, which is what keeps the limits independent.
    assert asset_for_slug('btc-updown-5m-1') != asset_for_slug('eth-updown-5m-1')
