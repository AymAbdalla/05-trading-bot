#!/usr/bin/env python3
"""Seven Eastern-time days of paper/shadow trading, summarised.

    env -u PYTHONPATH python3 scripts/weekly_shadow_summary.py
    env -u PYTHONPATH python3 scripts/weekly_shadow_summary.py --week-ending 2026-08-17
    env -u PYTHONPATH python3 scripts/weekly_shadow_summary.py --json
    env -u PYTHONPATH python3 scripts/weekly_shadow_summary.py --send

Same two sources as the daily report: `db/trading.db` is authoritative, the
paper adapter CSV is a cross check. The window is the seven ET calendar days
ENDING ON and INCLUDING `--week-ending` (default: today ET).

## `--send` does not send

Identical to the daily script: MCP tools belong to the agent session, not to a
subprocess, so this writes `logs/summaries/weekly_<week-ending>.txt` and prints
the path for cron or Raven to deliver. See `daily_shadow_summary.py`.

## Strategies added and removed

Derived from the FIRST and LAST appearance of each `strategy_id` in `signals`
across the whole table, not from `strategy_registry`: that table is empty, so a
registry-based answer would be NOT_TESTED and this one is a measurement of what
actually ran. A strategy that the current source would build but that never
appears in the log is reported separately and NOT counted as removed. That gap
is usually convention 13 (a running loop snapshotted its imports at start), not
a deletion, and the report says which it cannot distinguish.

## Recommendations

Every line in the RECOMMENDATIONS block is emitted by a numeric rule over the
numbers above it. Nothing is inferred, suggested, or invented. If no rule
fires, the block says so.
"""
import argparse
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shadow_summary_lib import (  # noqa: E402
    DATA_BLOCKER, DEFAULT_CSV_PATH, DEFAULT_DB_PATH, ERROR, UNCLASSIFIED,
    DataSourceError, aggregate_signals, aggregate_trades, check_schema,
    configured_strategies, dump_json, error_report, et_day_bounds_ms,
    et_range_bounds_ms, first_trade_ever, fmt_pct_of, fmt_pf, fmt_rate,
    fmt_usd, ms_to_et_date, ms_to_et_string, open_db, open_exposure,
    parse_date, read_csv_rows, read_equity, read_events, read_fills,
    read_forge, read_positions, read_signals, render_categories,
    render_equity, render_skip_table, rule, safe_div, summarise_events,
    today_et, write_summary_file)

WINDOW_DAYS = 7

#: A strategy with fewer decided trades than this is not a verdict either way
#: (convention 7). Stated as a constant so the recommendation block cannot
#: quietly change its mind about what "enough" means.
MIN_TRADES_FOR_A_VERDICT = 30


def strategy_lifespans(conn, mode='paper'):
    """First and last signal timestamp per strategy, over the WHOLE table."""
    rows = conn.execute(
        'SELECT strategy_id, min(ts) AS first_ts, max(ts) AS last_ts, '
        'count(*) AS n FROM signals WHERE mode = ? GROUP BY strategy_id',
        (mode,)).fetchall()
    return {r['strategy_id']: {'first_ts': int(r['first_ts']),
                               'last_ts': int(r['last_ts']),
                               'total_signals': int(r['n'])} for r in rows}


def collect(week_ending, db_path, csv_path):
    first_day = week_ending - datetime.timedelta(days=WINDOW_DAYS - 1)
    start_ms, end_ms = et_range_bounds_ms(first_day, week_ending)

    conn = open_db(db_path)
    try:
        check_schema(conn, db_path)
        signals = read_signals(conn, start_ms, end_ms)
        opened, closed = read_positions(conn, start_ms, end_ms)
        fills = read_fills(conn, start_ms, end_ms)
        equity = read_equity(conn, start_ms, end_ms)
        audit_rows, risk_rows = read_events(conn, start_ms, end_ms)
        first_ever = first_trade_ever(conn)
        lifespans = strategy_lifespans(conn)
        still_open = [p for p in opened if p.get('closed_ts') is None]
        daily = []
        for offset in range(WINDOW_DAYS):
            day = first_day + datetime.timedelta(days=offset)
            d_start, d_end = et_day_bounds_ms(day)
            day_signals = read_signals(conn, d_start, d_end)
            _, day_closed = read_positions(conn, d_start, d_end)
            day_equity = read_equity(conn, d_start, d_end)
            day_agg = aggregate_signals(day_signals)
            day_trades = aggregate_trades(day_closed)
            daily.append({
                'date_et': day.isoformat(),
                'evaluations': day_agg['evaluations'],
                'entries': day_agg['entries'],
                'skips': day_agg['skips'],
                'closed': day_trades['closed'],
                'pnl': day_trades['pnl'],
                'equity_end': day_equity['end'],
                'equity_status': day_equity['status'],
            })
    finally:
        conn.close()

    decisions = aggregate_signals(signals)
    trades = aggregate_trades(closed)
    events = summarise_events(audit_rows, risk_rows)
    csv_stats = read_csv_rows(csv_path, start_ms / 1000.0, end_ms / 1000.0)
    forge = read_forge(start_ms, end_ms)
    configured = configured_strategies()

    # Added: first ever appeared inside this window.
    #
    # Stopped: compared on CALENDAR DAYS, not on raw timestamps. Within one
    # poll cycle the loop writes its strategies in list order milliseconds
    # apart, so whichever strategy happens to be written last holds the global
    # max ts and every other strategy looks like it went quiet. Comparing on
    # the last ACTIVE DAY removes that artefact: a strategy has stopped when it
    # produced nothing on the most recent day that produced anything at all.
    last_signal_ms = max((v['last_ts'] for v in lifespans.values()),
                         default=None)
    last_active_day = (ms_to_et_date(last_signal_ms)
                       if last_signal_ms is not None else None)
    added, removed, still_active = [], [], []
    for name, span in sorted(lifespans.items()):
        if start_ms <= span['first_ts'] < end_ms:
            added.append({'strategy': name,
                          'first_seen': ms_to_et_string(span['first_ts'])})
        if not (start_ms <= span['last_ts'] < end_ms):
            continue
        if last_active_day is not None and \
                ms_to_et_date(span['last_ts']) < last_active_day:
            removed.append({'strategy': name,
                            'last_seen': ms_to_et_string(span['last_ts'])})
        else:
            still_active.append(name)
    configured_never_seen = ([n for n in configured['names']
                              if n not in lifespans]
                             if configured['status'] == 'OK' else [])

    return {
        'report': 'weekly',
        'week_ending_et': week_ending.isoformat(),
        'week_start_et': first_day.isoformat(),
        'window_start_ms': start_ms,
        'window_end_ms': end_ms,
        'db_path': db_path,
        'csv_path': csv_path,
        'status': 'OK',
        'decisions': decisions,
        'trades': trades,
        'entries_opened': len(opened),
        'fills': fills,
        'open_at_window_end': open_exposure(still_open),
        'equity': equity,
        'events': events,
        'daily': daily,
        'first_trade_ever_ms': first_ever,
        'first_trade_ever_in_window': (
            first_ever is not None and start_ms <= first_ever < end_ms),
        'csv': csv_stats,
        'forge': forge,
        'strategy_changes': {
            'added': added,
            'removed': removed,
            'still_active_at_window_end': sorted(still_active),
            'last_active_day_et': (last_active_day.isoformat()
                                   if last_active_day else None),
            'configured_but_never_logged': sorted(configured_never_seen),
            'configured_status': configured['status'],
            'configured_note': configured.get('note'),
            'configured_names': configured['names'],
            'derivation': 'first/last signals.ts per strategy_id, compared '
                          'on ET calendar days; strategy_registry is empty '
                          'so it cannot be used',
        },
    }


def _ranked(per_strategy, key, reverse=True, require_trades=True):
    """Rank strategies, excluding any whose metric is undefined.

    A strategy with no decided trades has no win rate, so it cannot be best or
    worst at one. Excluding it is the difference between "worst win rate" and
    "has not traded".
    """
    items = []
    for name, bucket in per_strategy.items():
        value = bucket.get(key)
        if value is None:
            continue
        if require_trades and (bucket['wins'] + bucket['losses']) == 0:
            continue
        items.append((name, value, bucket))
    items.sort(key=lambda item: item[1], reverse=reverse)
    return items


def render(data):
    d = data['decisions']
    t = data['trades']
    eq = data['equity']
    ev = data['events']
    sc = data['strategy_changes']
    out = []
    a = out.append

    a('SHADOW WEEKLY {} to {} (ET)'.format(
        data['week_start_et'], data['week_ending_et']))
    a('db: {}'.format(os.path.relpath(data['db_path'])))
    a('')

    a(rule('WEEK TOTALS'))
    a('  evaluations   {:>8}'.format(d['evaluations']))
    a('  entries       {:>8}'.format(d['entries']))
    a('  fills         {:>8}   (fees {})'.format(
        data['fills']['count'], fmt_usd(data['fills']['fees'])))
    a('  skips         {:>8}'.format(d['skips']))
    a('  identity      {:>8}   evaluations == entries + skips'.format(
        'OK' if d['identity_ok'] else 'VIOLATED'))
    a('  closed trades {:>8}'.format(t['closed']))
    a('  wins / losses / flat   {} / {} / {}'.format(
        t['wins'], t['losses'], t['flats']))
    a('  win rate      {}'.format(fmt_rate(t['win_rate'], t['decided'])))
    a('  profit factor {}'.format(fmt_pf(t['profit_factor'], t['losses'])))
    a('  weekly P&L    {}'.format(fmt_usd(t['pnl'])))
    a('  open at week end  {}  (premium at risk {})'.format(
        data['open_at_window_end']['count'],
        fmt_usd(data['open_at_window_end']['premium_at_risk'])))
    if t['closed'] == 0:
        a('  NOTE: no trade closed this week. Win rate and profit factor are')
        a('        undefined, not zero, and no edge claim is possible.')
    a('')

    a(rule('EQUITY (paper USDC)'))
    out.extend(render_equity(eq))
    a('')

    a(rule('DAY BY DAY'))
    a('  {:<12}{:>9}{:>9}{:>8}{:>12}{:>13}'.format(
        'date', 'evals', 'entries', 'closed', 'P&L', 'equity end'))
    for day in data['daily']:
        equity_cell = ('n/a' if day['equity_end'] is None
                       else fmt_usd(day['equity_end']))
        a('  {:<12}{:>9}{:>9}{:>8}{:>12}{:>13}'.format(
            day['date_et'], day['evaluations'], day['entries'], day['closed'],
            fmt_usd(day['pnl']), equity_cell))
    a('')

    a(rule('BEST AND WORST'))
    by_pnl = _ranked(t['per_strategy'], 'pnl', reverse=True,
                     require_trades=False)
    by_wr = _ranked(t['per_strategy'], 'win_rate', reverse=True,
                    require_trades=True)
    if by_pnl:
        a('  by P&L   best  {:<28} {}'.format(by_pnl[0][0], fmt_usd(by_pnl[0][1])))
        a('  by P&L   worst {:<28} {}'.format(by_pnl[-1][0], fmt_usd(by_pnl[-1][1])))
    else:
        a('  by P&L   n/a: no strategy closed a trade this week')
    if by_wr:
        best = by_wr[0]
        worst = by_wr[-1]
        a('  by win % best  {:<28} {} on {} decided'.format(
            best[0], fmt_rate(best[1], best[2]['wins'] + best[2]['losses']),
            best[2]['wins'] + best[2]['losses']))
        a('  by win % worst {:<28} {} on {} decided'.format(
            worst[0], fmt_rate(worst[1], worst[2]['wins'] + worst[2]['losses']),
            worst[2]['wins'] + worst[2]['losses']))
    else:
        a('  by win % n/a: no strategy has a decided trade, so no win rate')
        a('           exists to rank. Undefined, not last place.')
    a('')

    a(rule('PER STRATEGY'))
    a('  {:<30}{:>8}{:>8}{:>7}{:>8}  {:<16}{:>10}'.format(
        'strategy', 'evals', 'entries', 'skips', 'closed', 'win rate', 'P&L'))
    names = sorted(set(d['per_strategy']) | set(t['per_strategy']))
    if not names:
        a('  none')
    for name in names:
        sig = d['per_strategy'].get(
            name, {'evaluations': 0, 'entries': 0, 'skips': 0})
        tr = t['per_strategy'].get(name)
        if tr is None:
            closed, wr, pnl = 0, 'n/a (0 trades)', 0.0
        else:
            closed = tr['closed']
            wr = fmt_rate(tr['win_rate'], tr['wins'] + tr['losses'])
            pnl = tr['pnl']
        a('  {:<30}{:>8}{:>8}{:>7}{:>8}  {:<16}{:>10}'.format(
            name[:30], sig['evaluations'], sig['entries'], sig['skips'],
            closed, wr, fmt_usd(pnl)))
    a('')

    a(rule('TOP SKIP REASONS'))
    a('  {:>6}  {:<34} {:>6}  {}'.format('count', 'reason', 'share', 'class'))
    out.extend(render_skip_table(d['reasons'], limit=15))
    a('')

    a(rule('SKIP CATEGORIES'))
    out.extend(render_categories(d['categories'], d['unclassified_reasons']))
    a('')

    a(rule('STRATEGY CHANGES'))
    a('  derivation: {}'.format(sc['derivation']))
    if sc['added']:
        for item in sc['added']:
            a('  ADDED    {:<30} first seen {}'.format(
                item['strategy'], item['first_seen']))
    else:
        a('  ADDED    none inside the window')
    if sc['removed']:
        for item in sc['removed']:
            a('  STOPPED  {:<30} last seen {} (nothing on {})'.format(
                item['strategy'], item['last_seen'],
                sc['last_active_day_et']))
    else:
        a('  STOPPED  none (every strategy that ran was still running on '
          '{})'.format(sc['last_active_day_et'] or 'the last active day'))
    if sc['configured_status'] != 'OK':
        a('  CONFIGURED LIST: NOT_TESTED. {}'.format(sc['configured_note']))
    elif sc['configured_but_never_logged']:
        a('  configured in source but never logged: {}'.format(
            ', '.join(sc['configured_but_never_logged'])))
        a('  This report CANNOT tell "added to source, loop not restarted"')
        a('  (convention 13) apart from "added and broken". Both look like')
        a('  silence. NOT_TESTED until the loop is restarted.')
    else:
        a('  every configured strategy has appeared in the log')
    a('')

    a(rule('FORGE THIS WEEK'))
    f = data['forge']
    if f['status'] != 'OK':
        a('  NOT_TESTED: {}'.format(f['note']))
    else:
        a('  proposals with mtime in window: {} of {} on disk'.format(
            len(f['proposals_in_window']), f['proposals_total']))
        for item in f['proposals_in_window']:
            a('    {}  {}'.format(item['file'],
                                  ms_to_et_string(item['mtime_ms'])))
        a('  forge_runs.jsonl: {} run record(s){}'.format(
            f['runs_total'],
            ', file mtime in window' if f['runs_file_mtime_in_window'] else ''))
        if f['written_names']:
            a('  proposals named in run records: {}'.format(
                ', '.join(f['written_names'])))
        if f['refused_by_category']:
            a('  refusals by category: {}'.format(
                ', '.join('{}={}'.format(k, v) for k, v
                          in sorted(f['refused_by_category'].items()))))
        a('  attribution: {}'.format(f['attribution']))
    a('')

    a(rule('NOTABLE EVENTS'))
    a('  halts {} / resumes {} / accounting violations {}'.format(
        ev['halts'], ev['resumes'], ev['accounting_violations']))
    a('  sessions started {} / stopped {}'.format(
        ev['sessions_started'], ev['sessions_stopped']))
    if data['first_trade_ever_ms'] is None:
        a('  FIRST TRADE EVER: none. No position has ever been recorded.')
    elif data['first_trade_ever_in_window']:
        a('  FIRST TRADE EVER recorded this week at {}'.format(
            ms_to_et_string(data['first_trade_ever_ms'])))
    else:
        a('  first trade ever was {} (before this window)'.format(
            ms_to_et_string(data['first_trade_ever_ms'])))
    if ev['loop_health']:
        a('  loop health counters: {}'.format(
            ', '.join('{}={}'.format(k, v)
                      for k, v in sorted(ev['loop_health'].items()))))
    a('')

    a(rule('SOURCE RECONCILIATION'))
    csv_stats = data['csv']
    a('  db signals rows in window   {}'.format(d['evaluations']))
    if csv_stats['status'] != 'OK':
        a('  csv {}: {}'.format(csv_stats['status'], csv_stats['note']))
        a('  NOT_TESTED as a cross check. DB numbers above still stand.')
    else:
        a('  csv decision rows in window {}'.format(csv_stats['rows']))
        a('  delta                       {:+d}'.format(
            csv_stats['rows'] - d['evaluations']))
        if csv_stats['actions']:
            a('  csv actions: {}'.format(
                ', '.join('{}={}'.format(k, v) for k, v
                          in sorted(csv_stats['actions'].items()))))
        if csv_stats['unparsable_ts']:
            a('  csv rows with an unparsable ts: {} (excluded, counted)'
              .format(csv_stats['unparsable_ts']))
    a('')

    out.extend(recommendations(data))
    return '\n'.join(out)


def recommendations(data):
    """Emit one line per numeric rule that fired. No rule, no line.

    Each rule names the number that triggered it so a reader can check it
    against the tables above rather than taking the sentence on trust.
    """
    d = data['decisions']
    t = data['trades']
    ev = data['events']
    sc = data['strategy_changes']
    lines = [rule('RECOMMENDATIONS FOR NEXT WEEK')]
    fired = []

    def emit(text):
        fired.append(text)
        lines.append('  ' + text)

    if d['evaluations'] == 0:
        emit('Zero evaluation rows in seven days. Confirm the loop is running '
             'and writing to this database before reading anything else here.')
    if d['entries'] == 0 and d['evaluations'] > 0:
        emit('{} evaluations, 0 entries. Nothing was graded. Convention 11: '
             'NOT_TESTED, not a fleet that looked and declined.'
             .format(d['evaluations']))

    blocked = d['categories'].get(DATA_BLOCKER, 0)
    share = safe_div(blocked, d['skips'])
    if share is not None and share >= 0.5:
        emit('{} of {} skips ({}) are DATA BLOCKERS. The largest available '
             'gain is supplying the missing input, not tuning a threshold.'
             .format(blocked, d['skips'], fmt_pct_of(blocked, d['skips'])))

    fully_blocked = []
    for name, bucket in sorted(d['per_strategy'].items()):
        cats = bucket.get('categories') or {}
        if bucket['entries'] == 0 and bucket['skips'] > 0 and \
                cats.get(DATA_BLOCKER, 0) == bucket['skips']:
            fully_blocked.append(name)
    if fully_blocked:
        emit('100% data blocked all week, so NOT_TESTED rather than failing: '
             '{}. Either supply the input or stop counting their windows as '
             'evidence.'.format(', '.join(fully_blocked)))

    if d['categories'].get(ERROR, 0):
        emit('{} skips landed in the ERROR bucket. Those are our bugs and '
             'they are decision windows nobody evaluated.'
             .format(d['categories'][ERROR]))
    if d['categories'].get(UNCLASSIFIED, 0):
        emit('{} skips are UNCLASSIFIED: {}. Add a rule to '
             'scripts/shadow_summary_lib.py before citing the split.'
             .format(d['categories'][UNCLASSIFIED],
                     ', '.join(sorted(d['unclassified_reasons']))))

    if not d['identity_ok'] or ev['loop_identity_violations']:
        emit('The accounting identity did not hold ({} loop violations). '
             'Treat every count in this report as suspect until it is '
             'explained (convention 20).'
             .format(ev['loop_identity_violations']))

    if 0 < t['decided'] < MIN_TRADES_FOR_A_VERDICT:
        emit('{} decided trades. Below {} this is a shrug in either '
             'direction, not a verdict (convention 7). Do not promote or '
             'kill on it.'.format(t['decided'], MIN_TRADES_FOR_A_VERDICT))

    if t['profit_factor'] is not None:
        if t['profit_factor'] < 1.0:
            emit('Profit factor {:.2f} on {} decided trades: gross losses '
                 'exceed gross profits.'.format(t['profit_factor'],
                                                t['decided']))
        else:
            emit('Profit factor {:.2f} on {} decided trades.'
                 .format(t['profit_factor'], t['decided']))

    losers = [(n, b) for n, b in sorted(t['per_strategy'].items())
              if b['pnl'] < 0 and (b['wins'] + b['losses'])
              >= MIN_TRADES_FOR_A_VERDICT]
    for name, bucket in losers:
        emit('{} is down {} on {} decided trades, which is enough sample to '
             'act on.'.format(name, fmt_usd(bucket['pnl']),
                              bucket['wins'] + bucket['losses']))

    if ev['halts']:
        emit('{} halt event(s) this week. A Polymarket halt blocks entries '
             'only and cannot flatten, so open exposure survived it.'
             .format(ev['halts']))

    if sc['configured_status'] == 'OK' and sc['configured_but_never_logged']:
        emit('Configured but never logged: {}. Restart the loop to pick them '
             'up (convention 13: a running process snapshotted its imports).'
             .format(', '.join(sc['configured_but_never_logged'])))

    if data['forge']['status'] == 'OK' and \
            data['forge']['proposals_in_window']:
        emit('{} Forge proposal(s) carry an mtime inside this window and are '
             'awaiting review against strategies/proposals/README.md.'
             .format(len(data['forge']['proposals_in_window'])))

    if not fired:
        lines.append('  No numeric rule fired. Nothing in the week\'s numbers '
                     'triggers an action.')
    lines.append('')
    lines.append('  Every line above is emitted by a rule over the numbers in')
    lines.append('  this report. None of it is inferred beyond them.')
    return lines


def build_parser():
    p = argparse.ArgumentParser(
        description='Weekly paper/shadow trading summary (Eastern time).')
    p.add_argument('--week-ending', default=None,
                   help='YYYY-MM-DD, last day of the 7-day ET window '
                        '(default: today ET)')
    p.add_argument('--db', default=DEFAULT_DB_PATH,
                   help='path to trading.db (default: %(default)s)')
    p.add_argument('--csv', default=DEFAULT_CSV_PATH,
                   help='paper adapter decision CSV (cross check only)')
    p.add_argument('--json', action='store_true',
                   help='machine-readable output')
    p.add_argument('--send', action='store_true',
                   help='write the rendered message to logs/summaries/ and '
                        'print the path. This does NOT deliver it.')
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        week_ending = (parse_date(args.week_ending) if args.week_ending
                       else today_et())
        data = collect(week_ending, args.db, args.csv)
    except DataSourceError as exc:
        target = args.week_ending or 'week ending today'
        print(error_report('weekly', target, str(exc), as_json=args.json))
        return 2

    text = render(data)
    if args.json:
        print(dump_json(data))
    else:
        print(text)
    if args.send:
        path = write_summary_file(
            text, 'weekly_{}.txt'.format(data['week_ending_et']))
        print('\nwritten for delivery: {}'.format(path))
        print('NOT sent. Cron or Raven picks this file up.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
