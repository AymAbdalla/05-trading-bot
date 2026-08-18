"""Tests for the dashboard's chart layer (`dashboard/charts.py`) and its
formatting helpers.

Charts are hard to test and easy to get wrong, so these tests target the
properties that are actually checkable and actually matter:

**Every chart survives empty input.** The bot has not traded yet. A chart
function that raises on an empty frame takes the whole tab down with it, and
Streamlit renders that as a stack trace where the dashboard should be.

**No chart has two y-axes.** Dual-axis charts imply correlations that are not
there; it is the single most common charting mistake. `test_no_chart_has_a_
second_y_axis` is a standing guard against someone "helpfully" overlaying
drawdown onto the equity curve later.

**Sign never rides on color alone.** Green and red measure dE 4.1 apart under
deuteranopia on this surface - a red-green colorblind reader cannot separate
them. Every sign-encoded mark therefore carries an explicit +/- in its label
and every two-color chart carries a legend. These tests assert the redundant
channel is present, because it is the channel that is easy to delete by
accident during a refactor.

**`inf` and `None` render honestly.** A profit factor with no losing trade is
genuinely infinite (convention 12) and an unknown is not a zero.
"""
import math
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from dashboard import charts, config, db_reader  # noqa: E402

FIFTEEN_MIN_MS = 15 * 60_000


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

@pytest.fixture
def equity_df():
    ts = [1_700_000_000_000 + i * FIFTEEN_MIN_MS for i in range(8)]
    equity = [100.0, 105.0, 120.0, 90.0, 95.0, 108.0, 112.0, 130.0]
    df = pd.DataFrame({'ts': ts, 'equity': equity, 'cash': equity,
                       'open_risk': [0.0] * 8, 'mode': ['paper'] * 8})
    df['time'] = pd.to_datetime(df['ts'], unit='ms')
    return df


@pytest.fixture
def trades_df():
    rows = []
    for i, pnl in enumerate([30.0, -10.0, 5.5, -2.25, 12.0]):
        rows.append({
            'id': 'p{}'.format(i), 'opened_ts': 1_700_000_000_000 + i * 1000,
            'closed_ts': 1_700_000_000_000 + i * 2000, 'pair': 'BTC/USDT',
            'asset_class': 'CRYPTO', 'strategy_id': 'hammer', 'side': 'long',
            'entry_px': 100.0, 'exit_px': 100.0 + pnl, 'qty': 1.0, 'stop_px': 95.0,
            'target_px': 130.0, 'pnl_gross': pnl, 'pnl_net': pnl, 'fees': 0.1,
            'r_multiple': pnl / 10.0, 'exit_reason': 'target', 'mode': 'paper',
            'status': 'WIN' if pnl > 0 else 'LOSS',
        })
    rows.append({
        'id': 'p-open', 'opened_ts': 1_700_000_000_000, 'closed_ts': None,
        'pair': 'will-x-happen', 'asset_class': 'POLYMARKET',
        'strategy_id': 'PM_box_builder', 'side': 'long', 'entry_px': 0.42,
        'exit_px': None, 'qty': 10.0, 'stop_px': 0.3, 'target_px': 0.8,
        'pnl_gross': None, 'pnl_net': None, 'fees': 0.0, 'r_multiple': None,
        'exit_reason': None, 'mode': 'paper', 'status': 'OPEN',
    })
    df = pd.DataFrame(rows)
    df['opened_at'] = pd.to_datetime(df['opened_ts'], unit='ms')
    df['closed_at'] = pd.to_datetime(df['closed_ts'], unit='ms')
    return df


@pytest.fixture
def perf_df():
    return pd.DataFrame([
        {'strategy_id': 'hammer', 'name': 'Hammer', 'status': 'shadow',
         'asset_class': 'CRYPTO', 'total_trades': 5, 'open_trades': 0, 'wins': 3,
         'losses': 2, 'win_rate': 0.6, 'pnl_net': 35.25, 'avg_r': 0.7,
         'sharpe_trade': 0.4, 'profit_factor': 3.9},
        {'strategy_id': 'doji', 'name': 'Doji', 'status': 'candidate',
         'asset_class': 'CRYPTO', 'total_trades': 4, 'open_trades': 1, 'wins': 1,
         'losses': 3, 'win_rate': 0.25, 'pnl_net': -18.0, 'avg_r': -0.5,
         'sharpe_trade': -0.3, 'profit_factor': 0.4},
        {'strategy_id': 'idle', 'name': 'Idle', 'status': 'candidate',
         'asset_class': 'CRYPTO', 'total_trades': 0, 'open_trades': 0, 'wins': 0,
         'losses': 0, 'win_rate': None, 'pnl_net': 0.0, 'avg_r': None,
         'sharpe_trade': None, 'profit_factor': None},
    ])


#: (callable, empty-input args). Every chart in the module belongs here.
EMPTY_CASES = [
    (charts.equity_curve, pd.DataFrame(columns=['ts', 'equity', 'time'])),
    (charts.drawdown_curve, pd.DataFrame(columns=['ts', 'equity', 'time'])),
    (charts.pnl_distribution, pd.DataFrame(columns=['closed_ts', 'pnl_net'])),
    (charts.strategy_pnl_bar, pd.DataFrame(columns=['strategy_id', 'name', 'pnl_net', 'total_trades'])),
    (charts.verdict_composition, {}),
    (charts.pass_concentration_bar, []),
    (charts.strategy_firing_bar, pd.DataFrame(columns=['strategy', 'n_trades', 'n_rows_tested'])),
    (charts.asset_class_pnl_bar, []),
    (charts.lifecycle_timeline, pd.DataFrame(columns=['time', 'event_type', 'strategy_id'])),
]


# --------------------------------------------------------------------------
# Empty states
# --------------------------------------------------------------------------

@pytest.mark.parametrize('fn,empty_input', EMPTY_CASES,
                         ids=[c[0].__name__ for c in EMPTY_CASES])
def test_charts_render_an_empty_state_rather_than_raising(fn, empty_input):
    fig = fn(empty_input)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0, 'an empty chart must not draw phantom marks'
    assert fig.layout.annotations, 'the empty state must say something, not just be blank'


@pytest.mark.parametrize('fn,empty_input', EMPTY_CASES,
                         ids=[c[0].__name__ for c in EMPTY_CASES])
def test_charts_survive_none_input(fn, empty_input):
    """A reader that failed returns None in some paths; a chart tab should not
    be the thing that discovers this."""
    assert isinstance(fn(None), go.Figure)


def test_empty_figure_hides_its_axes():
    """An axis-only chart reads as 'the value is zero'. It is not zero, there
    is no value."""
    fig = charts.empty_figure('nothing here')
    assert fig.layout.xaxis.visible is False
    assert fig.layout.yaxis.visible is False


# --------------------------------------------------------------------------
# The dual-axis guard
# --------------------------------------------------------------------------

ALL_CHARTS = [
    ('equity_curve', lambda f: charts.equity_curve(f['equity'])),
    ('drawdown_curve', lambda f: charts.drawdown_curve(f['equity'])),
    ('pnl_distribution', lambda f: charts.pnl_distribution(f['trades'])),
    ('strategy_pnl_bar', lambda f: charts.strategy_pnl_bar(f['perf'])),
    ('verdict_composition', lambda f: charts.verdict_composition(
        {'PASS': 381, 'FAIL': 486350, 'NOT_TESTED': 48642, 'PASS_BENCHMARK': 52})),
    ('pass_concentration_bar', lambda f: charts.pass_concentration_bar(
        [{'ticker_timeframe': 'SLB 1h', 'pass_rows': 24},
         {'ticker_timeframe': 'TSLA 15m', 'pass_rows': 16}])),
    ('strategy_firing_bar', lambda f: charts.strategy_firing_bar(f['health'])),
    ('asset_class_pnl_bar', lambda f: charts.asset_class_pnl_bar(
        [{'strategy': 'hammer', 'class': 'CRYPTO', 'pnl_per_trade': -0.079, 'trades': 435}])),
    ('lifecycle_timeline', lambda f: charts.lifecycle_timeline(f['lifecycle'])),
]


@pytest.fixture
def all_inputs(equity_df, trades_df, perf_df):
    health = pd.DataFrame([
        {'strategy': 'C2', 'asset_class': 'FUTURES', 'n_trades': 0,
         'n_rows_tested': 264, 'n_rows_not_tested': 9471, 'observed_best_pf': 0.0,
         'confidence': 'cold_start', 'fires': False},
        {'strategy': 'hammer', 'asset_class': 'CRYPTO', 'n_trades': 435,
         'n_rows_tested': 143, 'n_rows_not_tested': 0, 'observed_best_pf': 1.2,
         'confidence': 'ok', 'fires': True},
    ])
    lifecycle = pd.DataFrame([
        {'time': pd.Timestamp('2026-08-01 10:00'), 'ts': 1, 'event_type': 'strategy_promoted',
         'strategy_id': 'hammer', 'from_status': 'candidate', 'to_status': 'shadow',
         'actor': 'quant'},
        {'time': pd.Timestamp('2026-08-05 10:00'), 'ts': 2, 'event_type': 'strategy_demoted',
         'strategy_id': 'doji', 'from_status': 'shadow', 'to_status': 'retired',
         'actor': 'aym'},
    ])
    return {'equity': equity_df, 'trades': trades_df, 'perf': perf_df,
            'health': health, 'lifecycle': lifecycle}


@pytest.mark.parametrize('name,build', ALL_CHARTS, ids=[c[0] for c in ALL_CHARTS])
def test_no_chart_has_a_second_y_axis(name, build, all_inputs):
    """Two y-scales on one frame make unrelated series look correlated. Equity
    and drawdown are two stacked charts for exactly this reason; this guard
    stops a later refactor from 'tidying' them into one."""
    fig = build(all_inputs)
    layout = fig.layout.to_plotly_json()
    extra = [k for k in layout if k.startswith('yaxis') and k != 'yaxis']
    assert not extra, '{} grew a second y-axis: {}'.format(name, extra)
    for trace in fig.data:
        assert getattr(trace, 'yaxis', None) in (None, 'y'), \
            '{} put a trace on a secondary axis'.format(name)


@pytest.mark.parametrize('name,build', ALL_CHARTS, ids=[c[0] for c in ALL_CHARTS])
def test_every_chart_renders_with_real_input(name, build, all_inputs):
    fig = build(all_inputs)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 1, '{} drew nothing from non-empty input'.format(name)


# --------------------------------------------------------------------------
# Redundant encoding of sign
# --------------------------------------------------------------------------

def test_strategy_pnl_bars_label_the_sign_not_just_the_color(perf_df):
    """Green and red are indistinguishable to a deuteranope. The signed text
    label is the channel that survives that, and it must not be optional."""
    fig = charts.strategy_pnl_bar(perf_df)
    labels = list(fig.data[0].text)
    assert any(t.startswith('+') for t in labels), 'no positive value carries a + sign'
    assert any(t.startswith('-') for t in labels), 'no negative value carries a - sign'

    colors = list(fig.data[0].marker.color)
    values = list(fig.data[0].x)
    for value, color in zip(values, colors):
        expected = config.PROFIT if value >= 0 else config.LOSS
        assert color == expected


def test_strategy_pnl_bar_excludes_strategies_that_never_traded(perf_df):
    """A zero-length bar for a strategy with no trades reads as 'broke even'.
    It did not break even; it did not trade."""
    fig = charts.strategy_pnl_bar(perf_df)
    assert 'Idle' not in list(fig.data[0].y)
    assert len(fig.data[0].y) == 2


def test_pnl_distribution_splits_at_zero_with_a_legend(trades_df):
    """Two named traces rather than one color-ramped trace, so the split is a
    labelled distinction instead of one the reader has to infer."""
    fig = charts.pnl_distribution(trades_df)
    names = sorted(t.name for t in fig.data)
    assert names == ['Losing trades', 'Winning trades']
    assert fig.layout.showlegend is True

    # Shared bins, or the two halves sit on different grids and the shape lies.
    bins = {(t.xbins.start, t.xbins.end, t.xbins.size) for t in fig.data}
    assert len(bins) == 1


def test_pnl_distribution_ignores_open_trades(trades_df):
    """An open position has no realised PnL. Counting it as 0.0 would put a
    fake spike at the middle of the distribution."""
    fig = charts.pnl_distribution(trades_df)
    total = sum(len(t.x) for t in fig.data)
    assert total == 5, 'the open position must not appear in a realised-PnL histogram'


def test_equity_curve_colors_by_direction_and_marks_the_start(equity_df):
    fig = charts.equity_curve(equity_df)
    assert len(fig.data) == 1
    assert fig.layout.showlegend is False, 'one series needs no legend; the title names it'
    # 100 -> 130 is up.
    assert fig.data[0].line.color == config.PROFIT
    shapes = fig.layout.to_plotly_json().get('shapes', [])
    assert shapes, 'the starting-equity baseline is what makes the line readable'


def test_equity_curve_turns_red_when_underwater(equity_df):
    down = equity_df.copy()
    down['equity'] = [100.0, 99.0, 98.0, 97.0, 96.0, 95.0, 94.0, 93.0]
    assert charts.equity_curve(down).data[0].line.color == config.LOSS


def test_drawdown_is_never_positive(equity_df):
    fig = charts.drawdown_curve(equity_df)
    assert max(fig.data[0].y) <= 0.0
    # 120 -> 90 is the worst trough in the fixture: -25%.
    assert min(fig.data[0].y) == pytest.approx(-25.0)


def test_drawdown_needs_two_points(equity_df):
    one = equity_df.head(1)
    fig = charts.drawdown_curve(one)
    assert len(fig.data) == 0
    assert fig.layout.annotations


def test_firing_bar_labels_non_firing_strategies_in_words(all_inputs):
    """'NON-FIRING' as text, not just a warning-colored zero-length bar - a
    zero-length bar in a warning color is exactly as informative as nothing."""
    fig = charts.strategy_firing_bar(all_inputs['health'])
    labels = list(fig.data[0].text)
    assert 'NON-FIRING' in labels

    colors = list(fig.data[0].marker.color)
    trades = list(fig.data[0].x)
    for t, c in zip(trades, colors):
        assert c == (config.STATUS_WARNING if t == 0 else config.CATEGORICAL[0])


def test_firing_bar_excludes_strategies_with_nothing_tested():
    """No evidence either way is not evidence of not firing (convention 11)."""
    health = pd.DataFrame([
        {'strategy': 'untested', 'n_trades': 0, 'n_rows_tested': 0},
        {'strategy': 'tested', 'n_trades': 0, 'n_rows_tested': 100},
    ])
    fig = charts.strategy_firing_bar(health)
    assert list(fig.data[0].y) == ['tested']


def test_verdict_composition_is_one_stacked_bar_not_a_ranking():
    """381 PASS against 486,350 FAIL cannot be a comparative bar chart: linear
    hides PASS, log makes the lengths meaningless. Composition is the question
    a stacked bar can honestly answer."""
    counts = {'PASS': 381, 'FAIL': 486350, 'NOT_TESTED': 48642, 'PASS_BENCHMARK': 52}
    fig = charts.verdict_composition(counts)
    assert fig.layout.barmode == 'stack'
    assert len(fig.data) == 4
    assert fig.layout.showlegend is True
    # PASS keeps the good-status color and leads the order.
    assert fig.data[0].name == 'PASS'
    assert fig.data[0].marker.color == config.STATUS_GOOD


def test_verdict_composition_handles_all_zero_counts():
    fig = charts.verdict_composition({'PASS': 0, 'FAIL': 0})
    assert len(fig.data) == 0
    assert fig.layout.annotations


def test_lifecycle_uses_distinct_symbols_per_event_type(all_inputs):
    """Promotion and demotion are green-up and red-down: the one pair a
    colorblind reader most needs the shape for."""
    fig = charts.lifecycle_timeline(all_inputs['lifecycle'])
    symbols = {t.name: t.marker.symbol for t in fig.data}
    assert symbols['Promoted'] == 'triangle-up'
    assert symbols['Demoted'] == 'triangle-down'
    assert fig.layout.showlegend is True


def test_asset_class_bar_drops_rows_with_no_pnl():
    fig = charts.asset_class_pnl_bar([
        {'strategy': 'a', 'class': 'CRYPTO', 'pnl_per_trade': -0.05, 'trades': 100},
        {'strategy': 'b', 'class': 'CRYPTO', 'pnl_per_trade': None, 'trades': 0},
        {'strategy': 'c', 'class': 'CRYPTO', 'pnl_per_trade': float('nan'), 'trades': 0},
    ])
    assert len(fig.data[0].y) == 1


# --------------------------------------------------------------------------
# Formatting helpers (imported here rather than in the db test because they
# are the presentation half of the same contract)
# --------------------------------------------------------------------------

def _ui():
    """`components` imports streamlit, which is a heavy import; keep it inside
    the tests that need it so the chart tests stay fast."""
    from dashboard import components
    return components


def test_none_renders_as_na_never_as_zero():
    """A zero and an unknown look nothing alike in a trading context, and a
    dashboard that renders them identically is one nobody trusts twice."""
    ui = _ui()
    for fn in (ui.fmt_money, ui.fmt_pct, ui.fmt_num, ui.fmt_int, ui.fmt_ts):
        assert fn(None) == ui.NA
        assert fn(float('nan')) == ui.NA


def test_infinity_renders_as_a_glyph_not_a_number():
    """A profit factor with no losing trade really is infinite (convention 12).
    Squashing it to a large float would be a lie with a decimal point on it."""
    ui = _ui()
    assert ui.fmt_num(math.inf) == '∞'
    assert ui.fmt_num(-math.inf) == '-∞'
    assert ui.fmt_money(math.inf) == '∞'


def test_signed_formats_carry_an_explicit_sign():
    ui = _ui()
    assert ui.fmt_money(12.5, signed=True) == '+12.50'
    assert ui.fmt_money(-12.5, signed=True) == '-12.50'
    assert ui.fmt_pct(-0.2512, 2, signed=True) == '-25.12%'


def test_status_badge_carries_a_glyph_and_a_word():
    """Color is the third channel on the status badge, never the first."""
    ui = _ui()
    for state in ('ALIVE', 'HALTED', 'STALE', 'IDLE'):
        badge = ui.status_badge(state)
        color, glyph, _ = ui.STATE_STYLE[state]
        assert glyph in badge
        assert state in badge
        assert color in badge


def test_trade_glyphs_cover_every_status_db_reader_can_emit():
    """`_trade_status` and the glyph table have to stay in step, or a status
    silently renders as the fallback dash."""
    ui = _ui()
    emitted = set()
    for closed, pnl in [(None, None), (1, 5.0), (1, -5.0), (1, 0.0), (1, None)]:
        emitted.add(db_reader._trade_status(pd.Series({'closed_ts': closed, 'pnl_net': pnl})))
    assert emitted <= set(ui.TRADE_GLYPH), 'un-glyphed status: {}'.format(
        emitted - set(ui.TRADE_GLYPH))
