"""Tests for the measured proxy strike.

The point of these is not that the arithmetic works. It is that the module
cannot quietly degrade into the thing the previous session refused to build:
spot substituted for a Chainlink TWAP with no record that it happened.
"""
import math
import sys
import os

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.strike import (STRIKE_PROXY_NOISE_FLOOR_BPS,
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
