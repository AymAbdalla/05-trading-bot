"""Cross-checks: production indicators (ta-backed facades) vs the original
hand-rolled reference implementations.

Direction of trust after Aym's 2026-08-12 ruling: PRODUCTION math comes from
the maintained `ta` library (via facades in indicators/ that keep the old
signatures); the audited hand-rolled versions live in
tests/reference_indicators.py as the independent referee. Two unrelated
implementations must agree on the same series, forever. A failure after any
indicator edit means semantics changed - stop and find out why.

Seeds differ between implementations (SMA-seed vs ewm first-value seed), so
comparisons are made on the tail of a long series where seed differences
have fully decayed.
"""
import math
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators.rsi import latest_rsi, rsi
from indicators.atr import latest_atr, atr
from indicators.ema import latest_ema, ema_slope
from indicators.macd_stoch import bollinger_bands

from reference_indicators import ref_rsi, ref_atr, ref_ema, ref_bollinger


N = 600          # long series: seed differences fully decay
TAIL_TOL = 5e-3  # relative tolerance on the final value


@pytest.fixture(scope='module')
def series():
    rng = random.Random(7)
    closes, highs, lows = [], [], []
    px = 100.0
    for _ in range(N):
        px = max(1.0, px * (1 + rng.gauss(0.0003, 0.02)))
        closes.append(px)
        spread = abs(rng.gauss(0, 0.01)) * px
        highs.append(px + spread)
        lows.append(max(0.5, px - spread))
    return highs, lows, closes


def test_rsi_matches_reference(series):
    _, _, closes = series
    assert latest_rsi(closes, 14) == pytest.approx(ref_rsi(closes, 14)[-1],
                                                   rel=TAIL_TOL, abs=0.1)


def test_atr_matches_reference(series):
    highs, lows, closes = series
    assert latest_atr(highs, lows, closes, 14) == pytest.approx(
        ref_atr(highs, lows, closes, 14)[-1], rel=TAIL_TOL)


def test_ema_matches_reference(series):
    _, _, closes = series
    assert latest_ema(closes, 50) == pytest.approx(ref_ema(closes, 50)[-1],
                                                   rel=TAIL_TOL)


def test_bollinger_matches_reference(series):
    _, _, closes = series
    lower, mid, upper = bollinger_bands(closes, period=20, std_mult=2.0)
    r_lower, r_mid, r_upper = ref_bollinger(closes, period=20, std_mult=2.0)
    assert mid == pytest.approx(r_mid, rel=TAIL_TOL)
    assert upper == pytest.approx(r_upper, rel=TAIL_TOL)
    assert lower == pytest.approx(r_lower, rel=TAIL_TOL)


def test_padding_conventions_preserved():
    """The facades must keep the original padding behavior that every
    warmup guard in the scanner/harnesses was written against."""
    short = [100.0, 101.0, 102.0]
    assert rsi(short, 14) == [50.0] * 3
    assert atr(short, [99.0] * 3, short, 14) == [0.0] * 3
    assert ema_slope(short, 50, 10) == 0.0  # insufficient data -> 0, never garbage


def test_vectorized_precompute_matches_reference(series):
    """The vectorized harness's precomputed arrays go through the same ta
    backend; their tails must agree with the reference too."""
    import numpy as np
    from backtest.vectorized_harness import _wilder_atr, _wilder_rsi, _ema
    highs, lows, closes = series
    h, l, c = np.array(highs), np.array(lows), np.array(closes)
    assert _wilder_rsi(c, 14)[-1] == pytest.approx(ref_rsi(closes, 14)[-1], rel=TAIL_TOL, abs=0.1)
    assert _wilder_atr(h, l, c, 14)[-1] == pytest.approx(ref_atr(highs, lows, closes, 14)[-1], rel=TAIL_TOL)
    assert _ema(c, 50)[-1] == pytest.approx(ref_ema(closes, 50)[-1], rel=TAIL_TOL)


def test_black_scholes_put_call_parity():
    """The options pricer (vollib-backed) must satisfy put-call parity."""
    from backtest.synthetic_options import black_scholes_call, black_scholes_put
    for spot, strike, t, r, v in [(100, 105, 0.25, 0.05, 0.3),
                                  (50, 40, 1.0, 0.02, 0.6),
                                  (200, 200, 0.08, 0.05, 0.2)]:
        c = black_scholes_call(spot, strike, t, r, v)
        p = black_scholes_put(spot, strike, t, r, v)
        parity = spot - strike * math.exp(-r * t)
        assert c - p == pytest.approx(parity, abs=1e-9)
