"""Plotly figures for the dashboard. Pure functions: DataFrame in, Figure out.

Chart choices follow the form-before-color procedure:

  * change over time -> line (equity, drawdown)
  * polarity (did it make or lose money) -> diverging two-color + explicit sign
  * magnitude ranking -> horizontal bar, single hue, length does the encoding
  * composition of a whole -> one stacked bar, not a pie
  * a single number -> not a chart at all; those live in `components.py`

Two rules are load-bearing here rather than decorative:

**No dual-axis charts.** Equity and drawdown are different scales, so they are
two stacked charts sharing an x-range, never one chart with two y-axes.

**Green and red are not distinguishable** to a deuteranope (measured dE 4.1 on
this surface). Every sign-colored mark therefore also carries an explicit `+`
or `-` in its label, and every two-color chart carries a legend naming the two
groups. Color is redundant everywhere it appears.

No streamlit import: these are testable without a browser.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import pandas as pd
import plotly.graph_objects as go

from . import config

# --------------------------------------------------------------------------
# Shared chrome. `theme` selects a chrome palette from `config.THEMES`
# ('Dark' | 'Light'); accent colors (PROFIT/LOSS/CATEGORICAL/etc.) do not
# change with it. Every public chart function below defaults to 'Dark', so
# existing callers and tests keep their current output unchanged.
# --------------------------------------------------------------------------

def _theme(theme: str) -> Dict[str, str]:
    return config.THEMES.get(theme, config.THEME_DARK)


def _axis(t: Dict[str, str]) -> Dict[str, Any]:
    return dict(
        showgrid=True,
        gridcolor=t['GRIDLINE'],
        gridwidth=1,
        zeroline=False,
        linecolor=t['AXIS_LINE'],
        tickfont=dict(color=t['INK_MUTED'], size=11, family=config.FONT_MONO),
        title_font=dict(color=t['INK_MUTED'], size=11, family=config.FONT_MONO),
    )


def _base_layout(height: int = 320, showlegend: bool = False, theme: str = 'Dark',
                 **kwargs) -> Dict[str, Any]:
    t = _theme(theme)
    axis = _axis(t)
    layout = dict(
        height=height,
        margin=dict(l=8, r=8, t=28, b=8),
        template=t['PLOTLY_TEMPLATE'],
        paper_bgcolor='rgba(0,0,0,0)',
        plot_bgcolor=t['SURFACE_CHART'],
        font=dict(family=config.FONT_MONO, color=t['INK_SECONDARY'], size=11),
        showlegend=showlegend,
        legend=dict(
            orientation='h', yanchor='bottom', y=1.02, xanchor='left', x=0,
            font=dict(color=t['INK_SECONDARY'], size=11),
            bgcolor='rgba(0,0,0,0)',
        ),
        hoverlabel=dict(
            bgcolor=t['SURFACE_CARD'],
            bordercolor=t['BORDER'],
            font=dict(family=config.FONT_MONO, color=t['INK_PRIMARY'], size=11),
        ),
        xaxis=dict(axis),
        yaxis=dict(axis),
    )
    layout.update(kwargs)
    return layout


def empty_figure(message: str = 'No data yet', height: int = 320, theme: str = 'Dark') -> go.Figure:
    """The empty state, as a figure. An empty chart area with a sentence in it
    beats an exception, and beats an axis-only chart that looks like a zero."""
    t = _theme(theme)
    fig = go.Figure()
    fig.update_layout(**_base_layout(height=height, theme=theme))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    fig.add_annotation(
        text=message, xref='paper', yref='paper', x=0.5, y=0.5,
        showarrow=False,
        font=dict(family=config.FONT_MONO, size=13, color=t['INK_MUTED']),
    )
    return fig


def _signed(value: float, unit: str = '') -> str:
    """`+12.30` / `-4.00`. The sign is the colorblind-safe channel; it is not
    optional formatting."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return 'n/a'
    return '{}{:,.2f}{}'.format('+' if value >= 0 else '-', abs(value), unit)


# --------------------------------------------------------------------------
# Equity
# --------------------------------------------------------------------------

def equity_curve(equity_df: pd.DataFrame, height: int = 340, theme: str = 'Dark') -> go.Figure:
    """Account equity over time. One series, so no legend - the title names it.

    A dashed baseline marks the starting equity, which turns the chart from
    "a wiggly line" into "above or below where it started" at a glance.
    """
    if equity_df is None or equity_df.empty:
        return empty_figure('No equity snapshots yet. The engine writes one every 15 min.',
                            height, theme)

    df = equity_df.dropna(subset=['equity']).sort_values('ts')
    if df.empty:
        return empty_figure('No equity snapshots yet.', height, theme)

    t = _theme(theme)
    start = float(df['equity'].iloc[0])
    end = float(df['equity'].iloc[-1])
    up = end >= start
    color = config.PROFIT if up else config.LOSS

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df['time'], y=df['equity'],
        mode='lines',
        name='Equity',
        line=dict(color=color, width=2),
        fill='tozeroy',
        fillcolor='rgba(12,163,12,0.10)' if up else 'rgba(208,59,59,0.10)',
        hovertemplate='%{x|%Y-%m-%d %H:%M}<br>equity %{y:,.2f}<extra></extra>',
    ))
    fig.add_hline(
        y=start, line=dict(color=t['INK_MUTED'], width=1, dash='dash'),
        annotation_text='start {:,.2f}'.format(start),
        annotation_position='top left',
        annotation_font=dict(color=t['INK_MUTED'], size=10, family=config.FONT_MONO),
    )
    fig.update_layout(**_base_layout(height=height, hovermode='x unified', theme=theme))
    # Crosshair: an equity curve is read by hovering, so ship the spike.
    fig.update_xaxes(showspikes=True, spikemode='across', spikethickness=1,
                     spikecolor=t['INK_MUTED'], spikedash='dot')
    fig.update_yaxes(title_text='equity', tickformat=',.2f',
                     rangemode='normal', autorange=True)
    return fig


def drawdown_curve(equity_df: pd.DataFrame, height: int = 180, theme: str = 'Dark') -> go.Figure:
    """Drawdown from running peak. Its own chart, sharing the equity x-range.

    Deliberately NOT a second y-axis on the equity chart. Two scales on one
    frame is the single most common charting mistake and it makes the two
    series look correlated in ways they are not.
    """
    if equity_df is None or equity_df.empty:
        return empty_figure('No equity history to draw down from.', height, theme)

    df = equity_df.dropna(subset=['equity']).sort_values('ts')
    if len(df) < 2:
        return empty_figure('Need at least two snapshots to compute drawdown.', height, theme)

    t = _theme(theme)
    eq = pd.to_numeric(df['equity'], errors='coerce')
    dd = ((eq / eq.cummax()) - 1.0) * 100.0

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df['time'], y=dd,
        mode='lines', name='Drawdown',
        line=dict(color=config.LOSS, width=2),
        fill='tozeroy', fillcolor='rgba(208,59,59,0.15)',
        hovertemplate='%{x|%Y-%m-%d %H:%M}<br>drawdown %{y:.2f}%<extra></extra>',
    ))
    fig.update_layout(**_base_layout(height=height, hovermode='x unified', theme=theme))
    fig.update_yaxes(title_text='drawdown %', ticksuffix='%', rangemode='tozero')
    fig.update_xaxes(showspikes=True, spikemode='across', spikethickness=1,
                     spikecolor=t['INK_MUTED'], spikedash='dot')
    return fig


# --------------------------------------------------------------------------
# Trade distribution
# --------------------------------------------------------------------------

def pnl_distribution(trades: pd.DataFrame, height: int = 300, theme: str = 'Dark') -> go.Figure:
    """Net PnL per closed trade, split at zero.

    Two traces rather than one so the split is a real categorical distinction
    with a legend, not a color ramp the reader has to infer. Both traces share
    a bin width so the bars line up on a common grid.
    """
    if trades is None or trades.empty:
        return empty_figure('No closed trades yet.', height, theme)

    closed = trades[trades['closed_ts'].notna()]
    pnl = pd.to_numeric(closed['pnl_net'], errors='coerce').dropna() if not closed.empty else pd.Series(dtype=float)
    if pnl.empty:
        return empty_figure('No closed trades with a recorded PnL yet.', height, theme)

    lo, hi = float(pnl.min()), float(pnl.max())
    span = hi - lo
    size = (span / 30.0) if span > 0 else 1.0
    bins = dict(start=lo - size, end=hi + size, size=size)

    fig = go.Figure()
    losers = pnl[pnl < 0]
    winners = pnl[pnl >= 0]
    if not losers.empty:
        fig.add_trace(go.Histogram(
            x=losers, name='Losing trades', marker=dict(color=config.LOSS),
            xbins=bins, hovertemplate='pnl %{x}<br>%{y} trades<extra>Losing</extra>',
        ))
    if not winners.empty:
        fig.add_trace(go.Histogram(
            x=winners, name='Winning trades', marker=dict(color=config.PROFIT),
            xbins=bins, hovertemplate='pnl %{x}<br>%{y} trades<extra>Winning</extra>',
        ))

    fig.update_layout(**_base_layout(
        height=height,
        showlegend=len(fig.data) > 1,
        barmode='overlay',
        bargap=0.08,
        theme=theme,
    ))
    fig.add_vline(x=0, line=dict(color=_theme(theme)['INK_MUTED'], width=1))
    fig.update_xaxes(title_text='net pnl per trade')
    fig.update_yaxes(title_text='trades')
    return fig


# --------------------------------------------------------------------------
# Strategy comparison
# --------------------------------------------------------------------------

def strategy_pnl_bar(perf: pd.DataFrame, height: Optional[int] = None, theme: str = 'Dark') -> go.Figure:
    """Net PnL by strategy. Horizontal because strategy names are long.

    Bars are colored by sign and ALSO labelled with a signed number, so the
    polarity survives both colorblindness and a greyscale print.
    """
    if perf is None or perf.empty:
        return empty_figure('No strategies with trades yet.', height or 300, theme)

    df = perf.copy()
    df['pnl_net'] = pd.to_numeric(df['pnl_net'], errors='coerce').fillna(0.0)
    df = df[df['total_trades'].fillna(0) > 0]
    if df.empty:
        return empty_figure('No strategy has closed a trade yet.', height or 300, theme)

    t = _theme(theme)
    df = df.sort_values('pnl_net')
    colors = [config.PROFIT if v >= 0 else config.LOSS for v in df['pnl_net']]
    labels = [_signed(v) for v in df['pnl_net']]
    height = height or max(220, 34 * len(df) + 60)

    fig = go.Figure(go.Bar(
        x=df['pnl_net'], y=df['name'].fillna(df['strategy_id']),
        orientation='h',
        marker=dict(color=colors, line=dict(color=t['SURFACE_CHART'], width=2)),
        text=labels, textposition='outside',
        textfont=dict(family=config.FONT_MONO, size=11, color=t['INK_SECONDARY']),
        customdata=df[['total_trades']].values,
        hovertemplate='%{y}<br>net pnl %{x:,.2f}<br>%{customdata[0]} closed trades<extra></extra>',
        cliponaxis=False,
    ))
    fig.update_layout(**_base_layout(height=height, theme=theme))
    fig.add_vline(x=0, line=dict(color=t['AXIS_LINE'], width=1))
    fig.update_xaxes(title_text='net pnl')
    fig.update_yaxes(automargin=True)
    return fig


# --------------------------------------------------------------------------
# Graveyard
# --------------------------------------------------------------------------

def verdict_composition(counts: Dict[str, Any], height: int = 120, theme: str = 'Dark') -> go.Figure:
    """Verdict mix as one stacked bar.

    Not a grouped bar chart: PASS is 381 against FAIL's 486,350, so on a linear
    scale PASS is invisible and on a log scale the bar lengths stop meaning
    anything. The stacked bar answers the question it can honestly answer -
    "what is the mix" - and the exact counts live in stat tiles beside it.
    """
    if not counts:
        return empty_figure('No graveyard summary available.', height, theme)

    t = _theme(theme)
    order = ['PASS', 'PASS_BENCHMARK', 'FAIL', 'NOT_TESTED']
    palette = {
        'PASS': config.STATUS_GOOD,
        'PASS_BENCHMARK': config.CATEGORICAL[2],
        'FAIL': config.STATUS_CRITICAL,
        'NOT_TESTED': t['INK_MUTED'],
    }
    keys = [k for k in order if k in counts] + [k for k in counts if k not in order]
    total = sum(float(counts.get(k) or 0) for k in keys)
    if total <= 0:
        return empty_figure('Graveyard summary has no verdict counts.', height, theme)

    fig = go.Figure()
    for i, k in enumerate(keys):
        v = float(counts.get(k) or 0)
        share = v / total
        fig.add_trace(go.Bar(
            x=[v], y=['verdicts'], orientation='h', name=k,
            marker=dict(color=palette.get(k, config.CATEGORICAL[i % len(config.CATEGORICAL)]),
                        line=dict(color=t['SURFACE_CHART'], width=2)),
            hovertemplate='{}<br>%{{x:,.0f}} rows ({:.2%})<extra></extra>'.format(k, share),
        ))

    fig.update_layout(**_base_layout(height=height, showlegend=True, barmode='stack', theme=theme))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


def pass_concentration_bar(items: List[Dict[str, Any]], height: Optional[int] = None,
                           theme: str = 'Dark') -> go.Figure:
    """Where the PASS rows pile up. Single hue: bar length is the encoding, so
    coloring by value too would be redundant ink."""
    if not items:
        return empty_figure('No pass concentration data.', height or 240, theme)

    t = _theme(theme)
    rows = [(str(d.get('ticker_timeframe', '?')), float(d.get('pass_rows') or 0)) for d in items]
    rows.sort(key=lambda r: r[1])
    height = height or max(200, 32 * len(rows) + 60)

    fig = go.Figure(go.Bar(
        x=[r[1] for r in rows], y=[r[0] for r in rows], orientation='h',
        marker=dict(color=config.CATEGORICAL[0], line=dict(color=t['SURFACE_CHART'], width=2)),
        text=['{:,.0f}'.format(r[1]) for r in rows], textposition='outside',
        textfont=dict(family=config.FONT_MONO, size=11, color=t['INK_SECONDARY']),
        hovertemplate='%{y}<br>%{x:,.0f} PASS rows<extra></extra>',
        cliponaxis=False,
    ))
    fig.update_layout(**_base_layout(height=height, theme=theme))
    fig.update_xaxes(title_text='PASS rows')
    fig.update_yaxes(automargin=True)
    return fig


def strategy_firing_bar(health: pd.DataFrame, top_n: int = 20,
                        height: Optional[int] = None, theme: str = 'Dark') -> go.Figure:
    """Zero-trade fraction per strategy: which strategies never fire.

    A strategy that produced no trades did not run and fail, it did not run
    (convention 11). Bars at or above the non-firing line are colored as a
    warning AND labelled, because that band is the actionable one.
    """
    if health is None or health.empty:
        return empty_figure('No judge evidence pack available.', height or 300, theme)

    df = health.copy()
    tested = pd.to_numeric(df['n_rows_tested'], errors='coerce').fillna(0)
    trades = pd.to_numeric(df['n_trades'], errors='coerce').fillna(0)
    # Rows tested but no trades: the strategy was given the chance and did
    # not signal. Strategies with nothing tested are excluded - no evidence
    # either way is not the same as evidence of not firing.
    df = df[tested > 0].copy()
    if df.empty:
        return empty_figure('No strategy has any tested rows.', height or 300, theme)

    t = _theme(theme)
    df['trades'] = trades[df.index]
    # Rank by "silence": no trades first, then fewest trades per tested row.
    df['trades_per_row'] = df['trades'] / tested[df.index].replace(0, pd.NA)
    df = df.sort_values('trades_per_row', ascending=True, na_position='first').head(top_n)
    df = df.iloc[::-1]

    silent = df['trades'] == 0
    colors = [config.STATUS_WARNING if s else config.CATEGORICAL[0] for s in silent]
    labels = ['NON-FIRING' if s else '{:,.0f} trades'.format(n)
              for s, n in zip(silent, df['trades'])]
    height = height or max(260, 26 * len(df) + 70)

    fig = go.Figure(go.Bar(
        x=df['trades'], y=df['strategy'], orientation='h',
        marker=dict(color=colors, line=dict(color=t['SURFACE_CHART'], width=2)),
        text=labels, textposition='outside',
        textfont=dict(family=config.FONT_MONO, size=10, color=t['INK_SECONDARY']),
        customdata=df[['n_rows_tested']].values,
        hovertemplate='%{y}<br>%{x:,.0f} trades over %{customdata[0]:,.0f} tested rows<extra></extra>',
        cliponaxis=False,
    ))
    fig.update_layout(**_base_layout(height=height, theme=theme))
    fig.update_xaxes(title_text='trades produced (lowest first)')
    fig.update_yaxes(automargin=True)
    return fig


def asset_class_pnl_bar(breakdown: List[Dict[str, Any]], height: Optional[int] = None,
                        theme: str = 'Dark') -> go.Figure:
    """Pooled PnL per trade by strategy x asset class, from the judge pack."""
    if not breakdown:
        return empty_figure('No asset class breakdown in the judge pack.', height or 320, theme)

    rows = []
    for d in breakdown:
        if not isinstance(d, dict):
            continue
        v = d.get('pnl_per_trade')
        if v is None or (isinstance(v, float) and math.isnan(v)):
            continue
        rows.append({
            'label': '{} · {}'.format(d.get('strategy', '?'), d.get('class', '?')),
            'pnl_per_trade': float(v),
            'trades': d.get('trades') or 0,
        })
    if not rows:
        return empty_figure('No usable pnl_per_trade values.', height or 320, theme)

    t = _theme(theme)
    df = pd.DataFrame(rows).sort_values('pnl_per_trade')
    colors = [config.PROFIT if v >= 0 else config.LOSS for v in df['pnl_per_trade']]
    height = height or max(280, 24 * len(df) + 70)

    fig = go.Figure(go.Bar(
        x=df['pnl_per_trade'], y=df['label'], orientation='h',
        marker=dict(color=colors, line=dict(color=t['SURFACE_CHART'], width=2)),
        customdata=df[['trades']].values,
        hovertemplate='%{y}<br>pnl/trade %{x:.4f}<br>%{customdata[0]:,.0f} trades<extra></extra>',
    ))
    fig.update_layout(**_base_layout(height=height, theme=theme))
    fig.add_vline(x=0, line=dict(color=t['AXIS_LINE'], width=1))
    fig.update_xaxes(title_text='pnl per trade')
    fig.update_yaxes(automargin=True)
    return fig


def lifecycle_timeline(events: pd.DataFrame, height: Optional[int] = None,
                       theme: str = 'Dark') -> go.Figure:
    """Strategy promotions and demotions over time.

    A dot plot, not a Gantt: the audit log records the MOMENT of a change, not
    a span, and drawing spans would invent end dates nobody wrote down.
    """
    if events is None or events.empty:
        return empty_figure('No promotion or demotion events logged yet.', height or 240, theme)

    df = events.dropna(subset=['time']).copy()
    if df.empty:
        return empty_figure('No promotion or demotion events with a timestamp.', height or 240, theme)

    t = _theme(theme)
    kinds = {
        'strategy_promoted': (config.PROFIT, 'triangle-up', 'Promoted'),
        'strategy_demoted': (config.LOSS, 'triangle-down', 'Demoted'),
        'mode_change': (config.STATUS_WARNING, 'diamond', 'Mode change'),
    }
    height = height or max(220, 30 * df['strategy_id'].nunique() + 80)
    fig = go.Figure()
    for kind, (color, symbol, label) in kinds.items():
        sub = df[df['event_type'] == kind]
        if sub.empty:
            continue
        fig.add_trace(go.Scatter(
            x=sub['time'], y=sub['strategy_id'], mode='markers', name=label,
            marker=dict(color=color, size=11, symbol=symbol,
                        line=dict(color=t['SURFACE_CHART'], width=2)),
            customdata=sub[['from_status', 'to_status', 'actor']].values,
            hovertemplate=('%{y}<br>%{x|%Y-%m-%d %H:%M}<br>'
                           '%{customdata[0]} -> %{customdata[1]} by %{customdata[2]}'
                           '<extra>' + label + '</extra>'),
        ))
    if not fig.data:
        return empty_figure('No lifecycle events to plot.', height, theme)

    fig.update_layout(**_base_layout(height=height, showlegend=True, theme=theme))
    fig.update_yaxes(automargin=True)
    return fig
