"""Tests for SPEC 5.6 strategy inversion (signal-as-exit fade) and its
F2 eligibility gate."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.inversion import (is_inversion_eligible, run_fade_test,
                                MIN_TRADES_FOR_INVERSION,
                                MAX_GROSS_PF_FOR_INVERSION)
from backtest.vectorized_harness import VectorizedBacktestHarness, precompute_indicators
from strategies.base import Strategy, Signal


CONFIG = {'risk': {'notional_cap_usd': 100},
          'exchange': {'fees': {'taker': 0.001}, 'slippage': {'market': 0.0}},
          'strategy': {'confirmation': {'apply_confirmation_stack': False}}}


class TestF2Gate:
    """The gate that stops inversion from mining noise and fee drag."""

    def _entry(self, **kw):
        base = {'inversion_flagged': True, 'trades': 50, 'gross_pf': 0.5, 'pf': 0.2}
        base.update(kw)
        return base

    def test_eligible_when_gross_edge_negative_and_sample_adequate(self):
        assert is_inversion_eligible(self._entry()) is None

    def test_rejects_unflagged(self):
        assert 'not flagged' in is_inversion_eligible(self._entry(inversion_flagged=False))

    def test_rejects_small_sample(self):
        reason = is_inversion_eligible(self._entry(trades=MIN_TRADES_FOR_INVERSION - 1))
        assert 'sample too small' in reason

    def test_rejects_cost_driven_failure(self):
        """THE F2 test: net PF terrible but gross PF fine means the strategy
        lost to FEES, not to being wrong. Inverting it inverts the edge but
        NOT the costs, so it would just produce a differently-shaped loser."""
        reason = is_inversion_eligible(self._entry(gross_pf=1.05, pf=0.2))
        assert 'lost to COSTS' in reason

    def test_rejects_infinite_gross_pf(self):
        reason = is_inversion_eligible(self._entry(gross_pf=None))
        assert 'infinite' in reason

    def test_boundary_gross_pf(self):
        assert is_inversion_eligible(self._entry(gross_pf=MAX_GROSS_PF_FOR_INVERSION)) is None
        assert is_inversion_eligible(
            self._entry(gross_pf=MAX_GROSS_PF_FOR_INVERSION + 0.01)) is not None


class SignalAtBars(Strategy):
    """Fires on a fixed set of bar indices (identified by close price tag)."""
    name = 'fires_at_peaks'
    is_entry = True

    def __init__(self, fire_indices):
        self.fire_indices = set(fire_indices)

    def scan(self, candles):
        i = len(candles['closes']) - 1
        if i in self.fire_indices:
            px = candles['closes'][-1]
            return Signal(pair='', pattern=self.name, direction='bullish',
                          confidence=0.5, features={}, entry=px, stop=px * 0.98)
        return None


def _candles(prices):
    return [{'ts': 1700000000000 + i * 900000, 'open': p, 'high': p * 1.001,
             'low': p * 0.999, 'close': p, 'volume': 100.0}
            for i, p in enumerate(prices)]


class TestFadeTest:
    def test_fade_beats_hold_when_signal_precedes_drops(self):
        """A signal that reliably fires right before declines SHOULD show
        positive fade edge: stepping out beats holding through."""
        # Sawtooth: rise to 110 then crash to 100, repeatedly. Signal fires
        # at every peak (the bar before each crash).
        prices, peaks = [], []
        px = 100.0
        for cycle in range(12):
            for _ in range(10):
                px += 1.0
                prices.append(px)
            peaks.append(len(prices) - 1)
            px -= 10.0
            prices.append(px)
        candles = _candles([100.0] * 110 + prices)  # 110 warmup bars
        peak_indices = [110 + p for p in peaks]

        harness = VectorizedBacktestHarness(CONFIG)
        ind = precompute_indicators(candles)
        res = run_fade_test(harness, SignalAtBars(peak_indices), ind, 'X', '15m')

        assert res.exits_taken > 0
        assert res.beats_hold is True
        assert res.edge_usd > 0

    def test_fade_loses_to_hold_when_signal_is_noise_because_of_fees(self):
        """A signal firing at random points in a steady uptrend must NOT
        show fade edge: every exit/re-entry pays round-trip fees for nothing.
        This is the fee-drag reality check that keeps inversion honest."""
        prices = [100.0 * (1.002 ** i) for i in range(260)]
        candles = _candles(prices)
        harness = VectorizedBacktestHarness(CONFIG)
        ind = precompute_indicators(candles)
        res = run_fade_test(harness, SignalAtBars(range(110, 250, 7)), ind, 'X', '15m')

        assert res.exits_taken > 0
        assert res.beats_hold is False
        assert res.edge_usd < 0  # pure cost, no edge

    def test_no_signals_matches_buy_hold(self):
        prices = [100.0 + i * 0.1 for i in range(260)]
        harness = VectorizedBacktestHarness(CONFIG)
        ind = precompute_indicators(_candles(prices))
        res = run_fade_test(harness, SignalAtBars([]), ind, 'X', '15m')
        assert res.exits_taken == 0
        assert res.fade_pnl_usd == pytest.approx(res.buy_hold_pnl_usd, abs=1e-9)


def test_benchmark_not_eligible_for_inversion():
    """Fading a signal-less benchmark tests nothing (dca_7 appeared in the
    first inversion run before this gate existed)."""
    entry = {'inversion_flagged': True, 'trades': 100, 'gross_pf': 0.4,
             'is_benchmark': True}
    assert 'benchmark' in is_inversion_eligible(entry)
