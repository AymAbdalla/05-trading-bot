"""Trading bot dashboard - Streamlit entry point.

    streamlit run dashboard/app.py --server.port 8501

Five tabs: Overview, Live Trades, Strategies, Graveyard, Agent Activity.

Three things about how this is wired.

**Read-only, always.** Every database call goes through `db_reader`, which
opens `mode=ro` URIs with `PRAGMA query_only=ON`. The engine owns the WAL; the
dashboard is a spectator and cannot become a second writer even by accident.

**Auto-refresh is per-tab, via `st.fragment(run_every=...)`.** Live tabs tick
at 5s, research tabs at 60s. Fragments rerun only their own block, so filter
widgets keep their values across a refresh - which a whole-page reload would
throw away. Cache TTLs sit just under each interval so a tick actually
re-reads the database instead of serving itself a cached frame.

**Empty is a first-class state.** The bot has not run in shadow mode yet, so
the empty path is the one most likely to be seen. Nothing here raises on an
empty table, a missing database, or a half-written research JSON.
"""
from __future__ import annotations

import os
import sys
import time
from typing import Any, Callable, Dict, Optional

import pandas as pd
import streamlit as st

# Support both `streamlit run dashboard/app.py` (script, no package context)
# and `python -m dashboard.app`. The first is how it is actually launched, and
# it does not put the repo root on the path.
if __package__ in (None, ''):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from dashboard import charts, components as ui, config, db_reader
else:
    from . import charts, components as ui, config, db_reader


st.set_page_config(
    page_title='Trading Bot',
    page_icon='▪',
    layout='wide',
    initial_sidebar_state='collapsed',
)

# --------------------------------------------------------------------------
# Cached readers. Caching lives here, not in db_reader, so the reader layer
# stays a plain function of the DB file and the tests do not need a streamlit
# runtime to exercise it.
# --------------------------------------------------------------------------

@st.cache_data(ttl=config.TTL_LIVE, show_spinner=False)
def _db_status():
    return db_reader.db_status()


@st.cache_data(ttl=config.TTL_LIVE, show_spinner=False)
def _equity(mode: Optional[str]):
    return db_reader.get_equity_curve(mode=mode)


@st.cache_data(ttl=config.TTL_LIVE, show_spinner=False)
def _trades(mode: Optional[str], asset_class: Optional[str], strategy_id: Optional[str],
            start_ms: Optional[int], end_ms: Optional[int], limit: int):
    return db_reader.get_trades(mode=mode, asset_class=asset_class, strategy_id=strategy_id,
                                start_ms=start_ms, end_ms=end_ms, limit=limit)


@st.cache_data(ttl=config.TTL_LIVE, show_spinner=False)
def _orders(mode: Optional[str], limit: int):
    return db_reader.get_orders(mode=mode, limit=limit)


@st.cache_data(ttl=config.TTL_LIVE, show_spinner=False)
def _status(mode: Optional[str]):
    return db_reader.get_bot_status(mode=mode)


@st.cache_data(ttl=config.TTL_SLOW, show_spinner=False)
def _modes():
    return db_reader.get_modes()


@st.cache_data(ttl=config.TTL_SLOW, show_spinner=False)
def _strategy_perf(mode: Optional[str]):
    return db_reader.get_strategy_performance(mode=mode)


@st.cache_data(ttl=config.TTL_SLOW, show_spinner=False)
def _lifecycle():
    return db_reader.get_strategy_lifecycle()


@st.cache_data(ttl=config.TTL_LIVE, show_spinner=False)
def _risk_events(limit: int):
    return db_reader.get_risk_events(limit=limit)


@st.cache_data(ttl=config.TTL_LIVE, show_spinner=False)
def _audit(limit: int, event_types: Optional[tuple], actor: Optional[str]):
    return db_reader.get_audit_log(limit=limit,
                                   event_types=list(event_types) if event_types else None,
                                   actor=actor)


@st.cache_data(ttl=config.TTL_LIVE, show_spinner=False)
def _signals(limit: int):
    return db_reader.get_signals(limit=limit)


@st.cache_data(ttl=config.TTL_RESEARCH, show_spinner=False)
def _graveyard_summary():
    return db_reader.load_graveyard_summary()


@st.cache_data(ttl=config.TTL_RESEARCH, show_spinner=False)
def _judge_pack():
    return db_reader.load_judge_pack()


# --------------------------------------------------------------------------
# Sidebar: global controls
# --------------------------------------------------------------------------

def sidebar() -> Dict[str, Any]:
    with st.sidebar:
        st.markdown('### Controls')

        auto = st.toggle('Auto-refresh', value=True,
                         help='Live tabs refresh every {}s, research tabs every {}s.'.format(
                             config.REFRESH_LIVE, config.REFRESH_SLOW))
        if st.button('Refresh now'):
            st.cache_data.clear()
            st.rerun()

        modes = _modes()
        mode = st.selectbox('Mode', ['ALL'] + modes, index=0,
                            help='paper | live | shadow, as recorded on each row.')

        st.markdown('---')
        state, detail = _db_status()
        st.caption('Database')
        st.caption('connected (read-only)' if state == 'ok' else '{}: {}'.format(state, detail))
        st.code(config.DB_PATH, language=None)
        st.caption('This dashboard never writes. `mode=ro` + `PRAGMA query_only`.')

    return {'auto': auto, 'mode': None if mode == 'ALL' else mode}


# --------------------------------------------------------------------------
# Tab renderers
# --------------------------------------------------------------------------

def render_overview(mode: Optional[str]) -> None:
    status = _status(mode)
    equity = _equity(mode)
    trades = _trades(mode, None, None, None, None, config.MAX_TRADE_ROWS)

    state = status['state']
    _, _, why = ui.STATE_STYLE.get(state, ('', '', ''))
    age = status.get('last_snapshot_age_min')
    detail = why if age is None else '{} · last snapshot {:.0f} min ago'.format(why, age)

    left, right = st.columns([2, 3], gap='medium')
    with left:
        st.markdown(ui.status_badge(state, detail), unsafe_allow_html=True)
    with right:
        st.markdown(
            '<div style="text-align:right;color:{};font-size:0.78rem">mode: <b>{}</b></div>'.format(
                config.INK_MUTED, status.get('mode') or 'no mode recorded'),
            unsafe_allow_html=True,
        )

    if state == 'HALTED':
        halt = status.get('halt') or {}
        if '_unreadable' in halt:
            st.error('KILL SWITCH ENGAGED. The HALT file exists but could not be parsed '
                     '({}). An unreadable halt is still a halt.'.format(halt['_unreadable']))
        else:
            st.error('KILL SWITCH ENGAGED — {} (halt_id `{}`, {}). No new entries until '
                     '`botctl.py resume --ack <id>`.'.format(
                         halt.get('reason', 'no reason recorded'),
                         halt.get('halt_id', '?'),
                         ui.fmt_ts(pd.to_datetime(halt['ts'], unit='ms')) if halt.get('ts') else 'time unknown'))

    today_pnl = db_reader.pnl_since(trades, db_reader.start_of_utc_day_ms())
    metrics = db_reader.compute_overview_metrics(trades, equity)

    ui.metric_row([
        {'label': 'Equity', 'value': ui.fmt_money(status.get('equity')),
         'sub': 'cash {}'.format(ui.fmt_money(status.get('cash')))},
        ui.signed_metric('PnL today (UTC)', today_pnl, 'realised on closed trades'),
        ui.signed_metric('PnL total', metrics['total_pnl'], 'net of fees'),
        {'label': 'Open risk', 'value': ui.fmt_money(status.get('open_risk')),
         'sub': 'unrealised, at stop'},
        {'label': 'Open positions', 'value': ui.fmt_int(metrics['open_positions'])},
        {'label': 'Closed trades', 'value': ui.fmt_int(metrics['total_trades'])},
    ], per_row=6)

    ui.section('Equity')
    st.plotly_chart(charts.equity_curve(equity), config={'displayModeBar': False})
    st.plotly_chart(charts.drawdown_curve(equity), config={'displayModeBar': False})

    ui.section('Performance')
    ppy = metrics.get('periods_per_year')
    sharpe_sub = ('annualised from {:,.0f} periods/yr'.format(ppy) if ppy
                  else 'needs 3+ snapshots')
    ui.metric_row([
        {'label': 'Win rate', 'value': ui.fmt_pct(metrics['win_rate']),
         'sub': '{} closed'.format(ui.fmt_int(metrics['total_trades']))},
        {'label': 'Sharpe (equity)', 'value': ui.fmt_num(metrics['sharpe'], 2),
         'sub': sharpe_sub, 'tone': ui._tone(metrics['sharpe'])},
        {'label': 'Profit factor', 'value': ui.fmt_num(metrics['profit_factor'], 2),
         'sub': 'gross win / gross loss'},
        {'label': 'Max drawdown', 'value': ui.fmt_pct(metrics['max_drawdown'], 2, signed=True),
         'sub': 'peak to trough', 'tone': ui._tone(metrics['max_drawdown'])},
        ui.signed_metric('Avg R', metrics['avg_r'], 'per closed trade', money=False),
        {'label': 'Equity points', 'value': ui.fmt_int(metrics['equity_points']),
         'sub': 'snapshots on record'},
    ], per_row=6)

    col_a, col_b = st.columns([3, 2], gap='medium')
    with col_a:
        ui.section('PnL distribution')
        st.plotly_chart(charts.pnl_distribution(trades), config={'displayModeBar': False})
    with col_b:
        ui.section('Risk events')
        events = _risk_events(8)
        if events.empty:
            ui.empty_state('No risk events recorded.',
                           'Circuit breakers, halts and restarts land here.')
        else:
            ui.log_lines(events, db_reader.summarize_payload, who_col='type',
                         what_col='type', payload_col='details_json', limit=8)
        ui.note('The kill switch itself is a file at the repo root, not a row. The badge '
                'above reads that file; this list is the audit trail beside it.')


def render_live_trades(mode: Optional[str]) -> None:
    ui.section('Filters')
    f1, f2, f3, f4 = st.columns([1, 1, 1, 1.4], gap='small')
    with f1:
        asset_class = st.selectbox('Asset class', ['ALL'] + config.ASSET_CLASSES,
                                   key='lt_class')
    with f2:
        perf = _strategy_perf(mode)
        options = ['ALL'] + (sorted(perf['strategy_id'].dropna().unique().tolist())
                             if not perf.empty else [])
        strategy = st.selectbox('Strategy', options, key='lt_strategy')
    with f3:
        window = st.selectbox('Window', ['All time', 'Today', 'Last 7 days', 'Last 30 days'],
                              key='lt_window')
    with f4:
        st.caption(' ')
        st.caption('Refreshing every {}s. Rows capped at {:,}.'.format(
            config.REFRESH_LIVE, config.MAX_TRADE_ROWS))

    now_ms = int(time.time() * 1000)
    start_ms = None
    if window == 'Today':
        start_ms = db_reader.start_of_utc_day_ms(now_ms)
    elif window == 'Last 7 days':
        start_ms = now_ms - 7 * 86_400_000
    elif window == 'Last 30 days':
        start_ms = now_ms - 30 * 86_400_000

    trades = _trades(mode, None if asset_class == 'ALL' else asset_class,
                     None if strategy == 'ALL' else strategy,
                     start_ms, None, config.MAX_TRADE_ROWS)

    open_trades = trades[trades['closed_ts'].isna()] if not trades.empty else trades
    closed = trades[trades['closed_ts'].notna()] if not trades.empty else trades
    realised = (float(pd.to_numeric(closed['pnl_net'], errors='coerce').fillna(0).sum())
                if not closed.empty else 0.0)

    ui.metric_row([
        {'label': 'Rows shown', 'value': ui.fmt_int(len(trades))},
        {'label': 'Open', 'value': ui.fmt_int(len(open_trades))},
        {'label': 'Closed', 'value': ui.fmt_int(len(closed))},
        ui.signed_metric('Realised PnL', realised, 'this filter'),
    ], per_row=4)

    if not open_trades.empty:
        ui.section('Open positions')
        ui.trade_table(open_trades, height=min(300, 60 + 36 * len(open_trades)))

    ui.section('Trade log')
    ui.trade_table(trades)
    if len(trades) >= config.MAX_TRADE_ROWS:
        ui.note('Truncated at {:,} rows. Narrow the window to see older trades.'.format(
            config.MAX_TRADE_ROWS))

    ui.section('Order flow')
    ui.note('Orders that never became a position - rejected, cancelled, still pending - '
            'are invisible in the trade log by construction. They are the first place to '
            'look when the bot appears idle but is failing at the venue.')
    ui.order_table(_orders(mode, config.MAX_ORDER_ROWS))


def render_strategies(mode: Optional[str]) -> None:
    perf = _strategy_perf(mode)

    if perf.empty:
        ui.empty_state('No strategies registered and none have traded.',
                       'The registry fills in when a strategy is promoted; the '
                       'performance columns fill in when it trades.')
    else:
        counts = perf['status'].value_counts().to_dict()
        ui.metric_row([
            {'label': s.capitalize(), 'value': ui.fmt_int(counts.get(s, 0))}
            for s in config.STRATEGY_STATUSES
        ] + [
            {'label': 'Unregistered', 'value': ui.fmt_int(counts.get('unregistered', 0)),
             'sub': 'traded but not in registry',
             'tone': 'neg' if counts.get('unregistered', 0) else ''},
            {'label': 'Total', 'value': ui.fmt_int(len(perf))},
        ], per_row=6)

        if counts.get('unregistered'):
            ui.note('A strategy that has traded but is not in `strategy_registry` is a '
                    'reconciliation gap, not a display quirk. Shown, not hidden.')

        ui.section('Performance')
        ui.strategy_table(perf)
        ui.note('`sharpe_trade` is mean/std of per-trade net PnL - NOT the annualised '
                'equity-curve Sharpe on the Overview tab. Two different quantities, '
                'named differently so they are not compared by accident.')

        ui.section('Net PnL by strategy')
        st.plotly_chart(charts.strategy_pnl_bar(perf), config={'displayModeBar': False})

    ui.section('Lifecycle')
    lifecycle = _lifecycle()
    if lifecycle.empty:
        ui.empty_state('No promotions or demotions logged yet.',
                       'Read from the append-only audit log, not the registry - the '
                       'registry keeps only the current status.')
    else:
        st.plotly_chart(charts.lifecycle_timeline(lifecycle), config={'displayModeBar': False})
        st.dataframe(lifecycle.assign(time=lifecycle['time'].apply(ui.fmt_ts))
                     .drop(columns=['ts']), width='stretch', hide_index=True, height=240)


def render_graveyard() -> None:
    summary, s_note = _graveyard_summary()
    pack, p_note = _judge_pack()

    for n in (s_note, p_note):
        if n:
            st.warning(n)

    if not summary and not pack:
        ui.empty_state('No graveyard artifacts found.',
                       'Expected {} and {}.'.format(config.GRAVEYARD_SUMMARY_PATH,
                                                    config.JUDGE_PACK_PATH))
        return

    summary = summary or {}
    counts = summary.get('verdict_counts', {}) or {}
    findings = summary.get('distinct_findings', {}) or {}

    ui.metric_row([
        {'label': 'Entries', 'value': ui.fmt_int(summary.get('entries_total')),
         'sub': 'graveyard rows'},
        {'label': 'PASS', 'value': ui.fmt_int(counts.get('PASS')),
         'sub': 'raw rows', 'tone': 'pos' if counts.get('PASS') else ''},
        {'label': 'PASS benchmark', 'value': ui.fmt_int(counts.get('PASS_BENCHMARK'))},
        {'label': 'FAIL', 'value': ui.fmt_int(counts.get('FAIL'))},
        {'label': 'NOT_TESTED', 'value': ui.fmt_int(counts.get('NOT_TESTED')),
         'sub': 'could not run'},
        {'label': 'Distinct findings', 'value': ui.fmt_int(
            findings.get('strategy_x_ticker_x_timeframe')),
         'sub': 'strategy x ticker x tf'},
    ], per_row=6)

    ui.note('Cite <b>distinct findings</b>, never raw PASS rows: one strategy that works '
            'on one ticker across 9 exit configs produces 9 rows and ONE finding. '
            'NOT_TESTED means the harness could not run the configuration - it never '
            'means "ran and found nothing".')

    nt = summary.get('not_tested_breakdown') or {}
    if nt:
        ui.metric_row([
            {'label': 'Tested rows', 'value': ui.fmt_int(summary.get('tested_rows'))},
            {'label': 'Tested WITH trades',
             'value': ui.fmt_int(summary.get('tested_rows_with_trades')),
             'sub': 'the honest denominator'},
            {'label': 'NOT_TESTED: bars', 'value': ui.fmt_int(nt.get('insufficient_bars')),
             'sub': 'too little history'},
            {'label': 'NOT_TESTED: unsizable', 'value': ui.fmt_int(nt.get('unsizable_at_cap')),
             'sub': 'no size at the notional cap'},
        ], per_row=4)

    mc = summary.get('multiple_comparisons') or {}
    if mc.get('expected_max_z_under_null') is not None:
        st.warning(
            'Multiple comparisons: with {} tests, chance alone is expected to produce a '
            'best result around {} sigma. A single impressive row is the base rate, not '
            'evidence.'.format(ui.fmt_int(mc.get('tests_completed')),
                               ui.fmt_num(mc.get('expected_max_z_under_null'), 2)))

    if counts:
        ui.section('Verdict mix')
        st.plotly_chart(charts.verdict_composition(counts), config={'displayModeBar': False})

    col_a, col_b = st.columns(2, gap='medium')
    with col_a:
        ui.section('Top PASS concentrations')
        st.plotly_chart(
            charts.pass_concentration_bar(summary.get('pass_concentration_top5') or []),
            config={'displayModeBar': False})
    with col_b:
        ui.section('Judge assertions')
        assertions = ((pack or {}).get('silent_assertions') or {})
        failed = assertions.get('failed') or []
        run = assertions.get('assertions_run')
        if run is None:
            ui.empty_state('No assertion block in the judge pack.')
        else:
            ui.metric_row([
                {'label': 'Assertions run', 'value': ui.fmt_int(run)},
                {'label': 'Failing', 'value': ui.fmt_int(len(failed)),
                 'tone': 'neg' if failed else 'pos'},
            ], per_row=2)
            lines = [
                '<div class="logline"><span style="color:{}">▼ FAIL</span>  {}</div>'.format(
                    config.LOSS, name) for name in failed
            ] + [
                '<div class="logline"><span style="color:{}">▲ PASS</span>  {}</div>'.format(
                    config.PROFIT, res.get('assertion', '?'))
                for res in (assertions.get('results') or [])
                if isinstance(res, dict) and res.get('pass')
            ]
            st.markdown(''.join(lines), unsafe_allow_html=True)

    ui.section('Strategy health: which strategies actually fire')
    health = db_reader.graveyard_strategy_health(pack)
    if health.empty:
        ui.empty_state('No per-strategy block in the judge pack.')
    else:
        silent = health[health['n_trades'].fillna(0) == 0]
        ui.metric_row([
            {'label': 'Strategies', 'value': ui.fmt_int(len(health))},
            {'label': 'Producing trades', 'value': ui.fmt_int(len(health) - len(silent)),
             'tone': 'pos'},
            {'label': 'Never fire', 'value': ui.fmt_int(len(silent)),
             'sub': 'zero trades over every tested row',
             'tone': 'neg' if len(silent) else ''},
        ], per_row=3)
        ui.note('A strategy that produced no trades did not run and fail - it did not '
                'run. Different verdicts, kept apart here.')
        st.plotly_chart(charts.strategy_firing_bar(health), config={'displayModeBar': False})
        st.dataframe(health, width='stretch', hide_index=True, height=320)

    breakdown = (pack or {}).get('asset_class_breakdown') or []
    if breakdown:
        ui.section('Pooled PnL per trade, by strategy and asset class')
        st.plotly_chart(charts.asset_class_pnl_bar(breakdown[:30]), config={'displayModeBar': False})
        if len(breakdown) > 30:
            ui.note('Showing the first 30 of {} rows.'.format(len(breakdown)))


def render_agent_activity() -> None:
    col_a, col_b = st.columns([3, 2], gap='medium')

    with col_a:
        ui.section('Audit log')
        events = st.multiselect('Event types', db_reader.AGENT_EVENT_TYPES, default=[],
                                help='Empty means all event types.', key='aa_events')
        audit = _audit(config.MAX_AUDIT_ROWS, tuple(events) if events else None, None)
        if audit.empty:
            ui.empty_state('Audit log is empty.',
                           'Append-only: order placed, filled, position opened/closed, '
                           'halt, resume, strategy promoted/demoted, mode change.')
        else:
            ui.log_lines(audit, db_reader.summarize_payload, limit=config.MAX_AUDIT_ROWS)

    with col_b:
        ui.section('Risk events')
        risk = _risk_events(config.MAX_RISK_ROWS)
        if risk.empty:
            ui.empty_state('No risk events.')
        else:
            ui.log_lines(risk, db_reader.summarize_payload, who_col='type',
                         what_col='type', payload_col='details_json',
                         limit=config.MAX_RISK_ROWS)

        ui.section('Recent signals')
        ui.note('Signals the scanner emitted, acted on or skipped. A long run of skips '
                'with the same reason is usually the interesting part.')
        signals = _signals(60)
        if signals.empty:
            ui.empty_state('No signals emitted yet.')
        else:
            view = pd.DataFrame({
                'time': signals['time'].apply(lambda v: ui.fmt_ts(v, '%m-%d %H:%M:%S')),
                'market': signals['pair'],
                'tf': signals['tf'],
                'strategy': signals['strategy_id'],
                'pattern': signals['pattern'],
                'dir': signals['direction'],
                'conf': signals['confidence'].apply(lambda v: ui.fmt_num(v, 2)),
                'acted': signals['acted'].map({1: 'yes', 0: 'no'}).fillna('?'),
                'skip_reason': signals['skip_reason'].fillna('-'),
            })
            st.dataframe(view, width='stretch', hide_index=True, height=340)


# --------------------------------------------------------------------------
# Fragment wiring
# --------------------------------------------------------------------------

def _run_fragment(fn: Callable[[], None], run_every: Optional[int]) -> None:
    """Run `fn` inside an auto-refreshing fragment.

    A fragment reruns only its own block, so widget state inside it survives
    the tick - which a whole-page reload would throw away. `run_every=None`
    (auto-refresh off) still runs the fragment, just without the timer.

    `fn` must be a NAMED module-level function, never a lambda: streamlit
    derives the fragment id from `module.qualname` plus the container path, and
    every lambda defined in the same scope shares the qualname
    `main.<locals>.<lambda>`. Wiring five tabs with lambdas leaves their ids
    differing only by where they happen to be rendered.
    """
    st.fragment(run_every=run_every)(fn)()


def _active_mode() -> Optional[str]:
    """The mode filter, via session state, so the tab renderers below can stay
    zero-argument (see `_run_fragment` for why that matters)."""
    return st.session_state.get('active_mode')


def tab_overview() -> None:
    render_overview(_active_mode())


def tab_live_trades() -> None:
    render_live_trades(_active_mode())


def tab_strategies() -> None:
    render_strategies(_active_mode())


def tab_graveyard() -> None:
    render_graveyard()


def tab_agent_activity() -> None:
    render_agent_activity()


def main() -> None:
    ui.inject_css()
    controls = sidebar()
    mode, auto = controls['mode'], controls['auto']
    st.session_state['active_mode'] = mode
    live_every = config.REFRESH_LIVE if auto else None
    slow_every = config.REFRESH_SLOW if auto else None

    st.markdown('# Trading Bot')
    st.markdown(
        '<div style="color:{};font-size:0.74rem;margin-top:-0.4rem">'
        'read-only view · {} · auto-refresh {}</div>'.format(
            config.INK_MUTED,
            'mode: {}'.format(mode) if mode else 'all modes',
            'on' if auto else 'off'),
        unsafe_allow_html=True,
    )

    state, detail = _db_status()
    if state == 'missing':
        st.info('No database yet at `{}`. The bot has not run. The Graveyard tab reads '
                'files on disk and still works.'.format(config.DB_PATH))
    elif state == 'error':
        st.error('Database unreadable: {}. This is NOT the same as "no trades" - the tabs '
                 'below cannot tell you anything about the engine right now.'.format(detail))

    tabs = st.tabs(['Overview', 'Live Trades', 'Strategies', 'Graveyard', 'Agent Activity'])
    with tabs[0]:
        _run_fragment(tab_overview, live_every)
    with tabs[1]:
        _run_fragment(tab_live_trades, live_every)
    with tabs[2]:
        _run_fragment(tab_strategies, slow_every)
    with tabs[3]:
        _run_fragment(tab_graveyard, slow_every)
    with tabs[4]:
        _run_fragment(tab_agent_activity, live_every)


main()
