"""Tests for the venue-accurate cost model.

The old harness charged one crypto percentage fee to 1,390,451 trades
including 905,124 equity trades on a commission-free venue. These tests pin
the four regimes so that cannot recur silently.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.cost_model import CostModel, COST_MODEL_VERSION


@pytest.fixture
def cm():
    return CostModel()


class TestRegimesAreStructurallyDifferent:
    def test_percentage_regime_is_size_invariant(self, cm):
        """Crypto: bps identical at any size. This is why bigger positions
        cannot outrun crypto fees."""
        small = cm.round_trip_bps('CRYPTO', 100, 'SOL/USDT')
        large = cm.round_trip_bps('CRYPTO', 1_000_000, 'SOL/USDT')
        assert small == pytest.approx(large, rel=1e-9)

    def test_fixed_regime_INVERTS_with_size(self, cm):
        """Options: bps FALL as premium rises. The opposite of crypto, and the
        reason cheap far-OTM contracts are punished twice."""
        cheap = cm.round_trip_bps('OPTIONS', 100, contracts=1)
        rich = cm.round_trip_bps('OPTIONS', 8000, contracts=1)
        assert cheap > rich * 10
        assert cheap > 100      # >1% of premium on a $100 contract
        assert rich < 5         # trivial on an $8k contract

    def test_futures_are_cheapest_per_exposure(self, cm):
        """Sub-basis-point per notional, an order of magnitude below equities.
        The Toll Law's friendliest terrain - offset by a huge minimum size."""
        fut = cm.round_trip_bps('FUTURES', 34_000, 'MES', contracts=1)
        eq = cm.round_trip_bps('EQUITY', 34_000, price=200, shares=170)
        assert fut < 1.0
        assert fut < eq

    def test_equity_far_cheaper_than_the_old_universal_model(self, cm):
        """The flaw this model fixes: equities were charged 30bps when the
        real cost is a few bps."""
        eq = cm.round_trip_bps('EQUITY', 100, price=200, shares=0.5)
        assert eq < 10
        assert eq < 30 / 3      # at least 3x overstated by the old model


class TestEquitySpecifics:
    def test_no_commission(self, cm):
        assert cm.equity_leg(100, 1, is_sell=False).commission == 0.0

    def test_regulatory_fees_are_sell_side_only(self, cm):
        buy = cm.equity_leg(100, 1, is_sell=False)
        sell = cm.equity_leg(100, 1, is_sell=True)
        assert buy.regulatory == 0.0
        assert sell.regulatory > 0.0

    def test_sec_fee_is_time_varying(self, cm):
        """It was $0 from May 2025 to 2026-04-04. A backtest spanning that
        window must not charge it retroactively."""
        before = cm.equity_leg(10_000, 50, is_sell=True, ts_ms=1740000000000)
        after = cm.equity_leg(10_000, 50, is_sell=True, ts_ms=1790000000000)
        assert after.regulatory > before.regulatory

    def test_taf_has_a_floor(self, cm):
        """Tiny share counts still pay the $0.01 minimum."""
        cb = cm.equity_leg(1.0, 0.01, is_sell=True, ts_ms=1790000000000)
        assert cb.regulatory >= 0.01


class TestCryptoSpecifics:
    def test_core_pairs_are_cheaper(self, cm):
        assert (cm.round_trip_bps('CRYPTO', 100, 'BTC/USD')
                < cm.round_trip_bps('CRYPTO', 100, 'SOL/USDT'))

    def test_maker_pays_no_fee_and_no_slippage(self, cm):
        """Maker legs pay adverse selection instead, which must be MEASURED,
        not assumed - so the model reports zero explicit cost here."""
        maker = cm.crypto_leg(100, 'BTC/USD', maker=True)
        taker = cm.crypto_leg(100, 'BTC/USD', maker=False)
        assert maker.total == 0.0
        assert taker.total > 0.0


class TestVersioning:
    def test_version_is_stamped(self, cm):
        assert cm.VERSION == COST_MODEL_VERSION
        assert cm.describe()['version'] == COST_MODEL_VERSION

    def test_describe_flags_crypto_as_unverified(self, cm):
        """The Binance.US rate change is reported by reviewers but has not
        been confirmed against a live account."""
        assert cm.describe()['crypto']['verified'] is False

    def test_unknown_asset_class_raises(self, cm):
        with pytest.raises(ValueError):
            cm.round_trip_bps('CURRENCY', 100)


# ---------------------------------------------------------------------------
# HARNESS WIRING (2026-08-13): the model above is only real if the harness
# actually charges it. These pin the coster path end to end.
# ---------------------------------------------------------------------------

def _bars(n=300, px=100.0, seed=7):
    import random
    rng = random.Random(seed)
    out, p = [], px
    for i in range(n):
        p *= 1 + rng.gauss(0.0005, 0.01)
        out.append({'ts': 1700000000000 + i * 86400000,
                    'open': p * 0.998, 'high': p * 1.012,
                    'low': p * 0.988, 'close': p, 'volume': 1000.0})
    return out


def _run(ticker, sector, use_model, bars=None, notional_cap=100.0):
    from backtest.vectorized_harness import (VectorizedBacktestHarness,
                                             precompute_indicators)
    from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
    h = VectorizedBacktestHarness({
        'strategy': {'confirmation': {'apply_confirmation_stack': False}},
        'use_cost_model': use_model,
        'risk': {'notional_cap_usd': notional_cap},
    })
    ind = precompute_indicators(bars or _bars())
    for strat in ENTRY_STRATEGIES_EXPANDED:
        r = h.run_strategy(strat, ind, ticker, '1d', 'fixed_2r', sector=sector)
        if r.trade_count:
            return r
    return None


def test_harness_flat_mode_is_default_and_stamped():
    r = _run('AAPL', None, use_model=False)
    assert r is not None, 'fixture produced no trades'
    assert r.cost_model_version.startswith('flat:')
    assert r.asset_class == 'FLAT'


def test_harness_modeled_equity_cheaper_than_flat():
    """The 7x equity overstatement, pinned: same trades, ~10x lower cost."""
    r_flat = _run('AAPL', 'Tech', use_model=False)
    r_model = _run('AAPL', 'Tech', use_model=True)
    assert r_model.asset_class == 'EQUITY'
    assert r_model.cost_model_version == CostModel.VERSION
    assert r_model.trade_count == r_flat.trade_count
    fees_flat = sum(t.fee_cost for t in r_flat.trades)
    fees_model = sum(t.fee_cost for t in r_model.trades)
    assert fees_model < fees_flat / 3, (
        f'modeled equity fees {fees_model} not clearly below flat {fees_flat}')


def test_harness_futures_trade_one_contract_on_margin():
    """At capital that actually affords the margin (ROADMAP P0.4's own
    $2,000 example: MES needs $1,800), one whole contract, margin as
    capital-at-risk, PnL in contract dollars."""
    bars = _bars(px=6800.0, seed=11)
    r = _run('ES_F', 'Futures', use_model=True, bars=bars, notional_cap=2000.0)
    assert r is not None, 'fixture produced no trades'
    assert r.asset_class == 'FUTURES'
    assert r.instrument == 'MES'          # standard remaps to reachable micro
    for t in r.trades:
        assert t.qty == 1.0               # whole contracts, not notional/price
        assert t.capital_at_risk == 1800.0  # margin, not notional
    # PnL must be in contract dollars: price move x multiplier 5.
    t = r.trades[0]
    assert abs(t.pnl_gross - (t.exit_px - t.entry_px) * 5.0) < 1e-6


def test_harness_futures_unaffordable_produces_no_fictional_trades():
    """THE BUG THIS FIXES: at the harness default $100 notional_cap, MES
    needs $1,800 margin - not affordable at any price. Before D-24x this
    silently traded 1 contract anyway (all 79,642 v0 futures rows were
    fictional, ROADMAP P0.4). Now size() honestly returns 0 and the strategy
    loop skips the trade like any other unaffordable signal."""
    bars = _bars(px=6800.0, seed=11)
    r = _run('ES_F', 'Futures', use_model=True, bars=bars, notional_cap=100.0)
    assert r is None, 'a $100 account cannot afford one MES contract ($1,800 margin)'


def test_harness_override_forces_flat_zero_cost():
    """Validation probes say fees=0 and must get EXACTLY that in any regime."""
    from backtest.vectorized_harness import (VectorizedBacktestHarness,
                                             precompute_indicators)
    from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
    h = VectorizedBacktestHarness({
        'strategy': {'confirmation': {'apply_confirmation_stack': False}},
        'use_cost_model': True,
    })
    ind = precompute_indicators(_bars())
    for strat in ENTRY_STRATEGIES_EXPANDED:
        r = h.run_strategy(strat, ind, 'AAPL', '1d', 'fixed_2r', sector='Tech',
                           fee_override=0.0, slippage_override=0.0)
        if r.trade_count:
            assert all(t.fee_cost == 0.0 for t in r.trades)
            assert r.cost_model_version.startswith('flat:')
            return
    raise AssertionError('fixture produced no trades')
