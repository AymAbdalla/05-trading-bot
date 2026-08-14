"""Tests for strategies/builtin/strategy_lab_v2.py.

Two jobs:
1. No strategy may raise on degenerate input. The sweep runs these across
   hundreds of files; one exception would silently zero a strategy out.
2. No strategy may be unfireable. A strategy that can never trigger is worse
   than a bad one because the graveyard records it as "no trades" instead of
   "wrong". Every trigger test below is paired with a near-miss that must
   return None, so a strategy that fires unconditionally fails too.
"""
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.builtin.strategy_lab_v2 import (  # noqa: E402
    STRATEGY_LAB_V2_STRATEGIES,
    WickAutopsy, RoundNumberDefenseDecay, LiquidationEcho, SecondBreakVerdict,
    VolumeDesertBreakout, VWAPMagnetClose, ExpiryPinDrift, ZeroDTEAmplifier,
    _level_increment,
)

ET = ZoneInfo('America/New_York')


# ============ candle builders ============

def blank():
    return {'opens': [], 'highs': [], 'lows': [], 'closes': [], 'volumes': [],
            'timestamps': []}


def make(bars, start_ts_ms=1_700_000_000_000, step_ms=300_000):
    """bars: list of (open, high, low, close, volume) tuples."""
    c = blank()
    for i, (o, h, l, cl, v) in enumerate(bars):
        c['opens'].append(float(o))
        c['highs'].append(float(h))
        c['lows'].append(float(l))
        c['closes'].append(float(cl))
        c['volumes'].append(float(v))
        c['timestamps'].append(start_ts_ms + i * step_ms)
    return c


def et_ms(y, m, d, hh, mm):
    """ET wall clock to epoch milliseconds."""
    return int(datetime(y, m, d, hh, mm, tzinfo=ET).timestamp() * 1000)


def session_bars(date_parts, n_bars, price, step_min=5, first_min=9 * 60 + 30):
    """Timestamps for n_bars 5m bars starting at first_min ET on a date."""
    y, m, d = date_parts
    base = datetime(y, m, d, 0, 0, tzinfo=ET) + timedelta(minutes=first_min)
    return [int((base + timedelta(minutes=step_min * i)).timestamp() * 1000)
            for i in range(n_bars)]


# Known good trading days (all are NYSE sessions, no holidays):
#   2024-08-12 Mon, 2024-08-14 Wed, 2024-08-16 Fri, 2024-08-13 Tue
MON = (2024, 8, 12)
TUE = (2024, 8, 13)
WED = (2024, 8, 14)


# ============ 1. hostile input: nothing may raise ============

DEGENERATE = {
    'empty': blank(),
    'five_bars': make([(100, 101, 99, 100, 1000)] * 5),
    'all_flat': make([(100, 100, 100, 100, 1000)] * 400),
    'zero_volume': make([(100 + i * 0.01, 100.5 + i * 0.01, 99.5 + i * 0.01,
                          100.2 + i * 0.01, 0) for i in range(400)]),
    'single_bar': make([(100, 101, 99, 100, 10)]),
    'missing_keys': {'closes': [1, 2, 3]},
    'nones_absent': {},
}


@pytest.mark.parametrize('strategy', STRATEGY_LAB_V2_STRATEGIES,
                         ids=[s.name for s in STRATEGY_LAB_V2_STRATEGIES])
@pytest.mark.parametrize('case', sorted(DEGENERATE))
def test_degenerate_input_returns_none(strategy, case):
    assert strategy.scan(DEGENERATE[case]) is None


@pytest.mark.parametrize('strategy', STRATEGY_LAB_V2_STRATEGIES,
                         ids=[s.name for s in STRATEGY_LAB_V2_STRATEGIES])
def test_negative_and_huge_prices_do_not_raise(strategy):
    weird = make([(0, 0, 0, 0, 0)] * 200 + [(1e12, 1e12, 1e12, 1e12, 1e12)] * 200)
    assert strategy.scan(weird) is None


# ============ 2. registry invariants ============

def test_names_unique_and_prefixed():
    names = [s.name for s in STRATEGY_LAB_V2_STRATEGIES]
    assert len(names) == len(set(names)), f"duplicate names: {names}"
    for n in names:
        assert n.startswith('V2_'), n


def test_all_are_entry_strategies():
    for s in STRATEGY_LAB_V2_STRATEGIES:
        assert s.is_entry is True, s.name


def test_all_constructible_fresh():
    for cls in (WickAutopsy, RoundNumberDefenseDecay, LiquidationEcho,
                SecondBreakVerdict, VolumeDesertBreakout, VWAPMagnetClose,
                ExpiryPinDrift, ZeroDTEAmplifier):
        inst = cls()
        assert inst.name.startswith('V2_')
        assert inst.scan(blank()) is None


def test_scan_window_is_enough_for_every_strategy():
    """min_bars over 260 makes the harness report NOT_TESTED. None of these
    should need that; if one grows past it, the change must be deliberate."""
    for s in STRATEGY_LAB_V2_STRATEGIES:
        assert getattr(s, 'min_bars', 0) <= 260, s.name


def test_level_increment_ladder():
    assert _level_increment(100_000) == 1000.0   # BTC
    assert _level_increment(2500) == 100.0       # ETH
    assert _level_increment(600) == 10.0         # SPY
    assert _level_increment(220) == 5.0          # AAPL
    assert _level_increment(30) == 1.0
    assert _level_increment(0) is None
    assert _level_increment(float('nan')) is None


def _assert_long_signal(sig, name):
    assert sig is not None, f"{name} did not fire on its trigger fixture"
    assert sig.direction == 'bullish'
    assert sig.pattern == name
    assert sig.entry is not None and sig.stop is not None
    assert sig.stop < sig.entry, f"{name}: stop {sig.stop} not below entry {sig.entry}"
    if sig.target is not None:
        assert sig.target > sig.entry
    assert 0.0 < sig.confidence <= 1.0


# ============ 3. must-fire and near-miss fixtures ============

def _absorption_bars(n=24, top_third=True, flat=True):
    """Candles with fat lower wicks that close near their highs."""
    bars = []
    base = 100.0
    for i in range(n):
        drift = 0.0 if flat else i * 0.25
        o = base + drift
        low = o - 1.0            # long lower wick
        high = o + 0.12          # short upper wick
        close = high - 0.02 if top_third else low + 0.05
        bars.append((o, high, low, close, 1000))
    return bars


def test_wick_autopsy_fires():
    # 20 warmup + 20 absorption candles, then one wide bullish expansion bar.
    warmup = [(100, 100.6, 99.4, 100.0, 1000) for _ in range(20)]
    absorb = _absorption_bars(20)
    trigger = (100.0, 104.0, 99.9, 103.8, 5000)   # range 4.1 vs median ~1.12
    sig = WickAutopsy().scan(make(warmup + absorb + [trigger]))
    _assert_long_signal(sig, 'V2_wick_autopsy')
    assert sig.features['absorption_score'] > 1.6


def test_wick_autopsy_near_miss_closes_in_bottom_third():
    warmup = [(100, 100.6, 99.4, 100.0, 1000) for _ in range(20)]
    absorb = _absorption_bars(20, top_third=False)
    trigger = (100.0, 104.0, 99.9, 103.8, 5000)
    assert WickAutopsy().scan(make(warmup + absorb + [trigger])) is None


def test_wick_autopsy_near_miss_no_range_expansion():
    warmup = [(100, 100.6, 99.4, 100.0, 1000) for _ in range(20)]
    absorb = _absorption_bars(20)
    trigger = (100.0, 100.6, 99.9, 100.5, 5000)   # ordinary range
    assert WickAutopsy().scan(make(warmup + absorb + [trigger])) is None


def _round_number_bars(decaying=True):
    """Price chops under the 220 level, testing it with shrinking wicks."""
    bars = []
    for _ in range(20):
        bars.append((218.0, 218.4, 217.6, 218.0, 1000))
    # first test: big rejection wick above 220
    bars.append((219.0, 221.5, 218.8, 219.2, 2000))
    for _ in range(8):
        bars.append((219.0, 219.4, 218.6, 219.0, 1000))
    # second test: small rejection wick if decaying, bigger if not
    poke = 220.3 if decaying else 222.0
    bars.append((219.5, poke, 219.2, 219.6, 2000))
    for _ in range(8):
        bars.append((219.5, 219.9, 219.1, 219.6, 1000))
    bars.append((219.7, 219.9, 219.5, 219.8, 1000))   # prev close below level
    bars.append((219.8, 221.0, 219.7, 220.9, 4000))   # break and close above
    return bars


def test_round_number_break_fires():
    sig = RoundNumberDefenseDecay().scan(make(_round_number_bars(True)))
    _assert_long_signal(sig, 'V2_round_number_decay')
    assert sig.features['mode'] == 'resistance_break'
    assert sig.features['level'] == 220.0
    assert sig.features['last_wick'] < 0.5 * sig.features['first_wick']


def test_round_number_break_near_miss_defense_not_decaying():
    assert RoundNumberDefenseDecay().scan(make(_round_number_bars(False))) is None


def test_round_number_near_miss_no_prior_tests():
    """Same break, but the level was never tested: fresh level, no trade."""
    bars = [(218.0, 218.4, 217.6, 218.0, 1000) for _ in range(38)]
    bars.append((219.7, 219.9, 219.5, 219.8, 1000))
    bars.append((219.8, 221.0, 219.7, 220.9, 4000))
    assert RoundNumberDefenseDecay().scan(make(bars)) is None


def _cascade_bars(hold_above_swing=True, volume_spike=True):
    bars = []
    # 20 quiet bars, then a pivot low at index ~22, then quiet again
    for _ in range(20):
        bars.append((100.0, 100.4, 99.6, 100.1, 1000))
    bars.append((100.0, 100.2, 96.0, 99.8, 1200))     # pivot low at 96.0
    for _ in range(22):
        bars.append((100.0, 100.4, 99.6, 100.1, 1000))
    # cascade: 3 down candles with expanding ranges (0.6 -> 0.9 -> 1.0+)
    spike = 40000 if volume_spike else 1000
    floor_lvl = 98.0 if hold_above_swing else 94.0
    bars.append((100.0, 100.1, 99.5, 99.6, spike // 3))
    bars.append((99.6, 99.7, 98.8, 98.9, spike // 2))
    bars.append((98.9, 99.0, floor_lvl, floor_lvl + 0.1, spike))
    # recovery candle closing above its own open
    bars.append((floor_lvl + 0.1, floor_lvl + 0.9, floor_lvl, floor_lvl + 0.8, 3000))
    return bars


def test_liquidation_echo_fires():
    sig = LiquidationEcho().scan(make(_cascade_bars()))
    _assert_long_signal(sig, 'V2_liquidation_echo')
    assert sig.features['cascade_len'] >= 3
    assert sig.features['volume_z'] > 3.0
    assert sig.features['cascade_low'] > sig.features['prior_swing_low']


def test_liquidation_echo_near_miss_reached_the_pool():
    """Cascade punches through the prior swing low: not an exhausted echo."""
    assert LiquidationEcho().scan(make(_cascade_bars(hold_above_swing=False))) is None


def test_liquidation_echo_near_miss_no_volume_spike():
    assert LiquidationEcho().scan(make(_cascade_bars(volume_spike=False))) is None


def _second_break_candles(reclaim=True, or_too_narrow=False):
    """One session: wide opening range, break below OR low, then reclaim."""
    ts = session_bars(TUE, 60, 100.0)
    c = blank()

    def push(i, o, h, l, cl, v=1000):
        c['opens'].append(o); c['highs'].append(h); c['lows'].append(l)
        c['closes'].append(cl); c['volumes'].append(v); c['timestamps'].append(ts[i])

    width = 0.4 if or_too_narrow else 3.0
    # 9:30-10:00 = 6 bars of opening range
    push(0, 100.0, 100.0 + width, 100.0, 100.5)
    for i in range(1, 6):
        push(i, 100.5, 100.0 + width, 100.0, 100.5)
    # quiet drift inside the range: small bars keep the bar ATR low so the
    # opening range lands inside the 0.5 to 2.0 session-ATR band
    for i in range(6, 40):
        push(i, 100.5, 100.7, 100.4, 100.6)
    # break below the OR low
    push(40, 100.4, 100.5, 99.0, 99.2, 4000)
    # reclaim back inside (or stay outside for the near miss)
    last_close = 100.8 if reclaim else 98.9
    push(41, 99.2, 101.0, 99.1, last_close, 5000)
    return c


def test_second_break_fires():
    sig = SecondBreakVerdict().scan(_second_break_candles(reclaim=True))
    _assert_long_signal(sig, 'V2_second_break')
    assert sig.features['break_bars'] == 1
    assert sig.target == pytest.approx(sig.features['or_high'])


def test_second_break_near_miss_never_reclaims():
    assert SecondBreakVerdict().scan(_second_break_candles(reclaim=False)) is None


def test_second_break_near_miss_dead_opening_range():
    assert SecondBreakVerdict().scan(_second_break_candles(or_too_narrow=True)) is None


def _volume_desert_candles(spike=True):
    """Two sessions of 5m bars; the trigger sits in the 12:00-13:30 window."""
    c = blank()

    def push(ts_ms, o, h, l, cl, v):
        c['opens'].append(o); c['highs'].append(h); c['lows'].append(l)
        c['closes'].append(cl); c['volumes'].append(v); c['timestamps'].append(ts_ms)

    for day in (MON, TUE):
        for k in range(78):                     # 9:30 to 16:00
            minute = 9 * 60 + 30 + 5 * k
            ts = et_ms(day[0], day[1], day[2], minute // 60, minute % 60)
            push(ts, 100.0, 100.3, 99.7, 100.05, 1000)
    # trigger bar on Wednesday at 12:30 ET
    for k in range(37):                          # 9:30 to 12:30
        minute = 9 * 60 + 30 + 5 * k
        ts = et_ms(WED[0], WED[1], WED[2], minute // 60, minute % 60)
        push(ts, 100.0, 100.3, 99.7, 100.05, 1000)
    v = 9000 if spike else 1000
    h = 102.0 if spike else 100.3
    push(et_ms(WED[0], WED[1], WED[2], 12, 35), 100.0, h, 99.7, h - 0.1, v)
    return c


def test_volume_desert_fires():
    sig = VolumeDesertBreakout().scan(_volume_desert_candles(spike=True))
    _assert_long_signal(sig, 'V2_volume_desert')
    assert sig.target is None, "exit is a trail supplied by the harness"
    assert sig.features['volume_mult'] > 2.0


def test_volume_desert_near_miss_ordinary_lunch_bar():
    assert VolumeDesertBreakout().scan(_volume_desert_candles(spike=False)) is None


def _vwap_candles(stretched=True):
    """One session ending on the 15:30 bar, price far below session VWAP."""
    c = blank()

    def push(ts_ms, o, h, l, cl, v):
        c['opens'].append(o); c['highs'].append(h); c['lows'].append(l)
        c['closes'].append(cl); c['volumes'].append(v); c['timestamps'].append(ts_ms)

    # 9:30 through 15:25 traded around 100, building VWAP near 100
    for k in range(71):
        minute = 9 * 60 + 30 + 5 * k
        ts = et_ms(TUE[0], TUE[1], TUE[2], minute // 60, minute % 60)
        push(ts, 100.0, 100.2, 99.8, 100.0, 1000)
    last = 98.0 if stretched else 99.98
    push(et_ms(TUE[0], TUE[1], TUE[2], 15, 30), 99.9, 100.0, last - 0.05, last, 1000)
    return c


def test_vwap_magnet_fires():
    sig = VWAPMagnetClose().scan(_vwap_candles(stretched=True))
    _assert_long_signal(sig, 'V2_vwap_magnet')
    assert sig.target == pytest.approx(sig.features['vwap'])
    assert sig.features['stretch_atr'] > 0.75


def test_vwap_magnet_near_miss_price_hugging_vwap():
    assert VWAPMagnetClose().scan(_vwap_candles(stretched=False)) is None


def test_vwap_magnet_near_miss_wrong_time_of_day():
    c = _vwap_candles(stretched=True)
    # move every timestamp back two hours: same geometry, 13:30 instead of 15:30
    c['timestamps'] = [t - 2 * 3600 * 1000 for t in c['timestamps']]
    assert VWAPMagnetClose().scan(c) is None


def _expiry_candles(day, hour, minute, price):
    c = blank()
    n = 40
    for i in range(n):
        total = hour * 60 + minute - 5 * (n - 1 - i)
        ts = et_ms(day[0], day[1], day[2], total // 60, total % 60)
        p = price if i == n - 1 else price - 0.5
        c['opens'].append(p - 0.05); c['highs'].append(p + 0.1)
        c['lows'].append(p - 0.15); c['closes'].append(p)
        c['volumes'].append(1000); c['timestamps'].append(ts)
    return c


def test_expiry_pin_fires():
    # Wednesday 14:30 ET, AAPL-like price just under the 220 strike ($5 ladder)
    sig = ExpiryPinDrift().scan(_expiry_candles(WED, 14, 30, 219.8))
    _assert_long_signal(sig, 'V2_expiry_pin')
    assert sig.features['strike'] == 220.0
    assert sig.features['distance_pct'] <= 0.003


def test_expiry_pin_near_miss_too_far_from_strike():
    assert ExpiryPinDrift().scan(_expiry_candles(WED, 14, 30, 217.0)) is None


def test_expiry_pin_near_miss_wrong_weekday():
    # Tuesday is not on the Mon/Wed/Fri expiry calendar
    assert ExpiryPinDrift().scan(_expiry_candles(TUE, 14, 30, 219.8)) is None


def test_expiry_pin_near_miss_before_1400():
    assert ExpiryPinDrift().scan(_expiry_candles(WED, 11, 30, 219.8)) is None


def _zero_dte_candles(day=WED, breaks=True, held=True):
    c = blank()

    def push(ts_ms, o, h, l, cl, v=1000):
        c['opens'].append(o); c['highs'].append(h); c['lows'].append(l)
        c['closes'].append(cl); c['volumes'].append(v); c['timestamps'].append(ts_ms)

    # 9:30 to 12:00 morning range 99.0 - 101.0
    for k in range(30):
        minute = 9 * 60 + 30 + 5 * k
        hi = 101.0 if k == 5 else 100.4
        lo = 99.0 if k == 9 else 99.8
        push(et_ms(day[0], day[1], day[2], minute // 60, minute % 60),
             100.0, hi, lo, 100.1)
    # 12:00 to 14:00 midday, inside the range (or breaking it for the near miss)
    for k in range(24):
        minute = 12 * 60 + 5 * k
        hi = 100.5 if held else 102.5
        push(et_ms(day[0], day[1], day[2], minute // 60, minute % 60),
             100.1, hi, 99.6, 100.2)
    # 14:00 onward: two afternoon bars, the last one breaking the morning high
    push(et_ms(day[0], day[1], day[2], 14, 0), 100.2, 100.6, 100.0, 100.4)
    last = 101.6 if breaks else 100.7
    push(et_ms(day[0], day[1], day[2], 14, 5), 100.4, last + 0.1, 100.3, last, 4000)
    return c


def test_zero_dte_amplifier_fires():
    sig = ZeroDTEAmplifier().scan(_zero_dte_candles())
    _assert_long_signal(sig, 'V2_0dte_amplifier')
    assert sig.features['morning_high'] == pytest.approx(101.0)
    assert sig.entry > sig.features['morning_high']


def test_zero_dte_near_miss_no_break():
    assert ZeroDTEAmplifier().scan(_zero_dte_candles(breaks=False)) is None


def test_zero_dte_near_miss_range_broken_at_midday():
    assert ZeroDTEAmplifier().scan(_zero_dte_candles(held=False)) is None


def test_zero_dte_near_miss_non_expiry_weekday():
    assert ZeroDTEAmplifier().scan(_zero_dte_candles(day=TUE)) is None


# ============ 4. no lookahead: truncating the future cannot change the past ============

@pytest.mark.parametrize('strategy', STRATEGY_LAB_V2_STRATEGIES,
                         ids=[s.name for s in STRATEGY_LAB_V2_STRATEGIES])
def test_scan_ignores_extra_future_keys(strategy):
    """The harness only populates 'future_closes' for control oracles. A real
    strategy must produce the identical signal whether or not it is present."""
    fixtures = [_second_break_candles(), _vwap_candles(),
                make(_round_number_bars()), make(_cascade_bars())]
    for c in fixtures:
        clean = strategy.scan(dict(c))
        poisoned = dict(c)
        poisoned['future_closes'] = [1e9] * 50
        after = strategy.scan(poisoned)
        assert (clean is None) == (after is None)
        if clean is not None:
            assert clean.entry == after.entry and clean.stop == after.stop


@pytest.mark.parametrize('strategy', STRATEGY_LAB_V2_STRATEGIES,
                         ids=[s.name for s in STRATEGY_LAB_V2_STRATEGIES])
def test_every_emitted_signal_has_a_valid_stop(strategy):
    """Sweep a synthetic random-ish series and assert the risk gate invariant
    holds for every signal the strategy chooses to emit."""
    bars = []
    price = 100.0
    for i in range(600):
        price *= 1.0 + math.sin(i * 0.37) * 0.004 + math.cos(i * 0.11) * 0.002
        rng = price * (0.002 + abs(math.sin(i * 0.53)) * 0.006)
        o = price - rng * 0.2
        bars.append((o, price + rng * 0.6, price - rng * 0.6, price,
                     1000 + 5000 * abs(math.sin(i * 0.29))))
    start = et_ms(2024, 8, 12, 9, 30)
    for end in range(120, 601, 7):
        c = make(bars[:end], start_ts_ms=start)
        sig = strategy.scan(c)
        if sig is not None:
            assert sig.entry is not None and sig.stop is not None
            assert sig.stop < sig.entry
            if sig.target is not None:
                assert sig.target > sig.entry
