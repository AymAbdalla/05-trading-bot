"""Tests for the Lab v5 P2 TOLL COLLECTOR maker-fill experiment.

The maker-fill simulator is P2's self-declared load-bearing wall ("an
optimistic fill model here would be self-deception of exactly the §3.6
class"), so these tests pin, in order of importance:
  1. the trade-through rule (a touch NEVER fills - SPEC 5.9),
  2. the arming percentile using STRICTLY past data (the lookahead class of
     bug that cost the project its first graveyard),
  3. maker legs paying zero fee and zero slippage,
  4. the taker stop paying real fees + slippage,
  5. fires-check fields present and serialized BEFORE any P&L field
     (v5 work order 4).
All fixtures are synthetic and tiny; nothing here touches the data dir.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.cost_model import CostModel, COST_MODEL_VERSION
from backtest.toll_collector import (
    arming_stats, build_report, run_cell,
    trades_through_buy, trades_through_sell,
)


@pytest.fixture
def cm():
    return CostModel()


def _bars(rows):
    """rows: list of (open, high, low, close). Timestamps are hourly ms."""
    return [{'ts': 1_700_000_000_000 + i * 3_600_000,
             'open': o, 'high': h, 'low': l, 'close': c, 'volume': 1.0}
            for i, (o, h, l, c) in enumerate(rows)]


# ---------------------------------------------------------------------------
# 1. Trade-through rule (the load-bearing wall)
# ---------------------------------------------------------------------------

class TestTradeThroughRule:
    TICK = 0.01

    def test_touch_does_not_fill(self):
        # low == limit: the far queue traded at our price; our resting order
        # has no proof of execution. NO fill.
        assert trades_through_buy(bar_low=98.00, limit=98.00, tick=self.TICK) is False

    def test_one_tick_beyond_does_not_fill(self):
        # low == limit - tick: still not THROUGH. The rule is strict
        # inequality beyond a full tick, per SPEC 5.9's "never on a touch".
        assert trades_through_buy(bar_low=97.99, limit=98.00, tick=self.TICK) is False

    def test_through_fills(self):
        assert trades_through_buy(bar_low=97.98, limit=98.00, tick=self.TICK) is True

    def test_sell_side_mirror(self):
        assert trades_through_sell(bar_high=100.00, limit=100.00, tick=self.TICK) is False
        assert trades_through_sell(bar_high=100.01, limit=100.00, tick=self.TICK) is False
        assert trades_through_sell(bar_high=100.02, limit=100.00, tick=self.TICK) is True

    def test_engine_touch_fills_taker_but_not_maker(self, cm):
        """End-to-end asymmetry: a bar whose low EQUALS the limit fills the
        taker-at-touch arm and must NOT fill the maker arm."""
        # Flat 100-close bars with 2-point range -> ATR(2) = 2, so with k=1
        # the resting limit sits at 98. Arm only bar 3 (armed_override), so
        # the order rests exactly during bar 4.
        rows = [(100, 101, 99, 100)] * 4 + [
            (100, 101, 98.00, 100),   # bar 4: low touches 98 exactly
        ] + [(100, 101, 99, 100)] * 3
        armed = np.zeros(len(rows), dtype=bool)
        armed[3] = True
        cell = run_cell(_bars(rows), 'BTCUSDT', 1.0, cm, atr_period=2,
                        armed_override=armed)
        assert cell['maker_counters']['fills'] == 0
        assert cell['taker_counters']['fills'] == 1


# ---------------------------------------------------------------------------
# 2. Arming percentile uses ONLY past data
# ---------------------------------------------------------------------------

class TestArmingLookahead:
    def test_current_bar_excluded_from_percentile_window(self):
        """Fixture where including the current bar's vol in its own
        percentile window FLIPS the arming decision.

        vol = [0, 0, 10, 5], window=3, pct=70:
          correct (past-only) window at i=3: [0, 0, 10]
            -> 70th pct = 4.0, and 5 > 4.0 -> ARMED.
          buggy (window includes bar 3):     [0, 10, 5]
            -> 70th pct = 7.0, and 5 > 7.0 is False -> not armed.
        So a lookahead implementation reads False where True belongs.
        """
        vol = np.array([0.0, 0.0, 10.0, 5.0])
        armed, pctile, valid = arming_stats(vol, pctl_window=3, arm_pct=70.0)
        assert bool(valid[3]) is True
        assert bool(armed[3]) is True   # would be False under the lookahead bug
        # And the recorded percentile rank comes from the past-only window:
        # 2 of 3 past values are below 5.0 -> 66.67.
        assert pctile[3] == pytest.approx(200.0 / 3.0)

    def test_warmup_bars_never_armed(self):
        vol = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        armed, _, valid = arming_stats(vol, pctl_window=3, arm_pct=70.0)
        assert not armed[:3].any()
        assert not valid[:3].any()


# ---------------------------------------------------------------------------
# 3 & 4. Cost mechanics: maker legs free, taker stop pays
# ---------------------------------------------------------------------------

def _filled_cell(cm, exit_rows, stop_atr_mult=1.5):
    """Shared fixture: arm bar 3, order rests at L=98 (ATR(2)=2, k=1) during
    bar 4, which trades THROUGH (low 97.5 < 98 - tick). Caller supplies the
    bars after the fill to steer the exit path."""
    rows = [(100, 101, 99, 100)] * 4 + [
        (100, 101, 97.5, 100),        # bar 4: trades through 98 -> maker fill
    ] + exit_rows
    armed = np.zeros(len(rows), dtype=bool)
    armed[3] = True
    return run_cell(_bars(rows), 'BTCUSDT', 1.0, cm, atr_period=2,
                    stop_atr_mult=stop_atr_mult, armed_override=armed)


class TestMakerLegsAreFree:
    def test_maker_round_trip_pays_zero_fee_and_zero_slippage(self, cm):
        # Exit: target = 98 + 1*ATR(2) = 100; bar high 100.5 > 100 + tick.
        cell = _filled_cell(cm, [(99, 100.5, 98.5, 100)])
        trades = cell['maker_trades']
        assert len(trades) == 1
        t = trades[0]
        assert t['reason'] == 'target_maker'
        # Zero fee both legs (Binance.US 0% maker per cost_model.py).
        assert t['entry_fee'] == 0.0
        assert t['exit_fee'] == 0.0
        assert t['fees'] == 0.0
        # Zero slippage: fills at EXACTLY the limit prices, never adjusted.
        assert t['entry_px'] == 98.0
        assert t['exit_px'] == 100.0
        # And the P&L is therefore the pure price move on $100 notional.
        assert t['pnl_usd'] == pytest.approx((100.0 - 98.0) * (100.0 / 98.0))

    def test_maker_exit_touch_does_not_fill(self, cm):
        # high == target + tick exactly (100.01): must NOT exit; the later
        # bar through 100.02 must.
        cell = _filled_cell(cm, [(99, 100.01, 98.5, 100),
                                 (100, 100.5, 99.5, 100)])
        t = cell['maker_trades'][0]
        assert t['reason'] == 'target_maker'
        assert t['bars_held'] == 2      # exited on the second bar, not the first


class TestTakerStopPays:
    def test_stop_pays_taker_fee_and_slippage(self, cm):
        # Stop = 98 - 1.5*2 = 95. Bar 5 opens 96, low 94 -> stop fills at
        # min(95, 96) = 95, slippage-adjusted DOWN, plus a taker fee.
        cell = _filled_cell(cm, [(96, 97, 94, 95)])
        t = cell['maker_trades'][0]
        assert t['reason'] == 'stop'
        assert t['exit_fee'] > 0.0                    # taker leg pays
        assert t['exit_px'] < 95.0                    # slippage worsened fill
        assert t['exit_px'] == pytest.approx(95.0 * (1 - cm.slippage_taker))
        # Entry leg was maker and stays free even on a losing trade.
        assert t['entry_fee'] == 0.0
        # Fee matches the cost model's core-pair taker rate (BTC/USDT).
        notional = 95.0 * t['qty']
        assert t['exit_fee'] == pytest.approx(
            cm.crypto_leg(notional, 'BTC/USDT').commission)

    def test_gap_through_stop_fills_at_open(self, cm):
        # Bar opens BELOW the stop (94 < 95): gap-aware fill at the open,
        # not the stop price - filling at the stop on a gap understates the
        # loss (vectorized_harness convention, kept here).
        cell = _filled_cell(cm, [(94, 94.5, 93, 94)])
        t = cell['maker_trades'][0]
        assert t['reason'] == 'stop'
        assert t['exit_px'] == pytest.approx(94.0 * (1 - cm.slippage_taker))


# ---------------------------------------------------------------------------
# 5. Fires-check fields exist and come BEFORE P&L
# ---------------------------------------------------------------------------

class TestFiresCheckFirst:
    def test_report_fields_and_order(self, cm):
        cell = _filled_cell(cm, [(99, 100.5, 98.5, 100)])
        report = build_report([cell], cm)
        # All four mandated fires-check numbers exist.
        fc = report['fires_check']['pooled']
        for key in ('armed_time_pct', 'orders_placed', 'maker_fills',
                    'fill_rate_pct', 'taker_stop_rate_pct'):
            assert key in fc, key
        # Serialization order: fires_check strictly precedes pnl (dict
        # insertion order IS the json order; work order 4 made this
        # mandatory, not stylistic).
        keys = list(report.keys())
        assert keys.index('fires_check') < keys.index('pnl')
        # The stamp that makes pooling legal or illegal downstream.
        assert report['cost_model_version'] == COST_MODEL_VERSION
        # Prediction and kill condition ride with the result, verbatim.
        assert 'Kill condition' in report['kill_condition_verbatim']
        assert 'Pre-registered prediction' in report['prediction_verbatim']

    def test_small_sample_flagged_as_shrug(self, cm):
        """Standing rule 7: a result on a handful of fills must announce
        itself as underpowered."""
        cell = _filled_cell(cm, [(99, 100.5, 98.5, 100)])
        report = build_report([cell], cm)
        assert any('shrug' in n for n in report['honesty_notes'])
