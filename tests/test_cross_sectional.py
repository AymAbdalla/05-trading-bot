"""Tests for the cross-sectional harness (backtest/cross_sectional.py,
SPEC 5.8).

The load-bearing test here is the LOOKAHEAD ORACLE: a "cheating" ranker
that tries to trade on the decision bar's own return is run on synthetic
data engineered so that lookahead would pay +20% per trade. If the harness
ever leaks the decision bar, the cheater's result becomes implausibly
positive and the test fails - the module docstring's kill condition. All
fixtures are synthetic; nothing here touches backtest/data/.
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from backtest.cost_model import COST_MODEL_VERSION
from backtest.cross_sectional import (
    CS_GATE_VERSION, SURVIVORSHIP_STAMP, CrossSectionalHarness, Panel,
    PanelView, aggregate_15m_to_30m, leave_one_out, select_names,
)

DAY = 86_400_000
T0 = 1_600_000_000_000   # arbitrary epoch anchor


def make_candles(n, start=T0, step=DAY, price=100.0, drift=0.0,
                 skip=None, close_mult=None):
    """Synthetic bars. `skip` = set of bar ordinals to omit (missing data).
    `close_mult[i]` multiplies bar i's open to make its close (defaults 1.0
    = flat bars); the next bar opens at the previous close."""
    out = []
    px = price
    for i in range(n):
        ts = start + i * step
        if skip and i in skip:
            px = px * (1 + drift)
            continue
        mult = close_mult[i] if close_mult is not None else 1.0
        o = px
        c = px * mult
        out.append({'ts': ts, 'open': o, 'high': max(o, c) * 1.001,
                    'low': min(o, c) * 0.999, 'close': c, 'volume': 1000.0})
        px = c * (1 + drift)
    return out


def flat_harness(**cfg):
    """Zero-cost flat harness: fee 0, slippage 0, so price arithmetic is
    exact in assertions."""
    base = {'use_cost_model': False,
            'exchange': {'fees': {'taker': 0.0}, 'slippage': {'market': 0.0}}}
    base.update(cfg)
    return CrossSectionalHarness(base)


def const_ranker(names, value=1.0):
    def ranker(view):
        return {n: value for n in names}
    return ranker


# ============ ALIGNMENT ============

class TestAlignment:
    def test_grid_is_union_of_bars(self):
        a = make_candles(10)
        b = make_candles(10, start=T0 + 5 * DAY)   # offset: union is 15 keys
        panel = Panel({'A': a, 'B': b})
        assert panel.n_steps == 15
        assert list(panel.grid) == sorted({c['ts'] for c in a + b})

    def test_missing_timestamps_not_forward_filled(self):
        """A skipped bar must not exist anywhere: not in the series arrays,
        not as an eligible position, not in any view's history."""
        a = make_candles(20)
        b = make_candles(20, skip={7, 8})
        panel = Panel({'A': a, 'B': b})
        assert panel.series['B'].n == 18          # no synthetic bars
        # B is not tradable at its missing grid steps.
        g7 = int(np.searchsorted(panel.grid, T0 + 7 * DAY))
        assert panel.series['B'].pos_at[g7] == -1
        assert panel.series['A'].pos_at[g7] >= 0
        # And no view ever serves a bar with the missing timestamp.
        for g in range(panel.n_steps):
            h = PanelView(panel, g).history('B')
            if h is not None:
                assert T0 + 7 * DAY not in set(h['ts'].tolist())

    def test_missing_bar_name_not_traded_that_step(self):
        """Ranker insists on B every step; at B's missing steps the harness
        must skip it (counted), never fill it at an invented price."""
        a = make_candles(30)
        b = make_candles(30, skip={10, 11, 12})
        panel = Panel({'A': a, 'B': b})
        h = flat_harness()
        report = h.run(panel, const_ranker(['B']), 'test',
                       selection={'mode': 'k', 'k': 1, 'min_scored': 1},
                       exit_cfg={'type': 'time', 'bars': 1},
                       min_history=2, twin_seeds=0, include_trades=True)
        missing_ts = {T0 + i * DAY for i in (10, 11, 12)}
        entry_ts = {t['entry_ts'] for t in report['trades_detail']}
        assert not (entry_ts & missing_ts)
        assert report['fires_check']['names_skipped_missing_bar'] >= 3

    def test_date_align_merges_offset_daily_stamps(self):
        """Equity daily bars (04:00 UTC) and crypto daily bars (00:00 UTC)
        land on one date key under date_align, without touching true ts."""
        day0 = (T0 // DAY) * DAY
        a = make_candles(10, start=day0)                     # 00:00 stamps
        b = make_candles(10, start=day0 + 4 * 3_600_000)     # +4h stamps
        panel = Panel({'A': a, 'B': b}, date_align=True)
        assert panel.n_steps == 10                           # merged, not 20
        g = 5
        assert panel.series['A'].pos_at[g] >= 0
        assert panel.series['B'].pos_at[g] >= 0
        # True timestamps preserved for fee/fill bookkeeping.
        i = int(panel.series['B'].pos_at[g])
        assert panel.series['B'].ts[i] % DAY == 4 * 3_600_000


# ============ LOOKAHEAD ============

class TestNoLookahead:
    def test_view_never_contains_decision_bar(self):
        """Structural guarantee: max visible ts < step key, every step,
        every name."""
        panel = Panel({'A': make_candles(15), 'B': make_candles(15, skip={3})})
        for g in range(panel.n_steps):
            view = PanelView(panel, g)
            for name in ('A', 'B'):
                h = view.history(name)
                if h is not None:
                    assert h['ts'].max() < view.key

    def test_cheating_ranker_is_denied_the_trade_bar(self):
        """THE ORACLE. Ticker X alternates +20%/-20% open->close by bar
        parity; Y is flat. A ranker scoring by 'the most recent return I can
        see', entering at the OPEN and exiting at the CLOSE of the decision
        bar, would capture +20% on every trade IF the harness leaked the
        decision bar (score the +20% bar, trade the +20% bar). Because the
        view ends strictly before the decision bar, the cheater actually
        ranks on the PREVIOUS bar - buying X right before its -20% bars -
        and must lose. A positive mean per-trade return here means lookahead
        exists and every harness result is void."""
        n = 40
        x_mult = [1.2 if i % 2 == 0 else 1 / 1.2 for i in range(n)]
        panel = Panel({'X': make_candles(n, close_mult=x_mult),
                       'Y': make_candles(n)})

        def cheater(view):
            out = {}
            for name in ('X', 'Y'):
                h = view.history(name)
                if h is None or len(h['closes']) < 1:
                    continue
                out[name] = float(h['closes'][-1] / h['opens'][-1] - 1.0)
            return out

        h = flat_harness()
        report = h.run(panel, cheater, 'cheater',
                       selection={'direction': 'top', 'mode': 'k', 'k': 1,
                                  'min_scored': 1},
                       exit_cfg={'type': 'same_bar_close'},
                       entry_mode='open', min_history=1,
                       twin_seeds=0, include_trades=True)
        assert report['trades'] > 5
        rets = [(t['exit_px'] - t['entry_px']) / t['entry_px']
                for t in report['trades_detail']]
        # Lookahead payoff would be +20% on EVERY trade (score the +20% bar,
        # trade the +20% bar). Denied the decision bar, the cheater ranks on
        # the PREVIOUS bar: it buys X right after X's up-bars, i.e. into the
        # -16.7% down-bars, and parks in flat Y after X's down-bars.
        assert max(rets) < 0.15, (
            f'a trade earned {max(rets):+.3f} - the harness leaked the '
            f'decision bar (lookahead). All results void.')
        x_rets = [(t['exit_px'] - t['entry_px']) / t['entry_px']
                  for t in report['trades_detail'] if t['ticker'] == 'X']
        assert x_rets and sum(x_rets) / len(x_rets) < -0.10, (
            'cheater should be buying X into its down-bars; it is not - '
            'check the view boundary')

    def test_cheater_equals_honest_lagged_ranker(self):
        """The cheater's trades must be IDENTICAL to a ranker that honestly
        uses the previous bar - proof the harness silently degraded the
        cheat into the lag, rather than merely punishing it."""
        n = 30
        rng = np.random.default_rng(7)
        mults = {'X': (1 + rng.normal(0, 0.02, n)).tolist(),
                 'Y': (1 + rng.normal(0, 0.02, n)).tolist(),
                 'Z': (1 + rng.normal(0, 0.02, n)).tolist()}
        series = {k: make_candles(n, close_mult=m) for k, m in mults.items()}

        def score_last_visible(view):
            out = {}
            for name in ('X', 'Y', 'Z'):
                h = view.history(name)
                if h is None:
                    continue
                out[name] = float(h['closes'][-1] / h['opens'][-1] - 1.0)
            return out

        h = flat_harness()
        kwargs = dict(
            selection={'direction': 'top', 'mode': 'k', 'k': 1, 'min_scored': 1},
            exit_cfg={'type': 'same_bar_close'}, entry_mode='open',
            min_history=1, twin_seeds=0, include_trades=True)
        r1 = h.run(Panel(series), score_last_visible, 'a', **kwargs)
        r2 = h.run(Panel(series), score_last_visible, 'b', **kwargs)
        t1 = [(t['ticker'], t['entry_ts']) for t in r1['trades_detail']]
        t2 = [(t['ticker'], t['entry_ts']) for t in r2['trades_detail']]
        assert t1 == t2 and len(t1) > 0


# ============ SELECTION ============

class TestSelection:
    SCORES = {f'T{i:02d}': float(i) for i in range(20)}   # T00 lowest

    def test_bottom_decile_of_20_is_two_lowest(self):
        sel = select_names(self.SCORES, {'direction': 'bottom',
                                         'mode': 'decile', 'min_scored': 10})
        assert sorted(sel) == ['T00', 'T01']

    def test_top_decile_of_20_is_two_highest(self):
        sel = select_names(self.SCORES, {'direction': 'top',
                                         'mode': 'decile', 'min_scored': 10})
        assert sorted(sel) == ['T18', 'T19']

    def test_quintile_and_k_modes(self):
        sel = select_names(self.SCORES, {'direction': 'bottom',
                                         'mode': 'quintile', 'min_scored': 10})
        assert sorted(sel) == ['T00', 'T01', 'T02', 'T03']
        sel = select_names(self.SCORES, {'direction': 'top', 'mode': 'k',
                                         'k': 3, 'min_scored': 10})
        assert sorted(sel) == ['T17', 'T18', 'T19']

    def test_min_scored_gate_blocks_thin_cross_sections(self):
        """A 'decile' of 3 names is not a decile - the weekend-crypto case."""
        thin = {'A': 1.0, 'B': 2.0, 'C': 3.0}
        assert select_names(thin, {'mode': 'decile', 'min_scored': 10}) == []

    def test_max_and_min_names_clamps(self):
        sel = select_names(self.SCORES, {'direction': 'top', 'mode': 'quintile',
                                         'min_scored': 10, 'max_names': 2})
        assert len(sel) == 2
        sel = select_names(self.SCORES, {'direction': 'top', 'mode': 'decile',
                                         'min_scored': 10, 'min_names': 5})
        assert sel == []          # decile yields 2 < min 3-5 genome floor

    def test_nan_and_none_scores_dropped(self):
        scores = dict(self.SCORES)
        scores['BAD'] = float('nan')
        scores['NONE'] = None
        sel = select_names(scores, {'direction': 'top', 'mode': 'k', 'k': 25,
                                    'min_scored': 5})
        assert 'BAD' not in sel and 'NONE' not in sel


# ============ COST STAMPING ============

class TestCostStamping:
    def _run(self, use_cost_model):
        panel = Panel({'AAA': make_candles(60), 'BBB': make_candles(60)})
        h = CrossSectionalHarness({'use_cost_model': use_cost_model})
        return h.run(panel, const_ranker(['AAA', 'BBB']), 'stamp_test',
                     selection={'mode': 'k', 'k': 1, 'min_scored': 1},
                     exit_cfg={'type': 'time', 'bars': 2},
                     min_history=5, twin_seeds=2)

    def test_venue_model_stamps_version_and_class(self):
        report = self._run(True)
        assert report['cost_model_version'] == COST_MODEL_VERSION
        assert report['cost_model_version_uniform'] is True
        assert report['asset_classes'] == ['EQUITY']
        for cell in report['per_cell']:
            assert cell['asset_class'] == 'EQUITY'

    def test_flat_model_stamps_flat_version(self):
        report = self._run(False)
        assert report['cost_model_version'].startswith('flat:')
        assert report['cost_model_version_uniform'] is True

    def test_survivorship_and_gate_stamps_always_present(self):
        report = self._run(True)
        assert report['survivorship'] == SURVIVORSHIP_STAMP
        assert report['cs_gate_version'] == CS_GATE_VERSION
        assert report['harness'] == 'cross_sectional'


# ============ FILL / EXIT SEMANTICS ============

class TestFills:
    def test_time_exit_holds_n_bars(self):
        panel = Panel({'A': make_candles(30), 'B': make_candles(30)})
        h = flat_harness()
        report = h.run(panel, const_ranker(['A']), 't',
                       selection={'mode': 'k', 'k': 1, 'min_scored': 1},
                       exit_cfg={'type': 'time', 'bars': 3},
                       min_history=2, rebalance_every=10,
                       twin_seeds=0, include_trades=True)
        for t in report['trades_detail']:
            assert t['exit_idx'] - t['entry_idx'] == 3
            assert t['exit_reason'] == 'time'

    def test_stop_is_gap_aware(self):
        """Bar opens BELOW the stop -> fill at the open, not the stop.
        Filling at the stop on a gap-through understates losses."""
        candles = make_candles(30)
        crash_i = 20
        for j in range(crash_i, 30):          # -30% gap from bar 20 on
            for k in ('open', 'high', 'low', 'close'):
                candles[j][k] *= 0.70
        panel = Panel({'A': candles, 'B': make_candles(30)})
        h = flat_harness()
        report = h.run(panel, const_ranker(['A']), 't',
                       selection={'mode': 'k', 'k': 1, 'min_scored': 1},
                       exit_cfg={'type': 'time', 'bars': 10,
                                 'stop_atr_mult': 2.0},
                       min_history=16, rebalance_every=100,
                       twin_seeds=0, include_trades=True)
        stops = [t for t in report['trades_detail'] if t['exit_reason'] == 'stop']
        assert stops, 'crash never triggered the stop'
        t = stops[0]
        # Gap-through: fill == that bar's open (well below the stop level).
        assert t['exit_px'] == pytest.approx(70.0, rel=0.02)
        assert t['exit_px'] < t['stop_px']

    def test_no_entry_on_final_bar_for_time_exit(self):
        panel = Panel({'A': make_candles(10), 'B': make_candles(10)})
        h = flat_harness()
        report = h.run(panel, const_ranker(['A']), 't',
                       selection={'mode': 'k', 'k': 1, 'min_scored': 1},
                       exit_cfg={'type': 'time', 'bars': 5},
                       min_history=9, twin_seeds=0, include_trades=True)
        # Only step g=9 (the last bar) clears min_history=9: unholdable.
        assert report['trades'] == 0
        assert report['fires_check']['names_skipped_unfillable'] >= 1

    def test_held_name_is_not_doubled(self):
        panel = Panel({'A': make_candles(40), 'B': make_candles(40)})
        h = flat_harness()
        report = h.run(panel, const_ranker(['A']), 't',
                       selection={'mode': 'k', 'k': 1, 'min_scored': 1},
                       exit_cfg={'type': 'time', 'bars': 6},
                       min_history=2, rebalance_every=1,
                       twin_seeds=0, include_trades=True)
        trades = sorted(report['trades_detail'], key=lambda t: t['entry_ts'])
        for prev, nxt in zip(trades, trades[1:]):
            assert nxt['entry_idx'] > prev['exit_idx']
        assert report['fires_check']['names_skipped_already_held'] > 0


# ============ REPORT SHAPE ============

class TestReportShape:
    def _report(self):
        rng = np.random.default_rng(3)
        series = {f'T{i}': make_candles(
            80, close_mult=(1 + rng.normal(0, 0.01, 80)).tolist())
            for i in range(6)}
        h = flat_harness()
        return h.run(Panel(series), const_ranker(list(series), 1.0), 'shape',
                     selection={'mode': 'k', 'k': 2, 'min_scored': 3},
                     exit_cfg={'type': 'time', 'bars': 3},
                     min_history=5, rebalance_every=3, twin_seeds=5)

    def test_leave_one_out_shape(self):
        report = self._report()
        loo = report['leave_one_out']
        for key in ('n_assets', 'worst_drop_asset', 'pnl_per_trade_worst_drop',
                    'carried_by_one_asset'):
            assert key in loo
        assert loo['n_assets'] >= 2

    def test_per_cell_and_pooled_present(self):
        report = self._report()
        assert report['trades'] > 0
        assert report['per_cell'], 'per-cell rows missing'
        for cell in report['per_cell']:
            for key in ('ticker', 'trades', 'pnl_per_trade', 'win_rate',
                        'asset_class'):
                assert key in cell
        assert sum(c['trades'] for c in report['per_cell']) == report['trades']
        assert 'time_split' in report
        assert set(report['time_split']) == {'first_half', 'second_half'}

    def test_leave_one_out_detects_concentration(self):
        """One huge winner among flat trades -> carried_by_one_asset."""
        Trade = __import__('backtest.cross_sectional',
                           fromlist=['CSTrade']).CSTrade
        mk = lambda tk, pnl: Trade(tk, 0, 1, 0, 1, 100, 100, None, 1, pnl, 0,
                                   pnl, 'time', 'EQUITY', 100)
        trades = [mk('AAA', 5.0)] + [mk(f'B{i}', 0.0) for i in range(9)]
        loo = leave_one_out(trades)
        assert loo['worst_drop_asset'] == 'AAA'
        assert loo['carried_by_one_asset'] is True


# ============ ROBUSTNESS ============

class TestRobustness:
    def test_raising_ranker_degrades_to_no_signal(self):
        """Rank code must never kill a run (standing rule): a ranker that
        raises produces zero trades and a visible error count, not a crash."""
        panel = Panel({'A': make_candles(20), 'B': make_candles(20)})

        def bomb(view):
            raise RuntimeError('ranker bug')

        h = flat_harness()
        report = h.run(panel, bomb, 'bomb',
                       selection={'mode': 'k', 'k': 1, 'min_scored': 1},
                       exit_cfg={'type': 'time', 'bars': 1},
                       min_history=2, twin_seeds=0)
        assert report['trades'] == 0
        assert report['fires_check']['ranker_errors'] > 0

    def test_context_series_readable_never_tradable(self):
        """A ranker may score a context name; the harness must refuse to
        trade it even when it tops the ranking."""
        panel = Panel({'A': make_candles(30), 'B': make_candles(30)},
                      context={'VIXY': make_candles(30)})
        view = PanelView(panel, 10)
        assert view.history('VIXY') is not None      # readable

        h = flat_harness()
        report = h.run(panel, const_ranker(['VIXY', 'A'], 1.0), 't',
                       selection={'mode': 'k', 'k': 2, 'min_scored': 1},
                       exit_cfg={'type': 'time', 'bars': 1},
                       min_history=2, twin_seeds=0, include_trades=True)
        assert report['trades'] > 0
        assert all(t['ticker'] != 'VIXY' for t in report['trades_detail'])


# ============ TWINS ============

class TestTwins:
    def test_twins_are_time_matched(self):
        """Twin entries may only occur at timestamps where the STRATEGY
        formed positions - the cross-sectional version of the same-clock-slot
        twin rule (vectorized_harness._time_bucket_key)."""
        rng = np.random.default_rng(11)
        series = {f'T{i}': make_candles(
            60, close_mult=(1 + rng.normal(0, 0.01, 60)).tolist())
            for i in range(5)}
        panel = Panel(series)

        formed_ts = set()

        def sparse_ranker(view):
            # Fires only every 7th step: a clock-anchored strategy.
            g_key = view.key
            if ((g_key - T0) // DAY) % 7 != 0:
                return {}
            formed_ts.add(g_key)
            return {n: 1.0 for n in series}

        h = flat_harness()
        report = h.run(panel, sparse_ranker, 't',
                       selection={'mode': 'k', 'k': 2, 'min_scored': 3},
                       exit_cfg={'type': 'time', 'bars': 2},
                       min_history=3, twin_seeds=3, debug_twins=True)
        assert report['trades'] > 0
        twin_ts = set(report['twin_debug']['entry_ts'])
        assert twin_ts, 'twins never traded'
        assert twin_ts <= formed_ts, 'twin traded at a step the strategy never formed on'

    def test_twin_percentile_reported(self):
        panel = Panel({'A': make_candles(60), 'B': make_candles(60),
                       'C': make_candles(60)})
        h = flat_harness()
        report = h.run(panel, const_ranker(['A', 'B', 'C']), 't',
                       selection={'mode': 'k', 'k': 1, 'min_scored': 1},
                       exit_cfg={'type': 'time', 'bars': 2},
                       min_history=5, twin_seeds=5)
        assert report['twin_sample_size'] == 5
        assert report['twin_percentile'] is not None


# ============ 30m AGGREGATION (Same-Clock Echo prerequisite) ============

class TestAggregation:
    def test_15m_pairs_become_30m_bars(self):
        m15 = 15 * 60 * 1000
        base = (T0 // (30 * 60 * 1000)) * (30 * 60 * 1000)
        candles = [
            {'ts': base, 'open': 10, 'high': 12, 'low': 9, 'close': 11, 'volume': 5},
            {'ts': base + m15, 'open': 11, 'high': 14, 'low': 10, 'close': 13, 'volume': 7},
            {'ts': base + 2 * m15, 'open': 13, 'high': 13.5, 'low': 12, 'close': 12.5, 'volume': 4},
        ]
        out = aggregate_15m_to_30m(candles)
        assert len(out) == 2
        first = out[0]
        assert first['ts'] == base
        assert first['open'] == 10 and first['close'] == 13
        assert first['high'] == 14 and first['low'] == 9
        assert first['volume'] == 12
        # Lone 15m bar in the next half hour stands alone - no fill.
        assert out[1]['open'] == 13 and out[1]['close'] == 12.5

    def test_gap_stays_gap(self):
        m30 = 30 * 60 * 1000
        base = (T0 // m30) * m30
        candles = [
            {'ts': base, 'open': 1, 'high': 1, 'low': 1, 'close': 1, 'volume': 1},
            {'ts': base + 4 * m30, 'open': 2, 'high': 2, 'low': 2, 'close': 2, 'volume': 1},
        ]
        out = aggregate_15m_to_30m(candles)
        assert len(out) == 2                     # 3 missing half-hours NOT filled
        assert out[1]['ts'] - out[0]['ts'] == 4 * m30
