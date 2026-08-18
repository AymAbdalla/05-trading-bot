#!/usr/bin/env python3
"""One Eastern-time day of paper/shadow trading, summarised.

    env -u PYTHONPATH python3 scripts/daily_shadow_summary.py
    env -u PYTHONPATH python3 scripts/daily_shadow_summary.py --date 2026-08-17
    env -u PYTHONPATH python3 scripts/daily_shadow_summary.py --json
    env -u PYTHONPATH python3 scripts/daily_shadow_summary.py --send

Reads `db/trading.db` (authoritative) and the paper adapter's decision CSV
(cross check only). Both are written by `engine/polymarket/shadow_loop.py`.

## What `--send` actually does, and does not

It does NOT send anything. Delivery to Aym runs over the Hermes MCP tool
`messages_send`, and MCP tools are exposed to the agent session, not to a
subprocess: this script has no channel to call one, and inventing an HTTP
shim would be a second delivery path nobody tests. So `--send` renders the
message to `logs/summaries/daily_<date>.txt` and prints the path. A cron job or
Raven reads the file and delivers it. Exit code 0 means the file was written,
never that a message arrived.

## Zero entries is the live case, and it is not a flat day

At the time of writing the shadow loop has produced zero entries, so almost
everything here is skip accounting. That case is handled explicitly:

  - a win rate over zero decided trades prints `n/a (0 trades)`, not `0.0%`
  - a profit factor over zero losses prints `n/a (0 losing trades)`, not `inf`
  - an unreadable database prints an ERROR report and exits 2, never a summary
    reading "0 trades" (convention 11)
  - every skip reason is counted AND categorised, and the buckets are asserted
    to sum to the skip total (convention 20)

The DATA BLOCKER flag on a skip reason is the number that matters while
nothing is trading: a strategy skipping because an input is missing is
NOT_TESTED, and must never be read as a strategy that looked and declined.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from shadow_summary_lib import (  # noqa: E402
    DEFAULT_CSV_PATH, DEFAULT_DB_PATH, DataSourceError, aggregate_signals,
    aggregate_trades, check_schema, dump_json, error_report, et_day_bounds_ms,
    first_trade_ever, fmt_pct_of, fmt_pf, fmt_rate, fmt_usd, ms_to_et_string,
    open_db, open_exposure, parse_date, read_csv_rows, read_equity,
    read_events, read_fills, read_forge, read_positions, read_signals,
    render_categories, render_equity, render_skip_table, rule,
    summarise_events, today_et, write_summary_file, DATA_BLOCKER, ERROR,
    UNCLASSIFIED)


def collect(day, db_path, csv_path):
    """Read every source for one ET day. Raises DataSourceError on the DB."""
    start_ms, end_ms = et_day_bounds_ms(day)
    conn = open_db(db_path)
    try:
        check_schema(conn, db_path)
        signals = read_signals(conn, start_ms, end_ms)
        opened, closed = read_positions(conn, start_ms, end_ms)
        fills = read_fills(conn, start_ms, end_ms)
        equity = read_equity(conn, start_ms, end_ms)
        audit_rows, risk_rows = read_events(conn, start_ms, end_ms)
        first_ever = first_trade_ever(conn)
        still_open = [p for p in opened if p.get('closed_ts') is None]
    finally:
        conn.close()

    decisions = aggregate_signals(signals)
    trades = aggregate_trades(closed)
    events = summarise_events(audit_rows, risk_rows)
    csv_stats = read_csv_rows(csv_path, start_ms / 1000.0, end_ms / 1000.0)
    forge = read_forge(start_ms, end_ms)

    return {
        'report': 'daily',
        'date_et': day.isoformat(),
        'window_start_ms': start_ms,
        'window_end_ms': end_ms,
        'db_path': db_path,
        'csv_path': csv_path,
        'status': 'OK',
        'decisions': decisions,
        'trades': trades,
        'entries_opened': len(opened),
        'fills': fills,
        'open_at_cutoff': open_exposure(still_open),
        'equity': equity,
        'events': events,
        'first_trade_ever_ms': first_ever,
        'first_trade_ever_is_today': (
            first_ever is not None and start_ms <= first_ever < end_ms),
        'csv': csv_stats,
        'forge': forge,
    }


def render(data):
    d = data['decisions']
    t = data['trades']
    eq = data['equity']
    ev = data['events']
    out = []
    a = out.append

    a('SHADOW DAILY {} (ET)'.format(data['date_et']))
    a('db: {}'.format(os.path.relpath(data['db_path'])))
    a('window: {} to {}'.format(ms_to_et_string(data['window_start_ms']),
                                ms_to_et_string(data['window_end_ms'])))
    a('')

    a(rule('DECISIONS'))
    a('  evaluations   {:>8}'.format(d['evaluations']))
    a('  entries       {:>8}'.format(d['entries']))
    a('  fills         {:>8}   (fees {})'.format(
        data['fills']['count'], fmt_usd(data['fills']['fees'])))
    a('  skips         {:>8}'.format(d['skips']))
    a('  identity      {:>8}   evaluations == entries + skips'.format(
        'OK' if d['identity_ok'] else 'VIOLATED'))
    if d['evaluations'] == 0:
        a('  NOTE: no evaluation rows in this window. Either the loop was not')
        a('        running or it wrote elsewhere. This is NOT a quiet market.')
    a('')

    a(rule('TRADES'))
    a('  positions opened {:>6}'.format(data['entries_opened']))
    a('  positions closed {:>6}'.format(t['closed']))
    a('  wins / losses / flat   {} / {} / {}'.format(
        t['wins'], t['losses'], t['flats']))
    if t['unpriced']:
        a('  unpriced closed rows   {}  (pnl_net NULL; excluded from P&L)'
          .format(t['unpriced']))
    a('  win rate         {}'.format(fmt_rate(t['win_rate'], t['decided'])))
    a('  profit factor    {}'.format(fmt_pf(t['profit_factor'], t['losses'])))
    a('  realised P&L     {}'.format(fmt_usd(t['pnl'])))
    a('  avg per trade    {}'.format(
        'n/a (0 trades)' if t['avg_pnl'] is None else fmt_usd(t['avg_pnl'])))
    a('  still open at cutoff  {}  (premium at risk {})'.format(
        data['open_at_cutoff']['count'],
        fmt_usd(data['open_at_cutoff']['premium_at_risk'])))
    if t['closed'] == 0:
        a('  NOTE: nothing closed today, so win rate and profit factor are')
        a('        undefined rather than zero. No edge claim is possible.')
    a('')

    a(rule('EQUITY (paper USDC)'))
    out.extend(render_equity(eq))
    if eq['status'] == 'OK' and d['entries'] == 0:
        a('  Flat because nothing traded, not because trades netted to zero.')
    a('')

    a(rule('PER STRATEGY'))
    a('  {:<30}{:>7}{:>8}{:>7}  {:<16}{:>10}'.format(
        'strategy', 'evals', 'entries', 'skips', 'win rate', 'P&L'))
    per_trade = t['per_strategy']
    names = sorted(set(d['per_strategy']) | set(per_trade))
    if not names:
        a('  none')
    for name in names:
        sig = d['per_strategy'].get(
            name, {'evaluations': 0, 'entries': 0, 'skips': 0})
        tr = per_trade.get(name)
        if tr is None:
            wr, pnl = 'n/a (0 trades)', 0.0
        else:
            wr = fmt_rate(tr['win_rate'], tr['wins'] + tr['losses'])
            pnl = tr['pnl']
        a('  {:<30}{:>7}{:>8}{:>7}  {:<16}{:>10}'.format(
            name[:30], sig['evaluations'], sig['entries'], sig['skips'],
            wr, fmt_usd(pnl)))
    a('')

    a(rule('TOP SKIP REASONS'))
    a('  {:>6}  {:<34} {:>6}  {}'.format('count', 'reason', 'share', 'class'))
    out.extend(render_skip_table(d['reasons']))
    a('')

    a(rule('SKIP CATEGORIES'))
    out.extend(render_categories(d['categories'], d['unclassified_reasons']))
    blocked = d['categories'].get(DATA_BLOCKER, 0)
    if blocked:
        a('  {} of {} skips ({}) are DATA BLOCKERS. Those windows are'.format(
            blocked, d['skips'], fmt_pct_of(blocked, d['skips'])))
        a('  NOT_TESTED, not tested-and-declined (convention 11).')
    a('')

    a(rule('BLOCKED STRATEGIES'))
    blocked_names = []
    for name, bucket in sorted(d['per_strategy'].items()):
        cats = bucket.get('categories') or {}
        if bucket['entries'] == 0 and bucket['skips'] > 0 and \
                cats.get(DATA_BLOCKER, 0) == bucket['skips']:
            blocked_names.append(name)
    if blocked_names:
        for name in blocked_names:
            a('  {}  100% of skips are data blockers: NOT_TESTED today'
              .format(name))
    else:
        a('  none (every strategy reached at least one real decision)')
    a('')

    a(rule('NOTABLE EVENTS'))
    if ev['notable']:
        for item in ev['notable']:
            a('  {}  {}'.format(ms_to_et_string(item['ts_ms']), item['event']))
    else:
        a('  none')
    a('  halts {} / resumes {} / accounting violations {}'.format(
        ev['halts'], ev['resumes'], ev['accounting_violations']))
    a('  sessions started {} / stopped {}'.format(
        ev['sessions_started'], ev['sessions_stopped']))
    if ev['loop_identity_violations']:
        a('  LOOP REPORTED {} IDENTITY VIOLATIONS. Investigate before citing'
          .format(ev['loop_identity_violations']))
        a('  any count in this report.')
    if ev['loop_health']:
        a('  loop health counters: {}'.format(
            ', '.join('{}={}'.format(k, v)
                      for k, v in sorted(ev['loop_health'].items()))))
    if data['first_trade_ever_ms'] is None:
        a('  FIRST TRADE EVER: none. No position has ever been recorded.')
    elif data['first_trade_ever_is_today']:
        a('  FIRST TRADE EVER recorded today at {}'.format(
            ms_to_et_string(data['first_trade_ever_ms'])))
    a('')

    a(rule('FORGE'))
    f = data['forge']
    if f['status'] != 'OK':
        a('  NOT_TESTED: {}'.format(f['note']))
    else:
        a('  proposals with mtime in this window: {} of {} on disk'.format(
            len(f['proposals_in_window']), f['proposals_total']))
        for item in f['proposals_in_window']:
            a('    {}  {}'.format(item['file'],
                                  ms_to_et_string(item['mtime_ms'])))
        a('  forge_runs.jsonl: {} run record(s), file mtime {}{}'.format(
            f['runs_total'], ms_to_et_string(f['runs_file_mtime']),
            ' (in window)' if f['runs_file_mtime_in_window'] else ''))
        if f['refused_by_category']:
            a('  refusals by category: {}'.format(
                ', '.join('{}={}'.format(k, v) for k, v
                          in sorted(f['refused_by_category'].items()))))
        a('  attribution: {}'.format(f['attribution']))
    a('')

    a(rule('SOURCE RECONCILIATION'))
    csv_stats = data['csv']
    a('  db signals rows in window   {}'.format(d['evaluations']))
    if csv_stats['status'] != 'OK':
        a('  csv {}: {}'.format(csv_stats['status'], csv_stats['note']))
        a('  NOT_TESTED as a cross check. DB numbers above still stand.')
    else:
        a('  csv decision rows in window {}'.format(csv_stats['rows']))
        delta = csv_stats['rows'] - d['evaluations']
        a('  delta                       {:+d}'.format(delta))
        if delta:
            a('  The two sources are appended by different lifetimes: the CSV')
            a('  survives a replaced database, and one loop path suppresses')
            a('  its CSV row when the adapter already wrote one. A non-zero')
            a('  delta is reported, not reconciled away.')
        if csv_stats['actions']:
            a('  csv actions: {}'.format(
                ', '.join('{}={}'.format(k, v) for k, v
                          in sorted(csv_stats['actions'].items()))))
        if csv_stats.get('unknown_actions'):
            a('  UNKNOWN csv actions (no rule): {}'.format(
                ', '.join(csv_stats['unknown_actions'])))
        if csv_stats['unparsable_ts']:
            a('  csv rows with an unparsable ts: {} (excluded, counted)'
              .format(csv_stats['unparsable_ts']))
    a('')

    a(rule('READ THIS BEFORE QUOTING A NUMBER'))
    caveats = 0
    if d['entries'] == 0:
        caveats += 1
        a('  Zero entries. Nothing was graded today. Convention 11: this is')
        a('  NOT_TESTED, not a strategy set that looked and found nothing.')
    if t['decided'] and t['decided'] < 30:
        caveats += 1
        a('  {} decided trades. A verdict needs a sample; this is a shrug'
          .format(t['decided']))
        a('  (convention 7).')
    if not d['identity_ok'] or ev['loop_identity_violations']:
        caveats += 1
        a('  The accounting identity did not hold. Every count above is')
        a('  suspect until that is explained (convention 20).')
    if d['categories'].get(ERROR, 0):
        caveats += 1
        a('  {} skips are in the ERROR bucket. Those are our bugs, not'
          .format(d['categories'][ERROR]))
        a('  market conditions.')
    if d['categories'].get(UNCLASSIFIED, 0):
        caveats += 1
        a('  {} skips are UNCLASSIFIED. Add a rule to shadow_summary_lib'
          .format(d['categories'][UNCLASSIFIED]))
        a('  before citing the category split.')
    if caveats == 0:
        a('  no caveats triggered')
    return '\n'.join(out)


def build_parser():
    p = argparse.ArgumentParser(
        description='Daily paper/shadow trading summary (Eastern time).')
    p.add_argument('--date', default=None,
                   help='YYYY-MM-DD in Eastern time (default: today ET)')
    p.add_argument('--db', default=DEFAULT_DB_PATH,
                   help='path to trading.db (default: %(default)s)')
    p.add_argument('--csv', default=DEFAULT_CSV_PATH,
                   help='paper adapter decision CSV (cross check only)')
    p.add_argument('--json', action='store_true',
                   help='machine-readable output')
    p.add_argument('--send', action='store_true',
                   help='write the rendered message to logs/summaries/ and '
                        'print the path. This does NOT deliver it; see the '
                        'module docstring.')
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        day = parse_date(args.date) if args.date else today_et()
        data = collect(day, args.db, args.csv)
    except DataSourceError as exc:
        target = args.date or 'today'
        print(error_report('daily', target, str(exc), as_json=args.json))
        return 2

    text = render(data)
    if args.json:
        print(dump_json(data))
    else:
        print(text)
    if args.send:
        path = write_summary_file(
            text, 'daily_{}.txt'.format(data['date_et']))
        print('\nwritten for delivery: {}'.format(path))
        print('NOT sent. Cron or Raven picks this file up.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
