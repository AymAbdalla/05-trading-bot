"""Tests for the options overlay: fixed per-contract commissions, contract
sizing, and the fee-drag behavior that makes cheap options dangerous."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.options_overlay import (run_option_overlay, realized_vol,
                                      BARS_PER_YEAR)
from backtest.vectorized_harness import precompute_indicators
from strategies.base import Strategy, Signal


class AlwaysLong(Strategy):
    name = 'always_long_stub'
    is_entry = True

    def scan(self, candles):
        px = candles['closes'][-1]
        return Signal(pair='', pattern=self.name, direction='bullish',
                      confidence=0.5, features={}, entry=px, stop=px * 0.95,
                      target=px * 1.10)


def _rising(n=400, start=100.0, step=0.3):
    """Rising WITH realistic daily noise: a perfectly smooth series has zero
    realized volatility, which prices every option at zero and produces no
    trades at all."""
    import random
    rng = random.Random(11)
    out, px = [], start
    for i in range(n):
        px = max(5.0, px * (1 + 0.0015 + rng.gauss(0, 0.018)))
        out.append({'ts': 1700000000000 + i * 86400000, 'open': px * 0.998,
                    'high': px * 1.012, 'low': px * 0.988, 'close': px,
                    'volume': 1000.0})
    return out


@pytest.fixture
def ind():
    return precompute_indicators(_rising())


def _signals(ind, n):
    stub = AlwaysLong()
    sigs = [None] * n
    for i in range(100, n):
        sigs[i] = stub.scan({'closes': ind.closes[:i + 1].tolist()})
    return sigs


class TestCommissionModel:
    def test_commission_is_per_contract_not_per_trade(self, ind):
        """The defining property of options costs: doubling contracts doubles
        commission. (Percentage fees on equities/crypto do NOT behave this
        way relative to position size - see the position-size research note.)"""
        stub = AlwaysLong()
        sigs = _signals(ind, ind.n)
        small = run_option_overlay(stub, ind, 'X', '1d', signals=sigs,
                                   budget_usd=1_000.0, commission_per_contract=0.65)
        large = run_option_overlay(stub, ind, 'X', '1d', signals=sigs,
                                   budget_usd=10_000.0, commission_per_contract=0.65)
        assert small.trades and large.trades
        ratio_contracts = (sum(t.contracts for t in large.trades)
                           / sum(t.contracts for t in small.trades))
        ratio_commission = large.total_commission / small.total_commission
        assert ratio_commission == pytest.approx(ratio_contracts, rel=0.01)

    def test_zero_commission_removes_all_commission_cost(self, ind):
        stub = AlwaysLong()
        sigs = _signals(ind, ind.n)
        free = run_option_overlay(stub, ind, 'X', '1d', signals=sigs,
                                  commission_per_contract=0.0)
        assert free.total_commission == 0.0

    def test_cheaper_options_suffer_worse_fee_drag(self, ind):
        """THE finding: further OTM means cheaper premium means MORE contracts
        per dollar AND a bigger fee-to-premium ratio. Fixed per-contract costs
        punish cheap options twice."""
        stub = AlwaysLong()
        sigs = _signals(ind, ind.n)
        near = run_option_overlay(stub, ind, 'X', '1d', signals=sigs, otm_pct=0.02)
        far = run_option_overlay(stub, ind, 'X', '1d', signals=sigs, otm_pct=0.15)
        if not (near.trades and far.trades):
            pytest.skip('one leg produced no affordable contracts')
        assert far.commission_pct_of_premium > near.commission_pct_of_premium

    def test_order_minimum_applies(self, ind):
        stub = AlwaysLong()
        sigs = _signals(ind, ind.n)
        r = run_option_overlay(stub, ind, 'X', '1d', signals=sigs,
                               commission_per_contract=0.01, order_minimum=5.0)
        assert all(t.commission == pytest.approx(10.0) for t in r.trades)


class TestVolAnnualization:
    def test_bars_per_year_differs_by_timeframe(self):
        """synthetic_options hardcodes sqrt(252), correct ONLY for daily.
        The overlay must annualize by the series timeframe."""
        assert BARS_PER_YEAR['1d'] == 252
        assert BARS_PER_YEAR['1wk'] == 52
        assert BARS_PER_YEAR['15m'] > BARS_PER_YEAR['1h'] > BARS_PER_YEAR['1d']

    def test_realized_vol_scales_with_timeframe(self):
        closes = [100.0 * (1.001 if i % 2 else 0.999) ** 1 for i in range(200)]
        import numpy as np
        arr = np.array(closes)
        daily = realized_vol(arr, 199, 60, BARS_PER_YEAR['1d'])
        intraday = realized_vol(arr, 199, 60, BARS_PER_YEAR['15m'])
        assert intraday > daily  # same returns, more bars per year


def test_unaffordable_premium_skips_signal(ind):
    """If one contract costs more than the budget, no trade happens - never a
    fractional contract (options are not divisible)."""
    stub = AlwaysLong()
    sigs = _signals(ind, ind.n)
    r = run_option_overlay(stub, ind, 'X', '1d', signals=sigs,
                           budget_usd=1.0, otm_pct=0.01)
    assert all(t.contracts >= 1 for t in r.trades)


def test_spec_100_dollar_notional_cannot_buy_most_contracts(ind):
    """Real constraint, not a bug: SPEC 6.1 caps notional at $100, but one
    option contract on a $200 stock commonly costs $200-500 in premium. At
    $100 the overlay buys ZERO contracts and skips every signal. Options
    require their own sizing rule, not the equity notional cap."""
    stub = AlwaysLong()
    sigs = _signals(ind, ind.n)
    at_100 = run_option_overlay(stub, ind, 'X', '1d', signals=sigs, budget_usd=100.0)
    at_2000 = run_option_overlay(stub, ind, 'X', '1d', signals=sigs, budget_usd=2000.0)
    assert at_100.trade_count == 0
    assert at_2000.trade_count > 0


def test_small_budget_changes_the_TRADE_POPULATION_not_the_skill(ind):
    """TRAP PINNED: a small options budget can post a BETTER profit factor
    than a large one, and it is not skill. It silently declines the trades
    whose contracts it cannot afford, so it measures a different (smaller,
    cheaper-premium) population. On AAPL the $500 budget skipped 4 trades of
    which 3 were losers, inflating its PF from 1.45 to 1.93.

    Anyone comparing PF across account sizes must first confirm the trade
    counts match."""
    stub = AlwaysLong()
    sigs = _signals(ind, ind.n)
    small = run_option_overlay(stub, ind, 'X', '1d', signals=sigs, budget_usd=250)
    large = run_option_overlay(stub, ind, 'X', '1d', signals=sigs, budget_usd=25_000)
    assert small.trade_count < large.trade_count, (
        'expected the small budget to be unable to afford some contracts')
    small_idx = {t.entry_idx for t in small.trades}
    large_idx = {t.entry_idx for t in large.trades}

    # NOT a subset. Declining one trade frees the scanner to enter a LATER
    # trade the funded account was still holding through, so the sequences
    # diverge entirely rather than one being a subset of the other. Two
    # budgets on identical signals trade genuinely different populations.
    assert small_idx != large_idx
    assert small_idx - large_idx, (
        'small budget should pick up trades the large one was in a position for')

    # The trades the small budget could not afford are the EXPENSIVE ones.
    missed = [t for t in large.trades if t.entry_idx not in small_idx]
    assert missed
    avg_missed = sum(t.premium_in for t in missed) / len(missed)
    avg_small = sum(t.premium_in for t in small.trades) / len(small.trades)
    assert avg_missed > avg_small
