"""Tests for the backtest harness (T7).

Tests:
1. Chronological split (60/20/20)
2. Trade execution: a stub strategy drives REAL trades through
   run_strategy_on_candles (the old synthetic fixture produced zero trades,
   so the execution loops were never exercised)
3. PnL and fee computation on an actual harness-produced trade
4. Buy-and-hold benchmark (dollar comparison)
5. Random-entry twin (median over internal seeds, deterministic)
6. Profit factor, win rate, expectancy, max drawdown (account-equity based)
7. Go/no-go checkpoint: pass, fail, and infinite-PF scenarios
8. Regressions: regime timestamp alignment, gap-through stops, buy-stop
   entries, stop-vs-target slippage
"""
import os
import sys
import random
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.harness import BacktestHarness, BacktestResult, Trade
from backtest.cost_model import FlatCoster
from backtest.report import generate_report, generate_full_report
from strategies.base import Strategy, Signal
from strategies.builtin.patterns import ENTRY_STRATEGIES

def _flat_coster():
    """The legacy flat cost regime the twin tests were written against."""
    return FlatCoster(0.001, 0.0005, notional_cap=100)


@pytest.fixture
def config():
    return {
        'exchange': {
            'name': 'binanceus',
            'fees': {'maker': 0.001, 'taker': 0.001},
            'slippage': {'market': 0.0005, 'limit': 0.0},
        },
        'risk': {
            'notional_cap_usd': 100,
            'fee_to_edge_max': 0.15,
            'max_trades_per_day': 1,
        },
        'strategy': {
            'confirmation': {
                'regime_ema_period': 50,
                'regime_lookback': 10,
                'rsi_period': 14,
                'rsi_max_entry': 60,
                'rsi_reversal_boost': 45,
                'volume_sma_period': 20,
                'volume_min_ratio': 1.5,
                'support_lookback': 100,
                'support_min_touches': 2,
                'support_cluster_atr_mult': 0.5,
                'location_atr_mult': 1.5,
                'spread_max': 0.001,
            }
        }
    }


@pytest.fixture
def harness(config):
    return BacktestHarness(config)


FEE = 0.001
SLIP = 0.0005
TS0 = 1700000000000
M15 = 900000
H1 = 3600000

# With support_lookback=100 the harness starts scanning at bar index 110.
TRIGGER_IDX = 110


def make_synthetic_candles(n: int = 500, base_price: float = 50000,
                           trend: float = 50, volatility: float = 100,
                           seed: int = 42) -> list:
    """Generate noisy synthetic candle data (smoke tests only - this fixture
    is verified to produce ZERO trades for the builtin strategies, so any
    trade-level assertion must use the stub fixture below instead)."""
    random.seed(seed)
    candles = []

    for i in range(n):
        noise = random.uniform(-volatility, volatility)
        close = base_price + i * trend + noise
        open_ = close - random.uniform(-volatility / 2, volatility / 2)
        high = max(close, open_) + random.uniform(10, 50)
        low = min(close, open_) - random.uniform(10, 50)

        vol = random.uniform(100, 200)
        if i > n - 50 and random.random() > 0.6:
            vol *= 2.0

        candles.append({
            'ts': TS0 + i * M15,
            'open': round(open_, 2),
            'high': round(high, 2),
            'low': round(low, 2),
            'close': round(close, 2),
            'volume': round(vol, 2),
        })

    return candles


@pytest.fixture
def candles():
    return make_synthetic_candles(500)


@pytest.fixture
def regime_candles():
    """1h candles (simpler, just closes needed)."""
    return make_synthetic_candles(200, base_price=50000, trend=80, volatility=50)


# ---------------------------------------------------------------------------
# Deterministic trade-producing fixture
# ---------------------------------------------------------------------------

class StubStrategy(Strategy):
    """Emits exactly one bullish signal when the scan window reaches
    trigger_len candles. Lets tests drive a known trade through the full
    run_strategy_on_candles pipeline (confirmation stack, entry resolution,
    exit simulation, fees)."""
    name = 'stub_strategy'
    is_entry = True

    def __init__(self, trigger_len: int, entry: float, stop: float,
                 target: float, valid_for: int = 1):
        self.trigger_len = trigger_len
        self.entry = entry
        self.stop = stop
        self.target = target
        self.valid_for = valid_for

    def scan(self, candles):
        if len(candles['closes']) != self.trigger_len:
            return None
        return Signal(
            pair='', pattern=self.name, direction='bullish', confidence=0.6,
            features={}, entry=self.entry, stop=self.stop, target=self.target,
            valid_for=self.valid_for,
        )


def make_flat_candles(n: int = 130, base: float = 100.0,
                      trigger_idx: int = TRIGGER_IDX) -> list:
    """Flat, alternating candles: RSI ~50 (passes rsi<=60), volume spike at
    the trigger bar (ratio ~2.7, passes >=1.5). Close at even indices is
    base+0.2, odd indices base-0.2, so close at the trigger bar (110) = 100.2.
    Mutate individual bars afterwards to script exits."""
    candles = []
    for i in range(n):
        close = base + (0.2 if i % 2 == 0 else -0.2)
        open_ = (base + (0.2 if (i - 1) % 2 == 0 else -0.2)) if i > 0 else base
        candles.append({
            'ts': TS0 + i * M15,
            'open': open_,
            'high': max(open_, close) + 0.1,
            'low': min(open_, close) - 0.1,
            'close': close,
            'volume': 300.0 if i == trigger_idx else 100.0,
        })
    return candles


def make_uptrend_regime(n: int = 200, hours_before_start: int = 100) -> list:
    """1h regime candles in a steady uptrend, starting well BEFORE the signal
    series so enough regime candles (>= 60) are closed by the trigger bar."""
    ts_start = TS0 - hours_before_start * H1
    out = []
    for i in range(n):
        c = 90.0 + i * 0.1
        out.append({'ts': ts_start + i * H1, 'open': c - 0.05, 'high': c + 0.1,
                    'low': c - 0.1, 'close': c, 'volume': 100.0})
    return out


class TestChronologicalSplit:
    def test_split_proportions(self, harness, candles):
        train, val, test = harness.split_chronological(candles)
        assert len(train) == 300  # 60% of 500
        assert len(val) == 100    # 20%
        assert len(test) == 100   # 20%

    def test_no_overlap(self, harness, candles):
        train, val, test = harness.split_chronological(candles)
        # Last train ts < first val ts < last val ts < first test ts
        assert train[-1]['ts'] < val[0]['ts']
        assert val[-1]['ts'] < test[0]['ts']

    def test_chronological_order(self, harness, candles):
        train, val, test = harness.split_chronological(candles)
        # All timestamps increasing
        for series in [train, val, test]:
            for i in range(1, len(series)):
                assert series[i]['ts'] > series[i - 1]['ts']


class TestTradeExecution:
    def test_strategy_runs_without_crash(self, harness, candles, regime_candles):
        """Builtin strategy should run on synthetic data without errors."""
        strategy = ENTRY_STRATEGIES[0]  # bullish_engulfing
        result = harness.run_strategy_on_candles(
            strategy, candles, regime_candles, 'BTC/USDT'
        )
        assert result is not None
        assert result.pair == 'BTC/USDT'
        assert result.strategy_id == strategy.name

    def test_buy_hold_computed(self, harness, candles, regime_candles):
        """Buy-and-hold benchmark should be computed (both % and $)."""
        strategy = ENTRY_STRATEGIES[0]
        result = harness.run_strategy_on_candles(
            strategy, candles, regime_candles, 'BTC/USDT'
        )
        assert result.buy_hold_return != 0.0
        assert result.buy_hold_pnl_usd != 0.0

    def test_market_entry_target_exit_arithmetic(self, harness):
        """A stub signal must produce exactly one REAL trade with verifiable
        entry/exit/fee/PnL arithmetic. This is the execution-loop test the old
        zero-trade fixture never ran."""
        cands = make_flat_candles()
        # Bar after the trigger hits the target without touching the stop
        cands[111].update({'open': 100.0, 'high': 103.0, 'low': 99.0, 'close': 102.5})

        close_trigger = cands[TRIGGER_IDX]['close']  # 100.2
        strategy = StubStrategy(trigger_len=TRIGGER_IDX + 1,
                                entry=close_trigger, stop=97.0, target=102.2)
        result = harness.run_strategy_on_candles(
            strategy, cands, make_uptrend_regime(), 'BTC/USDT'
        )

        assert result.trade_count == 1
        t = result.trades[0]

        entry_px = close_trigger * (1 + SLIP)  # market fill at close + slippage
        exit_px = 102.2                        # target: resting limit, NO slippage
        qty = 100 / entry_px                   # notional_cap / entry
        fees = entry_px * qty * FEE + exit_px * qty * FEE
        pnl_gross = (exit_px - entry_px) * qty

        assert t.exit_reason == 'target'
        assert t.entry_px == pytest.approx(entry_px, rel=1e-9)
        assert t.exit_px == pytest.approx(exit_px, rel=1e-9)
        assert t.qty == pytest.approx(qty, rel=1e-9)
        assert t.fee_cost == pytest.approx(fees, rel=1e-9)
        assert t.pnl_gross == pytest.approx(pnl_gross, rel=1e-9)
        assert t.pnl_net == pytest.approx(pnl_gross - fees, rel=1e-9)
        # R-multiple: pnl_net / (entry - stop) * qty
        assert t.r_multiple == pytest.approx(
            t.pnl_net / ((entry_px - 97.0) * qty), rel=1e-9)
        # Entry/exit timestamps are candle timestamps, not wall clock
        assert t.entry_ts == cands[TRIGGER_IDX]['ts']
        assert t.exit_ts == cands[111]['ts']
        assert t.regime == 'uptrend'


class TestMetrics:
    def test_profit_factor_calculation(self):
        trades = [
            Trade(1, 2, 'BTC', 'test', 100, 102, 99, 104, 1, 2, 0.2, 1.8, 1.8, 'target', 'uptrend'),
            Trade(3, 4, 'BTC', 'test', 100, 98, 99, 104, 1, -2, 0.2, -2.2, -2.2, 'stop', 'uptrend'),
        ]
        result = BacktestResult('test', 'BTC', 1, 4, trades)
        # gross_profit = 1.8, gross_loss = 2.2, PF = 1.8/2.2 = 0.818
        assert result.profit_factor == pytest.approx(0.818, rel=0.01)

    def test_win_rate(self):
        trades = [
            Trade(1, 2, 'BTC', 'test', 100, 102, 99, 104, 1, 2, 0.2, 1.8, 1.8, 'target', 'uptrend'),
            Trade(3, 4, 'BTC', 'test', 100, 98, 99, 104, 1, -2, 0.2, -2.2, -2.2, 'stop', 'uptrend'),
            Trade(5, 6, 'BTC', 'test', 100, 103, 99, 104, 1, 3, 0.2, 2.8, 2.8, 'target', 'uptrend'),
        ]
        result = BacktestResult('test', 'BTC', 1, 6, trades)
        assert result.win_rate == pytest.approx(2 / 3, rel=0.01)

    def test_expectancy(self):
        trades = [
            Trade(1, 2, 'BTC', 'test', 100, 102, 99, 104, 1, 2, 0.2, 1.8, 1.8, 'target', 'uptrend'),
            Trade(3, 4, 'BTC', 'test', 100, 98, 99, 104, 1, -2, 0.2, -2.2, -2.2, 'stop', 'uptrend'),
        ]
        result = BacktestResult('test', 'BTC', 1, 4, trades)
        # (1.8 + (-2.2)) / 2 = -0.2
        assert result.expectancy == pytest.approx(-0.2, rel=0.01)

    def test_empty_result_metrics(self):
        result = BacktestResult('test', 'BTC', 1, 100)
        assert result.trade_count == 0
        assert result.win_rate == 0.0
        assert result.profit_factor == 0.0
        assert result.expectancy == 0.0
        assert result.max_drawdown == 0.0

    def test_max_drawdown_is_pct_of_account_equity(self):
        """Drawdown is % of peak ACCOUNT equity (starting_capital + cum PnL),
        not of cumulative trade PnL. A small dip after small gains must report
        a SMALL percentage (the old formula reported ~96% here)."""
        trades = [
            Trade(1, 2, 'BTC', 't', 100, 102, 99, 104, 1, 2, 0.2, 1.8, 1.8, 'target', 'up'),
            Trade(3, 4, 'BTC', 't', 100, 103, 99, 104, 1, 3, 0.2, 2.8, 2.8, 'target', 'up'),
            Trade(5, 6, 'BTC', 't', 100, 97, 99, 104, 1, -3, 0.2, -3.2, -3.2, 'stop', 'up'),
            Trade(7, 8, 'BTC', 't', 100, 99, 99, 104, 1, -1, 0.2, -1.2, -1.2, 'stop', 'up'),
        ]
        result = BacktestResult('test', 'BTC', 1, 8, trades)  # capital 2000
        # equity: 2001.8, 2004.6, 2001.4, 2000.2; peak 2004.6, trough 2000.2
        # dd = 4.4 / 2004.6 * 100 = 0.2195%
        assert result.max_drawdown == pytest.approx(4.4 / 2004.6 * 100, rel=1e-6)
        assert result.max_drawdown < 1.0  # NOT the old ~96%

    def test_max_drawdown_pure_losing_streak(self):
        """Regression: a pure losing streak MUST report > 0 drawdown.
        Trades of [-50, -50] on 2000 starting capital => 100/2000 = 5.0%.
        The old formula (peak on cumulative PnL, peak>0 guard) reported 0%."""
        trades = [
            Trade(1, 2, 'BTC', 't', 100, 50, 99, 104, 1, -50, 0.2, -50.0, -1.0, 'stop', 'up'),
            Trade(3, 4, 'BTC', 't', 100, 50, 99, 104, 1, -50, 0.2, -50.0, -1.0, 'stop', 'up'),
        ]
        result = BacktestResult('test', 'BTC', 1, 4, trades)  # capital 2000
        assert result.max_drawdown == pytest.approx(5.0, rel=1e-6)
        assert result.max_drawdown > 0

    def test_beats_buy_hold_dollar_comparison(self):
        """beats_buy_hold is total_pnl > buy_hold_pnl_usd (dollars, not %)."""
        trades = [
            Trade(1, 2, 'BTC', 't', 100, 110, 99, 104, 1, 10, 0.2, 9.8, 9.8, 'target', 'up'),
        ]
        result = BacktestResult('t', 'BTC', 1, 2, trades, buy_hold_pnl_usd=5.0)
        assert result.total_pnl == pytest.approx(9.8)
        assert result.beats_buy_hold()

    def test_does_not_beat_buy_hold(self):
        trades = [
            Trade(1, 2, 'BTC', 't', 100, 103, 99, 104, 1, 3, 0.2, 2.8, 2.8, 'target', 'up'),
        ]
        result = BacktestResult('t', 'BTC', 1, 2, trades, buy_hold_pnl_usd=10.0)
        # $2.8 strategy PnL < $10 buy-and-hold PnL
        assert not result.beats_buy_hold()

    def test_beats_buy_hold_in_down_market(self):
        """Dollar semantics: losing less than buy-and-hold still 'beats' it.
        (The old %-vs-% comparison got this backwards in up markets.)"""
        trades = [
            Trade(1, 2, 'BTC', 't', 100, 99, 99, 104, 1, -1, 0.2, -1.2, -0.5, 'stop', 'up'),
        ]
        result = BacktestResult('t', 'BTC', 1, 2, trades, buy_hold_pnl_usd=-5.0)
        assert result.beats_buy_hold()


class TestRandomTwin:
    def test_random_twin_runs(self, harness, candles):
        """Smoke: twin produces a PF on the uptrend fixture. (inf is correct
        there: the fixture never dips 2%, so no stop can hit.)"""
        pf = harness._run_random_twin(candles, 'BTC/USDT', _flat_coster(), num_entries=10)
        assert pf >= 0.0

    def test_random_twin_loses_to_fees_on_flat_data(self, harness):
        """On DEAD FLAT data every twin trade times out at entry price and
        loses exactly the fees+slippage: PF must be 0.0. If fees were not
        applied, PF would be ~1.0/undefined. This is the fee-drag assertion
        the old tautological test pretended to be."""
        flat = [{'ts': TS0 + i * M15, 'open': 100.0, 'high': 100.05,
                 'low': 99.95, 'close': 100.0, 'volume': 100.0}
                for i in range(400)]
        pf = harness._run_random_twin(flat, 'X', _flat_coster(), num_entries=20)
        assert pf == 0.0

    def test_random_twin_reproducible(self, harness, candles):
        """Twin uses its own internal seeds (median over 10 draws), so
        repeated calls are deterministic."""
        pf1 = harness._run_random_twin(candles, 'BTC/USDT', _flat_coster(), num_entries=10)
        pf2 = harness._run_random_twin(candles, 'BTC/USDT', _flat_coster(), num_entries=10)
        assert pf1 == pytest.approx(pf2, rel=0.01)


class TestGoNoGo:
    @staticmethod
    def _mini_harness():
        return BacktestHarness({
            'risk': {'notional_cap_usd': 100, 'fee_to_edge_max': 0.15, 'max_trades_per_day': 1},
            'exchange': {'fees': {'taker': 0.001}},
            'strategy': {'confirmation': {}}
        })

    def test_passing_strategy(self):
        """Strategy that meets all criteria should pass. It needs LOSING
        trades: an all-wins (infinite PF) result now fails by design."""
        wins = [
            Trade(i * 2, i * 2 + 1, 'BTC', 'test', 100, 103, 99, 104, 1,
                  3, 0.2, 3.0, 1.0, 'target', 'uptrend')
            for i in range(20)
        ]
        losses = [
            Trade(100 + i * 2, 101 + i * 2, 'BTC', 'test', 100, 98, 99, 104, 1,
                  -2, 0.2, -2.0, -0.7, 'stop', 'uptrend')
            for i in range(5)
        ]
        result = BacktestResult('test', 'BTC', 1, 200, wins + losses,
                                buy_hold_pnl_usd=10.0, random_twin_pf=0.5)
        # PF = 60/10 = 6.0 (finite), total_pnl 50 > BH $10, beats twin, 25 trades
        assert result.profit_factor == pytest.approx(6.0)

        verdicts = self._mini_harness().go_no_go({'test': result}, min_pf=1.15)
        assert verdicts['test']['pass'] is True

    def test_infinite_pf_fails(self):
        """Regression: zero losing trades => infinite PF => automatic FAIL
        with a reason mentioning 'infinite'. The old gate auto-passed it."""
        wins = [
            Trade(i * 2, i * 2 + 1, 'BTC', 'test', 100, 103, 99, 104, 1,
                  3, 0.2, 3.0, 1.0, 'target', 'uptrend')
            for i in range(25)
        ]
        result = BacktestResult('test', 'BTC', 1, 100, wins,
                                buy_hold_pnl_usd=2.0, random_twin_pf=0.5)
        assert result.profit_factor == float('inf')

        verdicts = self._mini_harness().go_no_go({'test': result}, min_pf=1.15)
        assert verdicts['test']['pass'] is False
        assert any('infinite' in r.lower() for r in verdicts['test']['reasons'])

    def test_failing_strategy_low_pf(self):
        """Strategy with PF < 1.15 should fail."""
        trades = [
            Trade(1, 2, 'BTC', 't', 100, 102, 99, 104, 1, 2, 0.2, 1.8, 1.8, 'target', 'up'),
            Trade(3, 4, 'BTC', 't', 100, 98, 99, 104, 1, -2, 0.2, -2.2, -2.2, 'stop', 'up'),
        ] * 12  # 24 trades, PF = 1.8/2.2 = 0.82
        result = BacktestResult('test', 'BTC', 1, 100, trades,
                                buy_hold_pnl_usd=5.0, random_twin_pf=0.5)
        verdicts = self._mini_harness().go_no_go({'test': result}, min_pf=1.15)
        assert verdicts['test']['pass'] is False
        assert any('PF' in r for r in verdicts['test']['reasons'])

    def test_failing_insufficient_trades(self):
        """Strategy with < 20 trades should fail."""
        trades = [
            Trade(1, 2, 'BTC', 't', 100, 110, 99, 104, 1, 10, 0.2, 9.8, 9.8, 'target', 'up'),
            Trade(3, 4, 'BTC', 't', 100, 98, 99, 104, 1, -2, 0.2, -2.2, -2.2, 'stop', 'up'),
        ] * 3  # only 6 trades (finite PF so the trade-count reason is isolated)
        result = BacktestResult('test', 'BTC', 1, 100, trades,
                                buy_hold_pnl_usd=2.0, random_twin_pf=0.5)
        verdicts = self._mini_harness().go_no_go({'test': result}, min_pf=1.15)
        assert verdicts['test']['pass'] is False
        assert any('trade_count' in r for r in verdicts['test']['reasons'])


class TestRegressions:
    """Each test here pins a specific audited-and-fixed bug. If the bug is
    reintroduced, the test fails."""

    def test_regime_counts_only_closed_candles(self):
        """Regression for the regime lookahead bug (audit 1.1): at each 15m
        bar, only 1h regime candles fully CLOSED by that bar's close may be
        counted. Old code sliced by bar index, handing out future data."""
        signal_candles = [{'ts': TS0 + i * M15} for i in range(8)]
        regime_candles = [{'ts': TS0 + i * H1} for i in range(3)]

        counts = BacktestHarness._regime_closed_counts(
            signal_candles, regime_candles, M15, H1)

        # decision time for bar i = TS0 + (i+1)*15m; regime candle j closes at
        # TS0 + (j+1)*1h. Counted iff (j+1)*4 <= i+1 in 15m units:
        # i=0..2 -> 0; i=3..6 -> 1; i=7 (decision TS0+2h) -> 2
        assert counts == [0, 0, 0, 1, 1, 1, 1, 2]

        # No count may include a regime candle whose close is after decision time
        for i, c in enumerate(counts):
            decision = signal_candles[i]['ts'] + M15
            for j in range(c):
                assert regime_candles[j]['ts'] + H1 <= decision

    def test_regime_counts_future_candles_never_counted(self):
        """Regime candles that only exist in the future (beyond every decision
        time) must contribute 0 - the old index-slicing counted them."""
        signal_candles = [{'ts': TS0 + i * M15} for i in range(8)]
        # Regime series starts 4h AFTER the signal series begins
        regime_candles = [{'ts': TS0 + 4 * H1 + i * H1} for i in range(3)]

        counts = BacktestHarness._regime_closed_counts(
            signal_candles, regime_candles, M15, H1)
        assert counts == [0] * 8

    def test_gap_through_stop_fills_at_open(self, harness):
        """Regression (audit sec 3, optimistic fills): if the bar after entry
        OPENS below the stop, the fill is the open (minus slippage), not the
        stop price."""
        cands = make_flat_candles()
        # Gap down through the stop: opens at 88, stop is 95
        cands[111].update({'open': 88.0, 'high': 89.0, 'low': 85.0, 'close': 88.5})

        close_trigger = cands[TRIGGER_IDX]['close']  # 100.2
        strategy = StubStrategy(trigger_len=TRIGGER_IDX + 1,
                                entry=close_trigger, stop=95.0, target=110.0)
        result = harness.run_strategy_on_candles(
            strategy, cands, make_uptrend_regime(), 'BTC/USDT')

        assert result.trade_count == 1
        t = result.trades[0]
        assert t.exit_reason == 'stop'
        assert t.exit_px == pytest.approx(88.0 * (1 - SLIP), rel=1e-9)
        assert abs(t.exit_px - 95.0) > 6.0  # nowhere near the stop price

    def test_stop_exit_pays_slippage_target_does_not(self, harness):
        """Regression: stop exits are market orders and pay slippage; target
        exits are resting limits and do not. (The old code exempted stops.)"""
        # Stop case (no gap: open above the stop, low pierces it)
        cands = make_flat_candles()
        cands[111].update({'open': 100.2, 'high': 100.3, 'low': 94.0, 'close': 95.0})
        close_trigger = cands[TRIGGER_IDX]['close']
        strategy = StubStrategy(trigger_len=TRIGGER_IDX + 1,
                                entry=close_trigger, stop=99.0, target=110.0)
        result = harness.run_strategy_on_candles(
            strategy, cands, make_uptrend_regime(), 'BTC/USDT')
        assert result.trade_count == 1
        t = result.trades[0]
        assert t.exit_reason == 'stop'
        assert t.exit_px == pytest.approx(99.0 * (1 - SLIP), rel=1e-9)
        assert t.exit_px != pytest.approx(99.0, rel=1e-7)  # slippage WAS applied

        # Target case: exact target price, zero slippage
        cands2 = make_flat_candles()
        cands2[111].update({'open': 100.0, 'high': 103.0, 'low': 99.0, 'close': 102.5})
        strategy2 = StubStrategy(trigger_len=TRIGGER_IDX + 1,
                                 entry=close_trigger, stop=97.0, target=102.2)
        result2 = harness.run_strategy_on_candles(
            strategy2, cands2, make_uptrend_regime(), 'BTC/USDT')
        assert result2.trade_count == 1
        t2 = result2.trades[0]
        assert t2.exit_reason == 'target'
        assert t2.exit_px == pytest.approx(102.2, abs=1e-9)  # no slippage

    def test_buy_stop_fills_only_on_touch(self, harness):
        """Regression (audit 1.3: signal.entry was ignored, everything filled
        at close): a buy-stop ABOVE the market only fills when a later bar's
        high touches the level, at max(level, open) + slippage."""
        cands = make_flat_candles()
        # Bar 111 touches the 103.0 level; bar 112 hits the target
        cands[111].update({'open': 100.0, 'high': 103.5, 'low': 99.5, 'close': 103.2})
        cands[112].update({'open': 103.0, 'high': 105.0, 'low': 102.0, 'close': 104.5})

        strategy = StubStrategy(trigger_len=TRIGGER_IDX + 1,
                                entry=103.0, stop=98.0, target=104.0, valid_for=2)
        result = harness.run_strategy_on_candles(
            strategy, cands, make_uptrend_regime(), 'BTC/USDT')

        assert result.trade_count == 1
        t = result.trades[0]
        # Filled on the TOUCH bar at the level (open was below it) + slippage
        assert t.entry_ts == cands[111]['ts']
        assert t.entry_px == pytest.approx(103.0 * (1 + SLIP), rel=1e-9)
        assert t.exit_reason == 'target'
        assert t.exit_px == pytest.approx(104.0, abs=1e-9)

    def test_buy_stop_expires_untouched(self, harness):
        """A buy-stop that no bar touches within valid_for candles expires:
        ZERO trades. (Old fill-at-close behavior would have traded anyway.)"""
        cands = make_flat_candles()  # all highs ~100.4, level 103 never touched
        strategy = StubStrategy(trigger_len=TRIGGER_IDX + 1,
                                entry=103.0, stop=98.0, target=104.0, valid_for=2)
        result = harness.run_strategy_on_candles(
            strategy, cands, make_uptrend_regime(), 'BTC/USDT')
        assert result.trade_count == 0


class TestWalkForward:
    def test_runs_multiple_windows(self, harness, candles, regime_candles):
        """Walk-forward should produce multiple results per strategy."""
        results = harness.run_walk_forward(candles, regime_candles, 'BTC/USDT',
                                           window_size=200, step_size=100)
        # 500 candles, window=200, step=100 => windows at 0, 100, 200 => 3 windows
        for name, wf_results in results.items():
            assert len(wf_results) >= 1


class TestStressProbes:
    def test_all_probes_run(self, harness, candles, regime_candles):
        """All 5 stress probes should run without crash."""
        strategy = ENTRY_STRATEGIES[0]
        probes = harness.run_stress_probes(strategy, candles, regime_candles, 'BTC/USDT')

        assert 'baseline' in probes
        assert 'fee_2x' in probes
        assert 'slippage_2x' in probes
        assert 'delay_1c' in probes
        assert 'param_jitter' in probes

    def test_fee_doubling_reduces_pnl(self, harness):
        """Higher fees must reduce PnL on a REAL trade (the old test compared
        0.0 to 0.0 because the fixture produced no trades)."""
        cands = make_flat_candles()
        cands[111].update({'open': 100.0, 'high': 103.0, 'low': 99.0, 'close': 102.5})
        close_trigger = cands[TRIGGER_IDX]['close']
        strategy = StubStrategy(trigger_len=TRIGGER_IDX + 1,
                                entry=close_trigger, stop=97.0, target=102.2)
        regime = make_uptrend_regime()

        baseline = harness.run_strategy_on_candles(
            strategy, cands, regime, 'BTC/USDT')
        fee_2x = harness.run_strategy_on_candles(
            strategy, cands, regime, 'BTC/USDT', fee_override=FEE * 2)

        assert baseline.trade_count == 1
        assert fee_2x.trade_count == 1
        assert fee_2x.total_pnl < baseline.total_pnl


class TestReport:
    def test_generate_report_text(self):
        """Report should be non-empty text."""
        trades = [
            Trade(1, 2, 'BTC', 'test', 100, 103, 99, 104, 1, 3, 0.2, 2.8, 2.8, 'target', 'uptrend'),
        ]
        results = {'test': BacktestResult('test', 'BTC', 1, 2, trades,
                                          buy_hold_return=2.0, random_twin_pf=0.5)}
        report = generate_report(results, 'BTC/USDT')
        assert len(report) > 0
        assert 'BTC' in report
        assert 'test' in report

    def test_full_report_generates(self):
        """Full report with all sections should generate."""
        trades = [
            Trade(1, 2, 'BTC', 'test', 100, 103, 99, 104, 1, 3, 0.2, 2.8, 2.8, 'target', 'uptrend'),
        ]
        test_results = {'test': BacktestResult('test', 'BTC', 1, 2, trades,
                                               buy_hold_return=2.0, random_twin_pf=0.5)}

        harness = BacktestHarness({
            'risk': {'notional_cap_usd': 100, 'fee_to_edge_max': 0.15, 'max_trades_per_day': 1},
            'exchange': {'fees': {'taker': 0.001}},
            'strategy': {'confirmation': {}}
        })
        go_no_go = harness.go_no_go(test_results)

        report = generate_full_report(
            test_results=test_results,
            walk_forward_results={},
            stress_results={},
            pair='BTC/USDT',
            go_no_go=go_no_go,
        )
        assert 'Go/No-Go' in report
        assert len(report) > 100


class TestScanCacheEquivalence:
    """The sweep's scan-once-replay-per-exit-config optimization must be
    invisible: cached and direct scanning produce identical results. Guards
    against any future stateful strategy silently corrupting sweeps."""

    def test_cached_signals_match_direct_scan(self):
        import yaml
        from backtest.data_loader import load_csv
        from backtest.vectorized_harness import (VectorizedBacktestHarness,
                                                 precompute_indicators)
        from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
        import os
        path = os.path.join(os.path.dirname(__file__), '..', 'backtest', 'data', 'AAPL_1d.csv')
        if not os.path.exists(path):
            pytest.skip('AAPL_1d.csv not present')
        config = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), '..', 'config.yaml')))
        h = VectorizedBacktestHarness(config)
        ind = precompute_indicators(load_csv(path))
        for strat in ENTRY_STRATEGIES_EXPANDED[:3]:
            cache = h.scan_all_bars(strat, ind)
            for cfg in ('fixed_2r', 'time_8c'):
                direct = h.run_strategy(strat, ind, 'AAPL', '1d', cfg)
                cached = h.run_strategy(strat, ind, 'AAPL', '1d', cfg,
                                        precomputed_signals=cache)
                assert direct.trade_count == cached.trade_count
                assert direct.total_pnl == pytest.approx(cached.total_pnl, abs=1e-9)


class TestSignalExitConfigs:
    """SPEC 5.1 #6: bearish patterns as EXIT triggers. The 14 exit strategies
    existed from day one and were never simulated - the harness dropped every
    non-bullish signal, so no exit-signal evidence existed anywhere."""

    def _setup(self):
        import yaml, os
        from backtest.data_loader import load_csv
        from backtest.vectorized_harness import (VectorizedBacktestHarness,
                                                 precompute_indicators, EXIT_CONFIGS)
        from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
        path = os.path.join(os.path.dirname(__file__), '..', 'backtest', 'data', 'AAPL_1h.csv')
        if not os.path.exists(path):
            pytest.skip('AAPL_1h.csv not present')
        cfg = yaml.safe_load(open(os.path.join(os.path.dirname(__file__), '..', 'config.yaml')))
        cfg['strategy']['confirmation']['apply_confirmation_stack'] = False
        h = VectorizedBacktestHarness(cfg)
        ind = precompute_indicators(load_csv(path))
        strat = next(s for s in ENTRY_STRATEGIES_EXPANDED if s.name == 'grid_1.0atr')
        return h, ind, strat

    def test_signal_exit_configs_registered(self):
        from backtest.vectorized_harness import EXIT_CONFIGS
        assert 'signal_exit' in EXIT_CONFIGS
        assert 'signal_exit_2r' in EXIT_CONFIGS
        assert EXIT_CONFIGS['signal_exit']['type'] == 'signal'

    def test_bearish_patterns_detected(self):
        h, ind, _ = self._setup()
        bars = h.exit_signal_bars(ind)
        assert bars.sum() > 0, 'no bearish exit pattern ever fired'
        assert bars.sum() < ind.n * 0.5, 'exit patterns firing on half of all bars is wrong'

    def test_signal_exits_actually_trigger_trades(self):
        h, ind, strat = self._setup()
        sigs = h.scan_all_bars(strat, ind)
        r = h.run_strategy(strat, ind, 'AAPL', '1h', 'signal_exit',
                           precomputed_signals=sigs)
        reasons = {t.exit_reason for t in r.trades}
        assert 'signal_exit' in reasons, 'signal exits registered but never fired'
        # Stop must still be honored alongside signal exits
        assert 'stop' in reasons

    def test_signal_exit_fills_at_next_bar_open(self):
        """A pattern is only known once its candle CLOSES, so the exit fills
        at the next bar's open (SPEC 5.1 #6 'close at next candle open'),
        never at the signal bar's own close."""
        h, ind, strat = self._setup()
        sigs = h.scan_all_bars(strat, ind)
        r = h.run_strategy(strat, ind, 'AAPL', '1h', 'signal_exit',
                           precomputed_signals=sigs)
        sig_exits = [t for t in r.trades if t.exit_reason == 'signal_exit']
        assert sig_exits
        for t in sig_exits[:25]:
            expected = float(ind.opens[t.exit_idx]) * (1 - h.slippage)
            assert t.exit_px == pytest.approx(expected, rel=1e-9)


class TestTimeMatchedTwin:
    """strategy-lab-v2 harness warning (silent assertion #15): a clock-anchored
    strategy compared against an untimed random twin credits the CLOCK, not the
    signal. The twin must draw entries from the same minutes of day."""

    def _ind(self, n=9000):
        from backtest.vectorized_harness import precompute_indicators
        c = [{'ts': 1700000000000 + i * 300000, 'open': 100 + i * 0.01,
              'high': 100.5 + i * 0.01, 'low': 99.5 + i * 0.01,
              'close': 100.2 + i * 0.01, 'volume': 100.0} for i in range(n)]
        return precompute_indicators(c)

    def test_clock_anchored_signals_produce_time_buckets(self):
        from backtest.vectorized_harness import VectorizedBacktestHarness
        h = VectorizedBacktestHarness({})
        ind = self._ind()
        target_min = (int(ind.timestamps[150]) // 60000) % 1440
        anchored = [i for i in range(150, ind.n)
                    if (int(ind.timestamps[i]) // 60000) % 1440 == target_min]
        buckets = h._time_bucket_key(ind, anchored)
        assert buckets == frozenset({target_min})

    def test_spread_signals_produce_no_buckets(self):
        """A strategy firing across the session is NOT clock-anchored, so the
        twin must stay unrestricted (restricting it would shrink the sample
        for no reason)."""
        from backtest.vectorized_harness import VectorizedBacktestHarness
        h = VectorizedBacktestHarness({})
        ind = self._ind()
        assert h._time_bucket_key(ind, list(range(150, ind.n, 7))) is None

    def test_daily_bars_are_not_treated_as_anchored(self):
        """Daily/weekly series have one bar time by construction; matching on
        it would be meaningless, not informative."""
        from backtest.vectorized_harness import VectorizedBacktestHarness, precompute_indicators
        h = VectorizedBacktestHarness({})
        c = [{'ts': 1700000000000 + i * 86400000, 'open': 100.0, 'high': 101.0,
              'low': 99.0, 'close': 100.5, 'volume': 100.0} for i in range(400)]
        ind = precompute_indicators(c)
        assert h._time_bucket_key(ind, list(range(150, 400, 3))) is None

    def test_twin_entries_respect_time_buckets(self):
        """The twin must actually restrict its draws. Built on a series where
        ONE minute of day behaves differently (a systematic pop): a
        time-matched twin inherits that behavior, an unmatched one does not.
        If the two distributions came out identical, time-matching would be
        decorative."""
        import random
        from backtest.vectorized_harness import (VectorizedBacktestHarness,
                                                 precompute_indicators, EXIT_CONFIGS)
        h = VectorizedBacktestHarness({})
        rng = random.Random(3)
        # Derive the anchor minute FROM the grid: an arbitrary constant may
        # never occur at this bar spacing/offset.
        anchor_minute = ((1700000000000 + 120 * 300000) // 60000) % 1440
        bars, px = [], 100.0
        for i in range(9000):
            ts = 1700000000000 + i * 300000
            minute = (ts // 60000) % 1440
            # every day at this minute, price pops; elsewhere it drifts noisily
            px *= (1.02 if minute == anchor_minute else 1 + rng.gauss(0, 0.004))
            bars.append({'ts': ts, 'open': px * 0.999, 'high': px * 1.006,
                         'low': px * 0.994, 'close': px, 'volume': 100.0})
        ind = precompute_indicators(bars)
        buckets = frozenset({anchor_minute})
        matched = [i for i in range(100, ind.n - 10)
                   if (int(ind.timestamps[i]) // 60000) % 1440 == anchor_minute]
        assert len(matched) >= 20, 'fixture too short to exercise the restriction'
        restricted = h._twin_distribution(ind, 'fixed_2r', EXIT_CONFIGS['fixed_2r'],
                                          _flat_coster(), 100, time_buckets=buckets)
        unrestricted = h._twin_distribution(ind, 'fixed_2r', EXIT_CONFIGS['fixed_2r'],
                                            _flat_coster(), 100, time_buckets=None)
        assert restricted != unrestricted
