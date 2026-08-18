"""Tests for the measured proxy strike.

The point of these is not that the arithmetic works. It is that the module
cannot quietly degrade into the thing the previous session refused to build:
spot substituted for a Chainlink TWAP with no record that it happened.
"""
import json
import math
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.strike import (NOISE_FLOOR_ERROR_BY_ASSET,
                                      NOISE_FLOOR_SOURCE_ASSET,
                                      STRIKE_PROXY_NOISE_FLOOR_BPS,
                                      TWAP_LOOKBACK_SEC, StrikeProxy, _ohlc4,
                                      is_inside_noise_floor)


def _bar(open_ts, o, h, l, c):
    """A Binance kline row, only the fields this module reads."""
    return [open_ts * 1000, str(o), str(h), str(l), str(c), '1.0']


class _FakeResp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        if self._payload is _BAD_JSON:
            raise ValueError('no json')
        return self._payload


_BAD_JSON = object()


class _FakeSession:
    """Serves a canned kline payload and counts requests."""

    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status
        self.calls = 0

    def get(self, url, params=None, timeout=None):
        self.calls += 1
        return _FakeResp(self.payload, self.status)


# -- ohlc4 -------------------------------------------------------------------

def test_ohlc4_averages_all_four_components():
    assert _ohlc4(_bar(0, 100, 110, 90, 100)) == pytest.approx(100.0)
    # Not the close: a bar that ranged has an average away from its close.
    assert _ohlc4(_bar(0, 100, 120, 100, 120)) == pytest.approx(110.0)


@pytest.mark.parametrize('bad', ['nan', 'inf', '-inf', '0', '-5'])
def test_ohlc4_rejects_non_finite_and_non_positive(bad):
    """A NaN must never reach a strike comparison (convention 19)."""
    assert _ohlc4(_bar(0, 100, bad, 90, 100)) is None


def test_ohlc4_rejects_malformed_rows():
    assert _ohlc4([]) is None
    assert _ohlc4([0, 'abc', 'x', 'y', 'z']) is None


# -- TWAP alignment ----------------------------------------------------------

def test_twap60_reads_the_bar_that_chainlink_averages():
    """TWAP at t covers [t-60, t), which is the bar whose OPEN is t-60.

    Reading the bar opening at t instead would sample the minute AFTER the
    window opened, which is lookahead: it contains prices the strike cannot
    have been computed from.
    """
    t = 1_787_019_300
    session = _FakeSession([
        _bar(t - 60, 100, 100, 100, 100),   # the correct bar
        _bar(t, 200, 200, 200, 200),        # the lookahead trap
    ])
    proxy = StrikeProxy(session=session)
    assert proxy.twap60(t) == pytest.approx(100.0)


def test_twap60_returns_none_when_the_bar_is_absent():
    t = 1_787_019_300
    proxy = StrikeProxy(session=_FakeSession([_bar(t - 6000, 100, 100, 100, 100)]))
    assert proxy.twap60(t) is None
    assert proxy.health['bar_not_in_window'] == 1


def test_lookback_matches_the_market_config():
    """Chainlink's configured twapLookbackSeconds. Not a tunable."""
    assert TWAP_LOOKBACK_SEC == 60


# -- the guarantees ----------------------------------------------------------

def test_strike_for_never_returns_a_bare_float():
    """A strike without its provenance is the number that gets mistaken for
    an oracle reading three files downstream."""
    t = 1_787_019_300
    proxy = StrikeProxy(session=_FakeSession([_bar(t - 60, 100, 100, 100, 100)]))
    out = proxy.strike_for(t)
    assert isinstance(out, dict)
    assert out['strike'] == pytest.approx(100.0)
    assert out['is_proxy'] is True
    assert 'proxy' in out['source']
    assert out['noise_floor_bps'] == STRIKE_PROXY_NOISE_FLOOR_BPS


def test_unknown_strike_is_none_and_carries_no_source():
    """None means 'we do not know'. It must never be 0.0 and never be spot."""
    proxy = StrikeProxy(session=_FakeSession([]))
    out = proxy.strike_for(1_787_019_300)
    assert out['strike'] is None
    assert out['source'] is None


def test_failed_refresh_keeps_previous_bars_rather_than_blanking():
    """One transient HTTP error must not become a strike outage."""
    t = 1_787_019_300
    session = _FakeSession([_bar(t - 60, 100, 100, 100, 100)])
    proxy = StrikeProxy(session=session, refresh_sec=0.0)
    assert proxy.twap60(t) == pytest.approx(100.0)

    session.status = 500
    assert proxy.twap60(t) == pytest.approx(100.0)      # still served
    assert proxy.health['klines_http_500'] >= 1


def test_http_200_with_an_error_body_is_caught():
    """The binance.com failure mode: the status says fine, the schema does not."""
    proxy = StrikeProxy(session=_FakeSession({'code': -1121, 'msg': 'Invalid symbol.'}))
    assert proxy.twap60(1_787_019_300) is None
    assert proxy.health['klines_not_a_list'] == 1


def test_failure_causes_do_not_share_a_counter():
    """Convention 20: two drop causes never share one number."""
    t = 1_787_019_300
    a = StrikeProxy(session=_FakeSession({'msg': 'err'}))
    a.twap60(t)
    b = StrikeProxy(session=_FakeSession([_bar(t - 60, 100, 'nan', 90, 100)]))
    b.twap60(t)
    assert a.health['klines_not_a_list'] == 1
    assert b.health['bar_malformed_ohlc'] == 1
    assert 'bar_malformed_ohlc' not in a.health
    assert 'klines_not_a_list' not in b.health


def test_cache_is_reused_between_polls():
    """The loop polls every 5s; it must not pull klines every 5s."""
    t = 1_787_019_300
    session = _FakeSession([_bar(t - 60, 100, 100, 100, 100)])
    proxy = StrikeProxy(session=session, refresh_sec=1000.0)
    for _ in range(10):
        proxy.twap60(t)
    assert session.calls == 1


# -- the noise floor ---------------------------------------------------------

def test_leads_inside_the_measured_floor_are_refused():
    """Below the floor the proxy disagreed with the oracle 42% of the time."""
    assert is_inside_noise_floor(0.0)
    assert is_inside_noise_floor(0.5)
    assert is_inside_noise_floor(4.99)
    assert not is_inside_noise_floor(5.01)
    assert not is_inside_noise_floor(-9.0)      # sign must not matter


def test_missing_and_non_finite_leads_are_inside_the_floor():
    """'Could not compute' must not read as 'safe to trade'."""
    assert is_inside_noise_floor(None)
    assert is_inside_noise_floor(float('nan'))
    assert is_inside_noise_floor(float('inf')) is True


def test_floor_is_at_or_above_where_the_proxy_stops_being_a_coin_flip():
    """Guards against someone loosening the floor without re-measuring.

    The measured table says 0-1 bps is 42% wrong and 1-2 bps is 23% wrong.
    A floor at or below 2 would be trading inside known noise (convention 17).
    """
    assert STRIKE_PROXY_NOISE_FLOOR_BPS >= 3.0


def test_the_per_asset_error_matches_the_measurement():
    """The hardcoded per-asset error rates must not drift from the harness.

    `NOISE_FLOOR_ERROR_BY_ASSET` is stamped onto every row the strike gate
    rejects, so it is a claim the logs carry forever. Convention 17: it is a
    hardcoded number and therefore an assumption with an expiry date. This
    re-reads `research/strike_proxy_by_asset_500w.json` - the output of
    `backtest/measure_strike_proxy.py`, the named harness - and fails if the
    constant and the measurement ever disagree. It must read the file the
    constant is SOURCED from, or it pins the constant to a measurement nobody
    is using and goes green on a stale number.

    Re-running the harness is what makes this red. That is the point: the
    constant is then updated deliberately rather than silently outliving the
    numbers it was derived from.
    """
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'research', 'strike_proxy_by_asset_500w.json')
    if not os.path.exists(path):
        pytest.skip('measurement not present: %s' % path)
    with open(path) as fh:
        measured = json.load(fh)

    by_asset = {}
    for row in measured['by_asset']:
        at_floor = [c for c in row['cumulative']
                    if c['threshold_bps'] == STRIKE_PROXY_NOISE_FLOOR_BPS]
        assert len(at_floor) == 1, (row['asset'], row['cumulative'])
        # The JSON is a percent; the constant is a fraction. Two units for one
        # quantity is exactly how a 15.8% becomes a 0.158% in a report.
        by_asset[row['asset']] = round(at_floor[0]['rate_pct'] / 100.0, 4)

    assert by_asset == NOISE_FLOOR_ERROR_BY_ASSET, (
        'constant %r has drifted from %s (%r) - re-derive it, do not edit '
        'the test' % (NOISE_FLOOR_ERROR_BY_ASSET, path, by_asset))


def test_the_floor_is_sourced_from_an_asset_that_was_actually_measured():
    """`noise_floor_source` names where the constant came from, not a label."""
    assert NOISE_FLOOR_SOURCE_ASSET in NOISE_FLOOR_ERROR_BY_ASSET
    # And it must be the asset with the LOWEST measured error, because that is
    # the honest reading of "inherited from BTC": every other asset is being
    # trusted on evidence gathered somewhere it behaves better. If that ever
    # stops being true the inheritance argument has inverted.
    best = min(NOISE_FLOOR_ERROR_BY_ASSET,
               key=lambda a: NOISE_FLOOR_ERROR_BY_ASSET[a])
    assert NOISE_FLOOR_SOURCE_ASSET == best, NOISE_FLOOR_ERROR_BY_ASSET


# ---------------------------------------------------------------------------
# D-297: the per-asset error as a SCALAR on the row, not just as a dict
# ---------------------------------------------------------------------------

def test_error_at_floor_pct_is_a_percent_matching_the_constant():
    """The row field is a PERCENT; the constant is a FRACTION.

    Two units for one quantity is exactly how a 15.8% becomes a 0.158% three
    files downstream, so the conversion has one owner and this pins it.
    """
    from engine.polymarket.strike import error_at_floor_pct_for
    for asset, fraction in NOISE_FLOOR_ERROR_BY_ASSET.items():
        assert error_at_floor_pct_for(asset) == round(fraction * 100.0, 1)
    assert error_at_floor_pct_for('btc') == 5.1
    assert error_at_floor_pct_for('eth') == 9.3
    assert error_at_floor_pct_for('sol') == 15.8


def test_an_unmeasured_asset_reads_as_unknown_never_as_zero():
    """The convention 11 shape at the level of one field.

    A fourth asset added to SHADOW_ASSETS without being measured must not
    stamp 0.0, which reads as a PERFECT proxy - the best possible number
    produced by the total absence of evidence.
    """
    from engine.polymarket.strike import error_at_floor_pct_for
    for unmeasured in ('doge', 'DOGE', '', None):
        assert error_at_floor_pct_for(unmeasured) is None


def test_the_asset_lookup_is_case_insensitive():
    """Slugs are lowercase, `assets.py` labels are not. One spelling of the
    asset must not silently become an unmeasured one."""
    from engine.polymarket.strike import error_at_floor_pct_for
    assert error_at_floor_pct_for('BTC') == error_at_floor_pct_for('btc')


def test_the_shadow_loop_stamps_the_scalar_on_every_gated_row():
    """Convention 22: a claim in a comment is not a wiring test.

    D-297 is only delivered if the number reaches the ROW. The gate is deep
    inside a cycle, so this asserts on the source: the field name, the
    unavailable flag and the helper must all be at the gate site.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'engine', 'polymarket', 'shadow_loop.py')
    src = open(path).read()
    assert 'strike_proxy_error_at_floor_pct=error_pct' in src
    assert 'error_at_floor_pct_for(row_asset)' in src
    assert 'ERROR_UNAVAILABLE_FLAG' in src


# ---------------------------------------------------------------------------
# D-297, second half: the SAMPLE behind each of those percentages
# ---------------------------------------------------------------------------

def test_the_per_asset_sample_size_matches_the_measurement():
    """`n` drifts from the harness exactly as easily as the rate does.

    Same construction as the rate drift test above and for the same reason:
    re-running `backtest/measure_strike_proxy.py` must make this red rather
    than silently leaving a 220-window `n` stamped on rows measured over 500.
    That is not hypothetical any more - it is what the 500w repoint did, and
    this test is what would have caught the `n` had it been left behind. It
    reads the same file as the rate drift test, by construction: a rate and an
    `n` sourced from two different samples is the exact defect D-297 exists to
    prevent.
    """
    from engine.polymarket.strike import NOISE_FLOOR_ERROR_N_BY_ASSET
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        'research', 'strike_proxy_by_asset_500w.json')
    if not os.path.exists(path):
        pytest.skip('measurement not present: %s' % path)
    with open(path) as fh:
        measured = json.load(fh)

    n_by_asset = {}
    for row in measured['by_asset']:
        at_floor = [c for c in row['cumulative']
                    if c['threshold_bps'] == STRIKE_PROXY_NOISE_FLOOR_BPS]
        assert len(at_floor) == 1, (row['asset'], row['cumulative'])
        n_by_asset[row['asset']] = at_floor[0]['n']

    assert n_by_asset == NOISE_FLOOR_ERROR_N_BY_ASSET, (
        'constant %r has drifted from %s (%r) - re-derive it, do not edit '
        'the test' % (NOISE_FLOOR_ERROR_N_BY_ASSET, path, n_by_asset))


def test_every_rate_has_a_sample_size():
    """A percentage with no `n` beside it is a verdict pretending to be one.

    The two dicts must cover the same assets. A rate added without its sample
    would publish the exact claim D-297 exists to qualify.
    """
    from engine.polymarket.strike import NOISE_FLOOR_ERROR_N_BY_ASSET
    assert set(NOISE_FLOOR_ERROR_N_BY_ASSET) == set(NOISE_FLOOR_ERROR_BY_ASSET)


def test_low_sample_is_derived_from_n_not_asserted_per_asset():
    """Convention 7 applied by arithmetic, so it cannot be wrong about who.

    D-297 names SOL, and at 220 windows the measurement said BTC was thinner
    still (n=75 vs 84) with only ETH (n=106) clearing the threshold. At 500
    windows all three clear it and the flag is False everywhere. Hardcoding
    "sol is the low sample one" would have shipped the first error and then
    survived the re-measurement that fixed it; deriving from `n` cannot do
    either.
    """
    from engine.polymarket.strike import (LOW_SAMPLE_N,
                                          NOISE_FLOOR_ERROR_N_BY_ASSET,
                                          error_sample_at_floor_for)
    assert LOW_SAMPLE_N == 100
    for asset, n in NOISE_FLOOR_ERROR_N_BY_ASSET.items():
        got_n, low = error_sample_at_floor_for(asset)
        assert got_n == n
        assert low is (n < LOW_SAMPLE_N)
    assert error_sample_at_floor_for('sol') == (196, False)
    assert error_sample_at_floor_for('btc') == (175, False)
    assert error_sample_at_floor_for('eth') == (248, False)


def test_an_unmeasured_asset_has_no_sample_and_no_flag():
    """Unknown is not "well sampled" and it is not "poorly sampled" either.

    `False` here would read as "we checked and the evidence is solid", which
    is the same convention 11 inversion a 0.0% rate would be.
    """
    from engine.polymarket.strike import error_sample_at_floor_for
    for unmeasured in ('doge', 'DOGE', '', None):
        assert error_sample_at_floor_for(unmeasured) == (None, None)
    assert error_sample_at_floor_for('BTC') == error_sample_at_floor_for('btc')


def test_the_shadow_loop_stamps_the_sample_on_every_gated_row():
    """Convention 22 again: the percent reaching the row is only half of D-297.

    Without `n` on the same row a reader compares a 5.1% built on 175 windows
    against a 15.8% built on 196 as though they were the same kind of number.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'engine', 'polymarket', 'shadow_loop.py')
    src = open(path).read()
    assert 'strike_proxy_error_n=error_n' in src
    assert 'strike_proxy_error_low_sample=error_low_sample' in src
    assert 'error_sample_at_floor_for(row_asset)' in src
