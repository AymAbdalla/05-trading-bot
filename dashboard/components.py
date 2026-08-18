"""Reusable Streamlit UI pieces: CSS, metric cards, badges, tables, empty states.

This is the only module besides `app.py` that imports streamlit.

The formatting helpers carry a rule worth stating once: **a value the reader
cannot verify is never invented.** `None` renders as a dim `n/a`, not as `0.00`.
A zero and an unknown look nothing alike in a trading context, and a dashboard
that renders them identically is the reason people stop trusting dashboards.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st

from . import config

# --------------------------------------------------------------------------
# Global CSS. Compact, monospaced, dark - a terminal, not a report.
# --------------------------------------------------------------------------

CSS = """
<style>
  :root {{
    --surface-page: {page};
    --surface-card: {card};
    --surface-chart: {chart};
    --ink-primary: {ink};
    --ink-secondary: {ink2};
    --ink-muted: {muted};
    --border: {border};
    --profit: {profit};
    --loss: {loss};
    --warn: {warn};
  }}

  .stApp {{ background: var(--surface-page); }}

  /* Numbers align or they lie. Tabular figures everywhere numeric. */
  .stApp, .stMarkdown, .stDataFrame, div[data-testid="stMetricValue"] {{
    font-family: {mono};
  }}

  /* Trading terminals are dense. Claw back Streamlit's default padding. */
  .block-container {{ padding-top: 2.2rem; padding-bottom: 2rem; max-width: 1600px; }}
  div[data-testid="stVerticalBlock"] {{ gap: 0.55rem; }}

  h1, h2, h3, h4 {{
    font-family: {mono};
    color: var(--ink-primary);
    letter-spacing: -0.01em;
  }}
  h1 {{ font-size: 1.35rem !important; margin-bottom: 0.1rem; }}
  h2 {{ font-size: 1.05rem !important; }}
  h3 {{ font-size: 0.95rem !important; }}

  .sect {{
    font-size: 0.72rem;
    letter-spacing: 0.13em;
    text-transform: uppercase;
    color: var(--ink-muted);
    border-bottom: 1px solid var(--border);
    padding-bottom: 0.3rem;
    margin: 0.9rem 0 0.5rem 0;
  }}

  /* Metric card */
  .card {{
    background: var(--surface-card);
    border: 1px solid var(--border);
    border-radius: 6px;
    padding: 0.6rem 0.75rem;
    height: 100%;
  }}
  .card .label {{
    font-size: 0.66rem;
    letter-spacing: 0.11em;
    text-transform: uppercase;
    color: var(--ink-muted);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }}
  .card .value {{
    font-size: 1.28rem;
    font-variant-numeric: tabular-nums;
    color: var(--ink-primary);
    line-height: 1.35;
  }}
  .card .sub {{ font-size: 0.68rem; color: var(--ink-muted); }}
  .value.pos {{ color: var(--profit); }}
  .value.neg {{ color: var(--loss); }}
  .value.warn {{ color: var(--warn); }}
  .value.na {{ color: var(--ink-muted); }}

  /* Status badge. Always ships a glyph + a word, never color alone. */
  .badge {{
    display: inline-block;
    font-size: 0.72rem;
    letter-spacing: 0.09em;
    padding: 0.18rem 0.55rem;
    border-radius: 4px;
    border: 1px solid currentColor;
    font-variant-numeric: tabular-nums;
  }}

  .note {{
    font-size: 0.72rem;
    color: var(--ink-muted);
    border-left: 2px solid var(--border);
    padding: 0.15rem 0 0.15rem 0.6rem;
    margin: 0.3rem 0;
  }}

  .empty {{
    border: 1px dashed var(--border);
    border-radius: 6px;
    padding: 1.4rem;
    text-align: center;
    color: var(--ink-muted);
    font-size: 0.85rem;
  }}

  /* Log lines */
  .logline {{
    font-size: 0.74rem;
    color: var(--ink-secondary);
    padding: 0.16rem 0;
    border-bottom: 1px solid rgba(255,255,255,0.045);
    white-space: pre-wrap;
    word-break: break-word;
  }}
  .logline .ts {{ color: var(--ink-muted); }}
  .logline .who {{ color: {slot1}; }}

  .stTabs [data-baseweb="tab-list"] {{ gap: 0.15rem; overflow-x: auto; }}
  .stTabs [data-baseweb="tab"] {{
    font-family: {mono};
    font-size: 0.8rem;
    padding: 0.35rem 0.75rem;
  }}
  .stDataFrame {{ font-size: 0.78rem; }}

  /* Phone: stat cards get smaller, charts keep their height. */
  @media (max-width: 640px) {{
    .block-container {{ padding-left: 0.6rem; padding-right: 0.6rem; }}
    .card .value {{ font-size: 1.02rem; }}
    .card .label {{ font-size: 0.6rem; }}
    h1 {{ font-size: 1.1rem !important; }}
  }}
</style>
"""


def inject_css() -> None:
    st.markdown(
        CSS.format(
            page=config.SURFACE_PAGE, card=config.SURFACE_CARD, chart=config.SURFACE_CHART,
            ink=config.INK_PRIMARY, ink2=config.INK_SECONDARY, muted=config.INK_MUTED,
            border=config.BORDER, profit=config.PROFIT, loss=config.LOSS,
            warn=config.OPEN, mono=config.FONT_MONO, slot1=config.CATEGORICAL[0],
        ),
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------

NA = 'n/a'


def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float) and math.isnan(v):
        return True
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def fmt_money(v: Any, decimals: int = 2, signed: bool = False) -> str:
    if _is_missing(v):
        return NA
    v = float(v)
    if math.isinf(v):
        return '∞' if v > 0 else '-∞'
    if signed:
        return '{}{:,.{d}f}'.format('+' if v >= 0 else '-', abs(v), d=decimals)
    return '{:,.{d}f}'.format(v, d=decimals)


def fmt_pct(v: Any, decimals: int = 1, signed: bool = False) -> str:
    """`v` is a FRACTION (0.42 -> 42.0%)."""
    if _is_missing(v):
        return NA
    v = float(v) * 100.0
    if math.isinf(v):
        return '∞'
    if signed:
        return '{}{:.{d}f}%'.format('+' if v >= 0 else '-', abs(v), d=decimals)
    return '{:.{d}f}%'.format(v, d=decimals)


def fmt_num(v: Any, decimals: int = 2) -> str:
    """Ratios: Sharpe, profit factor, R.

    `inf` prints as the glyph. A profit factor with no losing trade really is
    infinite (convention 12) and squashing it to a big number would be a lie
    with a decimal point on it.
    """
    if _is_missing(v):
        return NA
    v = float(v)
    if math.isinf(v):
        return '∞' if v > 0 else '-∞'
    return '{:,.{d}f}'.format(v, d=decimals)


def fmt_int(v: Any) -> str:
    if _is_missing(v):
        return NA
    return '{:,.0f}'.format(float(v))


def fmt_ts(v: Any, fmt: str = '%Y-%m-%d %H:%M:%S') -> str:
    if _is_missing(v):
        return NA
    try:
        return pd.to_datetime(v).strftime(fmt)
    except (ValueError, TypeError):
        return str(v)


def _tone(v: Any, invert: bool = False) -> str:
    """CSS class for a signed value. Paired with a signed string everywhere,
    never used as the only channel."""
    if _is_missing(v):
        return 'na'
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ''
    if math.isnan(f):
        return 'na'
    if f == 0:
        return ''
    positive = f > 0
    if invert:
        positive = not positive
    return 'pos' if positive else 'neg'


# --------------------------------------------------------------------------
# Cards, badges, empty states
# --------------------------------------------------------------------------

def metric_card(label: str, value: str, sub: str = '', tone: str = '') -> str:
    cls = ('value ' + tone).strip()
    sub_html = '<div class="sub">{}</div>'.format(sub) if sub else ''
    return (
        '<div class="card"><div class="label">{label}</div>'
        '<div class="{cls}">{value}</div>{sub}</div>'
    ).format(label=label, cls=cls, value=value, sub=sub_html)


def metric_row(cards: List[Dict[str, str]], per_row: int = 6) -> None:
    """Render metric cards in rows of `per_row`. Streamlit stacks columns on a
    narrow viewport, so this is already phone-friendly."""
    for i in range(0, len(cards), per_row):
        chunk = cards[i:i + per_row]
        cols = st.columns(len(chunk), gap='small')
        for col, card in zip(cols, chunk):
            with col:
                st.markdown(
                    metric_card(card.get('label', ''), card.get('value', NA),
                                card.get('sub', ''), card.get('tone', '')),
                    unsafe_allow_html=True,
                )


def signed_metric(label: str, value: Any, sub: str = '', money: bool = True) -> Dict[str, str]:
    """A metric card whose sign is carried by BOTH the glyph and the color."""
    text = fmt_money(value, signed=True) if money else fmt_num(value)
    return {'label': label, 'value': text, 'sub': sub, 'tone': _tone(value)}


#: state -> (color, glyph, explanation). The glyph and the word are the
#: accessible channels; the color is the third, redundant one.
STATE_STYLE = {
    'ALIVE': (config.STATUS_GOOD, '●', 'writing equity snapshots'),
    'HALTED': (config.STATUS_CRITICAL, '■', 'kill switch engaged'),
    'STALE': (config.STATUS_WARNING, '▲', 'no recent snapshot'),
    'IDLE': (config.INK_MUTED, '○', 'has not run'),
}


def status_badge(state: str, detail: str = '') -> str:
    color, glyph, _ = STATE_STYLE.get(state, (config.INK_MUTED, '○', ''))
    suffix = ' <span style="color:{}">{}</span>'.format(config.INK_MUTED, detail) if detail else ''
    return (
        '<span class="badge" style="color:{c}">{g} {s}</span>{suffix}'
    ).format(c=color, g=glyph, s=state, suffix=suffix)


def section(title: str) -> None:
    st.markdown('<div class="sect">{}</div>'.format(title), unsafe_allow_html=True)


def note(text: str) -> None:
    st.markdown('<div class="note">{}</div>'.format(text), unsafe_allow_html=True)


def empty_state(message: str, hint: str = '') -> None:
    hint_html = '<div style="font-size:0.72rem;margin-top:0.35rem">{}</div>'.format(hint) if hint else ''
    st.markdown(
        '<div class="empty">{}{}</div>'.format(message, hint_html),
        unsafe_allow_html=True,
    )


# --------------------------------------------------------------------------
# Tables
# --------------------------------------------------------------------------

#: status -> glyph. The trade table is color-coded AND glyph-coded because
#: green/red is the one pair a red-green colorblind reader cannot separate.
TRADE_GLYPH = {'WIN': '▲', 'LOSS': '▼', 'OPEN': '●', 'FLAT': '–'}

_TONE_HEX = {'pos': config.PROFIT, 'neg': config.LOSS, 'open': config.OPEN}


def _style_trades(df: pd.DataFrame):
    """Color the PnL and status columns. Returns a pandas Styler."""
    def color_pnl(v):
        if _is_missing(v):
            return 'color: {}'.format(config.INK_MUTED)
        try:
            f = float(v)
        except (TypeError, ValueError):
            return ''
        if f > 0:
            return 'color: {}'.format(config.PROFIT)
        if f < 0:
            return 'color: {}'.format(config.LOSS)
        return 'color: {}'.format(config.INK_SECONDARY)

    def color_status(v):
        text = str(v)
        if 'WIN' in text:
            return 'color: {}'.format(config.PROFIT)
        if 'LOSS' in text:
            return 'color: {}'.format(config.LOSS)
        if 'OPEN' in text:
            return 'color: {}'.format(config.OPEN)
        return 'color: {}'.format(config.INK_MUTED)

    styler = df.style
    for col in ('pnl_net', 'pnl_gross', 'r_multiple', 'pnl'):
        if col in df.columns:
            styler = styler.map(color_pnl, subset=[col])
    if 'status' in df.columns:
        styler = styler.map(color_status, subset=['status'])
    return styler


def trade_table(df: pd.DataFrame, height: int = 420) -> None:
    """The trade log, formatted for reading rather than for machines."""
    if df is None or df.empty:
        empty_state('No trades yet.',
                    'Positions appear here as soon as the engine opens one.')
        return

    view = pd.DataFrame({
        'opened': df['opened_at'].apply(lambda v: fmt_ts(v, '%m-%d %H:%M:%S')),
        'closed': df['closed_at'].apply(lambda v: fmt_ts(v, '%m-%d %H:%M:%S')),
        'market': df['pair'],
        'class': df['asset_class'],
        'strategy': df['strategy_id'],
        'side': df['side'],
        'entry': df['entry_px'].apply(lambda v: fmt_money(v, 4)),
        'exit': df['exit_px'].apply(lambda v: fmt_money(v, 4)),
        'qty': df['qty'].apply(lambda v: fmt_money(v, 4)),
        'stop': df['stop_px'].apply(lambda v: fmt_money(v, 4)),
        'pnl_net': pd.to_numeric(df['pnl_net'], errors='coerce'),
        'R': pd.to_numeric(df['r_multiple'], errors='coerce'),
        'exit_reason': df['exit_reason'].fillna('-'),
        'status': [
            '{} {}'.format(TRADE_GLYPH.get(s, '–'), s) for s in df['status']
        ],
        'mode': df['mode'],
    })
    st.dataframe(
        _style_trades(view).format({'pnl_net': '{:+,.2f}', 'R': '{:+.2f}'}, na_rep=NA),
        width='stretch', height=height, hide_index=True,
    )


def order_table(df: pd.DataFrame, height: int = 280) -> None:
    if df is None or df.empty:
        empty_state('No orders yet.',
                    'Rejected and cancelled orders show up here too.')
        return
    view = pd.DataFrame({
        'time': df['time'].apply(lambda v: fmt_ts(v, '%m-%d %H:%M:%S')),
        'market': df['pair'],
        'side': df['side'],
        'type': df['type'],
        'qty': df['qty'].apply(lambda v: fmt_money(v, 4)),
        'limit': df['limit_price'].apply(lambda v: fmt_money(v, 4)),
        'stop': df['stop_price'].apply(lambda v: fmt_money(v, 4)),
        'filled': df['filled_qty'].apply(lambda v: fmt_money(v, 4)),
        'avg_fill': df['avg_fill_px'].apply(lambda v: fmt_money(v, 4)),
        'fees': df['fees'].apply(lambda v: fmt_money(v, 4)),
        'status': df['status'],
        'mode': df['mode'],
    })
    st.dataframe(view, width='stretch', height=height, hide_index=True)


def strategy_table(df: pd.DataFrame, height: int = 380) -> None:
    if df is None or df.empty:
        empty_state('No strategies registered.',
                    'The registry fills in when the engine or Quant promotes one.')
        return
    view = pd.DataFrame({
        'strategy': df['name'].fillna(df['strategy_id']),
        'status': df['status'],
        'class': df['asset_class'],
        'trades': df['total_trades'].apply(fmt_int),
        'open': df['open_trades'].apply(fmt_int),
        'win_rate': df['win_rate'].apply(lambda v: fmt_pct(v)),
        'pnl_net': pd.to_numeric(df['pnl_net'], errors='coerce'),
        'avg_R': pd.to_numeric(df['avg_r'], errors='coerce'),
        'sharpe_trade': df['sharpe_trade'].apply(lambda v: fmt_num(v, 3)),
        'profit_factor': df['profit_factor'].apply(lambda v: fmt_num(v, 2)),
    })
    st.dataframe(
        _style_trades(view).format({'pnl_net': '{:+,.2f}', 'avg_R': '{:+.2f}'}, na_rep=NA),
        width='stretch', height=height, hide_index=True,
    )


def log_lines(df: pd.DataFrame, summarize, ts_col: str = 'time',
              who_col: str = 'actor', what_col: str = 'event_type',
              payload_col: str = 'payload_json', limit: int = 200) -> None:
    """Render an event table as terminal log lines rather than a grid.

    A log reads better as lines than as a spreadsheet, and the payload JSON is
    too wide for a column but too useful to drop.
    """
    if df is None or df.empty:
        empty_state('Nothing logged yet.')
        return
    out = []
    for _, r in df.head(limit).iterrows():
        out.append(
            '<div class="logline"><span class="ts">{ts}</span>  '
            '<span class="who">{who}</span>  <b>{what}</b>  {detail}</div>'.format(
                ts=fmt_ts(r.get(ts_col), '%m-%d %H:%M:%S'),
                who=str(r.get(who_col, '')),
                what=str(r.get(what_col, '')),
                detail=summarize(r.get(payload_col)),
            )
        )
    st.markdown(''.join(out), unsafe_allow_html=True)
