"""Tests for the Lab v5 P3 dispersion gate (backtest/dispersion_gate.py).

Everything here is fast and synthetic. The pins:
  1. Gate arithmetic: ATR_hold >= c/kappa with KNOWN numbers, per asset
     class - the per-class refinement over the v5 doc's flat 14bps is the
     load-bearing change, so each class's threshold is pinned explicitly.
  2. The gate uses only pre-entry data (mutating future bars cannot change
     a past gate decision).
  3. Decile bucketing correctness on synthetic series.
  4. Holdout split determinism.
"""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.cost_model import CostModel
from backtest.dispersion_gate import (KAPPA, EXIT_HOLDS, atr_hold_frac,
                                      calendar_midpoint_ts,
                                      expanding_vol_deciles, gate_mask,
                                      gate_threshold_frac, mask_signals,
                                      series_cost_fraction, trade_half,
                                      underlying)


@pytest.fixture
def cm():
    return CostModel()


# ==========================================================================
# 1. GATE ARITHMETIC, PER CLASS - known numbers
# ==========================================================================

class TestPerClassThresholds:
    """c comes from the venue-accurate model, NOT the v5 doc's flat 14bps.
    These pins are hand-computed from the rates in backtest/cost_model.py."""

    def test_crypto_noncore_threshold_is_the_v5_docs_number(self, cm):
        """SOL/USDT: 2bps fees + 10bps slippage = 14bps round trip. c/kappa
        = 1.4% - the ONE case where the doc's flat assumption was right."""
        coster = cm.coster('SOL/USDT', 'CRYPTO', 100.0, notional_cap=100.0)
        c = series_cost_fraction(coster)
        assert c * 10_000 == pytest.approx(14.0, abs=0.01)
        assert gate_threshold_frac(c) == pytest.approx(0.014, abs=1e-5)

    def test_crypto_core_threshold_is_sharper(self, cm):
        """BTC/USDT is a core pair: 0.01% taker -> 12bps RT -> 1.2% gate."""
        coster = cm.coster('BTC/USDT', 'CRYPTO', 100.0, notional_cap=100.0)
        c = series_cost_fraction(coster)
        assert c * 10_000 == pytest.approx(12.0, abs=0.01)
        assert gate_threshold_frac(c) == pytest.approx(0.012, abs=1e-5)

    def test_equity_threshold_is_far_below_the_docs_14bps(self, cm):
        """Equity on a $100 clip: 2 x 1.5bp half-spread + 0.206bp SEC +
        1bp TAF-minimum ~ 4.2bps -> ~0.42% gate, ~3.3x sharper than 1.4%.
        This is the refinement the work order says to document."""
        coster = cm.coster('AAPL', 'EQUITY', 200.0, notional_cap=100.0)
        c = series_cost_fraction(coster)
        assert c * 10_000 == pytest.approx(4.206, abs=0.05)
        thr = gate_threshold_frac(c)
        assert 0.004 < thr < 0.0045
        assert thr < 0.014 / 3          # sharper by more than 3x

    def test_futures_threshold_is_sub_two_bps_of_exposure(self, cm):
        """One MES at ~6800: $2.06 fees + 2 ticks slip on ~$34k exposure,
        ~1.3bps -> ~0.13% gate (c/kappa = 1.34bp / 0.10). Nearly always
        open; the toll is not the binding constraint for futures (their
        constraint is blowup risk, ROADMAP P0.4).

        notional_cap=2000 (not the harness's $100 default, per D-249/
        ROADMAP P0.4): series_cost_fraction is a toll-RATE, which for a
        flat-fee-per-contract instrument is only defined once at least one
        contract is affordable - at $100 MES's $1,800 margin is not
        affordable, qty is honestly 0, and the rate is undefined (inf),
        which is the correct answer to "what's my cost rate on a position
        I cannot open," not a bug."""
        coster = cm.coster('ES_F', 'FUTURES', 6800.0, notional_cap=2000.0)
        c = series_cost_fraction(coster)
        assert c * 10_000 < 2.0
        assert gate_threshold_frac(c) < 0.002

    def test_futures_threshold_is_undefined_when_unaffordable(self, cm):
        """At the harness's $100 default, MES is not affordable (D-249) -
        the toll-rate is honestly inf, not the fictional ~0.13% a hardcoded
        1-contract floor used to produce. Downstream this closes the
        dispersion gate for futures at $100, which agrees with reality:
        the harness's own qty<=0 skip already refuses that trade regardless
        of what the gate says."""
        coster = cm.coster('ES_F', 'FUTURES', 6800.0, notional_cap=100.0)
        assert series_cost_fraction(coster) == float('inf')

    def test_gate_boundary_is_greater_or_equal(self, cm):
        """ATR_hold exactly at c/kappa passes; a hair below fails."""
        coster = cm.coster('SOL/USDT', 'CRYPTO', 100.0, notional_cap=100.0)
        thr = gate_threshold_frac(series_cost_fraction(coster))   # 0.0014/0.1
        hold = EXIT_HOLDS['time_8c']
        closes = np.array([100.0, 100.0, 100.0])
        # ATR that puts ATR_hold exactly at / just under the threshold.
        atr_at = np.full(3, thr * 100.0 / math.sqrt(hold))
        atr_under = atr_at * 0.999
        assert gate_mask(atr_at, closes, hold, thr).all()
        assert not gate_mask(atr_under, closes, hold, thr).any()

    def test_atr_hold_scales_with_sqrt_of_hold(self):
        """v5 SS2: ATR_hold ~ ATR_bar x sqrt(bars held). 16 bars = 2x the
        per-hold ATR of 4 bars, so a longer hold clears a gate a short one
        fails - the entire Toll Law mechanism in one assertion."""
        atr = np.array([1.0])
        closes = np.array([100.0])
        f4 = atr_hold_frac(atr, closes, 4)[0]
        f16 = atr_hold_frac(atr, closes, 16)[0]
        assert f16 == pytest.approx(2.0 * f4)
        assert f4 == pytest.approx(0.02)     # 1/100 * sqrt(4)

    def test_undefined_atr_gates_out(self):
        """NaN/zero ATR or nonpositive price must gate OUT, never in."""
        atr = np.array([np.nan, 0.0, 1.0])
        closes = np.array([100.0, 100.0, 0.0])
        assert not gate_mask(atr, closes, 8, 0.001).any()


# ==========================================================================
# 2. GATE USES ONLY PRE-ENTRY DATA
# ==========================================================================

class TestNoLookahead:
    def test_future_bars_cannot_change_a_past_gate_decision(self):
        """Mutate everything after bar i; the mask at bars <= i must be
        bit-identical. The threshold is a constant from the fee schedule and
        ATR14 is trailing, so this holds by construction - this test keeps
        it holding through refactors."""
        rng = np.random.RandomState(7)
        n = 300
        closes = 100 + np.cumsum(rng.normal(0, 1, n))
        atr = np.abs(rng.normal(1.0, 0.3, n)) + 0.1
        thr = 0.014
        i = 150
        base = gate_mask(atr, closes, 8, thr)
        atr2, closes2 = atr.copy(), closes.copy()
        atr2[i + 1:] *= 100.0            # violent future vol change
        closes2[i + 1:] = 1.0
        mutated = gate_mask(atr2, closes2, 8, thr)
        assert (base[:i + 1] == mutated[:i + 1]).all()

    def test_decile_at_bar_i_ignores_future_bars(self):
        """Expanding percentile: deciles at bars <= i are unchanged when the
        future is rewritten."""
        rng = np.random.RandomState(11)
        n = 200
        closes = np.full(n, 100.0)
        atr = np.abs(rng.normal(1.0, 0.4, n)) + 0.05
        i = 120
        base = expanding_vol_deciles(atr, closes, start=0, min_history=10)
        atr2 = atr.copy()
        atr2[i + 1:] = 50.0              # future becomes extreme-vol
        mutated = expanding_vol_deciles(atr2, closes, start=0, min_history=10)
        assert (base[:i + 1] == mutated[:i + 1]).all()

    def test_mask_signals_nulls_gated_bars_and_only_those(self):
        sigs = ['a', None, 'b', 'c', None]
        mask = np.array([True, True, False, True, False])
        out = mask_signals(sigs, mask)
        assert out == ['a', None, None, 'c', None]


# ==========================================================================
# 3. DECILE BUCKETING ON SYNTHETIC SERIES
# ==========================================================================

class TestDecileBucketing:
    def test_rising_vol_is_always_top_decile(self):
        """Strictly increasing ATR/close: every new bar is its own history's
        maximum -> percentile 1.0 -> decile 9."""
        n = 100
        closes = np.full(n, 100.0)
        atr = np.linspace(0.1, 5.0, n)
        d = expanding_vol_deciles(atr, closes, start=0, min_history=10)
        assert (d[:9] == -1).all()            # warmup: below min_history
        assert (d[9:] == 9).all()

    def test_falling_vol_is_always_bottom_decile(self):
        """Strictly decreasing: every new bar is the minimum -> percentile
        1/len -> decile 0 (once history is large enough)."""
        n = 100
        closes = np.full(n, 100.0)
        atr = np.linspace(5.0, 0.1, n)
        d = expanding_vol_deciles(atr, closes, start=0, min_history=20)
        assert (d[20:] == 0).all()

    def test_hand_computed_percentiles(self):
        """min_history=1 exposes the raw arithmetic: percentile = rank of
        the current value (ties included, bisect_right) / history size."""
        closes = np.full(5, 100.0)
        atr = np.array([2.0, 1.0, 3.0, 2.0, 2.5])
        d = expanding_vol_deciles(atr, closes, start=0, min_history=1)
        # bar 0: hist [2]        pct 1/1 = 1.00 -> 9
        # bar 1: hist [1,2]      pct 1/2 = 0.50 -> 5
        # bar 2: hist [1,2,3]    pct 3/3 = 1.00 -> 9
        # bar 3: hist [1,2,2,3]  pct(2.0, bisect_right) 3/4 = 0.75 -> 7
        # bar 4: hist [1,2,2,2.5,3] pct 4/5 = 0.80 -> 8
        assert list(d) == [9, 5, 9, 7, 8]

    def test_nonfinite_and_nonpositive_bars_are_skipped(self):
        """Bad bars neither get a decile nor pollute the history."""
        closes = np.full(6, 100.0)
        atr = np.array([1.0, np.nan, 0.0, 2.0, -1.0, 3.0])
        d = expanding_vol_deciles(atr, closes, start=0, min_history=1)
        assert d[1] == -1 and d[2] == -1 and d[4] == -1
        # history is [1], [1,2], [1,2,3] at the good bars
        assert d[0] == 9 and d[3] == 9 and d[5] == 9


# ==========================================================================
# 4. HOLDOUT SPLIT DETERMINISM
# ==========================================================================

class TestHoldoutSplit:
    def test_midpoint_is_deterministic_and_calendar_based(self):
        ts = np.array([1_000, 2_000, 9_000], dtype=np.int64)
        # Calendar midpoint (1000+9000)/2 = 5000, NOT the middle bar's 2000.
        assert calendar_midpoint_ts(ts) == 5_000
        assert calendar_midpoint_ts(ts) == calendar_midpoint_ts(ts.copy())

    def test_half_assignment_boundary(self):
        mid = 5_000
        assert trade_half(4_999, mid) == 'H1'
        assert trade_half(5_000, mid) == 'H2'    # boundary bar is holdout
        assert trade_half(5_001, mid) == 'H2'

    def test_same_series_twice_gives_identical_split(self):
        rng = np.random.RandomState(3)
        ts = np.cumsum(rng.randint(1, 10, 500)).astype(np.int64)
        m1, m2 = calendar_midpoint_ts(ts), calendar_midpoint_ts(ts)
        assert m1 == m2
        halves1 = [trade_half(int(t), m1) for t in ts]
        halves2 = [trade_half(int(t), m2) for t in ts]
        assert halves1 == halves2
        assert 'H1' in halves1 and 'H2' in halves1


# ==========================================================================
# SUPPORTING PIECES
# ==========================================================================

class TestUnderlyingGrouping:
    def test_matches_asset_class_analysis_convention(self):
        """BTC/USDT and BTC_USD are ONE asset for leave-one-out."""
        assert underlying('BTC/USDT') == 'BTC'
        assert underlying('BTC_USD') == 'BTC'
        assert underlying('ES_F') == 'ES'
        assert underlying('AAPL') == 'AAPL'


class TestPreRegisteredConstants:
    def test_kappa_is_the_v5_docs_value(self):
        """kappa = 0.10 is pre-registered in the v5 doc. Changing it is a
        different experiment and needs a new pre-registration."""
        assert KAPPA == 0.10

    def test_exit_holds_are_exact_time_exits(self):
        """Time-based exits only, so bars_in_hold is exact by construction
        (the documented design call - no median-hold estimation)."""
        assert EXIT_HOLDS == {'time_4c': 4, 'time_8c': 8, 'time_16c': 16}
