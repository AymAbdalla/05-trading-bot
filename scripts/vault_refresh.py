#!/usr/bin/env python3
"""Rebuild the vault's Trading notes from what the database currently says.

## Why this exists rather than a one-off rewrite

Raven hand-wrote the first lessons on 2026-08-18. They were accurate when
written and were already stale hours later, because the shadow loop never
stops trading. At the time of the first refresh, the lesson file said
fair_value_arb had 503 trades at a 21% win rate; the database said 255 trades
at 32.5%. A lesson that carries a number nobody re-derived is a lesson that
will be cited with a wrong number.

So the note is not the artifact. THIS SCRIPT is the artifact, and the note is
its output. Re-run it and every number is re-derived from `positions` and
`signals`; the reasoning on top of those numbers is composed by Opus through
`agents/vault_writer.py`, which refuses to overwrite a good note with an empty
turn and stamps every file with the model that wrote it.

Convention 2 in spirit: cite the derivation, not the pass count.

## What it will not do

It will not invent a number for the model. Every figure the model is allowed
to use is in the evidence block, and the prompts (in `vault_writer`) tell it
to mark anything else NOT_MEASURED. If a strategy has never traded, its
evidence block says so in those words, because Convention 11 makes
"never fired" and "fired and lost" different findings that must not share a
counter.

Usage:
    env -u PYTHONPATH python3 scripts/vault_refresh.py --list
    env -u PYTHONPATH python3 scripts/vault_refresh.py --all --skip-model
    env -u PYTHONPATH python3 scripts/vault_refresh.py --note fair-value-arb-lesson
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents import llm_client, vault_reader, vault_writer  # noqa: E402

DB_PATH = os.path.join(ROOT, 'db', 'trading.db')

# How many individual trades to show per strategy. Enough for the model to
# quote a real example; bounded so the prompt cannot grow with the log.
SAMPLE_TRADES = 12

# Below this many closed trades, any verdict is provisional. Convention 7:
# a FAIL on 200k trades is a verdict, on 1,700 a shrug.
PROVISIONAL_BELOW = 30


def connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Read-only. The shadow loop is usually mid-write on this file."""
    return sqlite3.connect('file:%s?mode=ro' % db_path, uri=True, timeout=20)


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return '(no rows)'
    out = ['| %s |' % ' | '.join(headers),
           '|%s|' % '|'.join(['---'] * len(headers))]
    for row in rows:
        out.append('| %s |' % ' | '.join('' if v is None else str(v)
                                         for v in row))
    return '\n'.join(out)


def _hold_seconds(opened: Optional[int], closed: Optional[int]) -> str:
    if opened is None or closed is None:
        return ''
    return '%.1f' % ((closed - opened) / 1000.0)


def window_facts(conn: sqlite3.Connection) -> str:
    """When the data starts and ends, and where equity is now."""
    row = conn.execute(
        'SELECT MIN(opened_ts), MAX(closed_ts), COUNT(*) FROM positions '
        'WHERE closed_ts IS NOT NULL').fetchone()
    lines = []
    if row and row[0]:
        lines.append('Closed-trade window: %s to %s UTC'
                     % (_iso(row[0]), _iso(row[1])))
        lines.append('Closed trades in the database: %d' % row[2])
    equity = conn.execute(
        'SELECT ts, equity FROM equity_snapshots ORDER BY ts DESC '
        'LIMIT 1').fetchone()
    if equity:
        lines.append('Latest equity snapshot: $%.2f at %s UTC'
                     % (equity[1], _iso(equity[0])))
    lines.append('Data pulled at: %s UTC'
                 % datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'))
    return '\n'.join(lines)


def _iso(ms: Optional[int]) -> str:
    if ms is None:
        return '?'
    return datetime.fromtimestamp(ms / 1000.0,
                                  timezone.utc).strftime('%Y-%m-%d %H:%M:%S')


def strategy_evidence(conn: sqlite3.Connection,
                      strategies: Sequence[str]) -> str:
    """Everything the database knows about one strategy or one family."""
    placeholders = ','.join('?' * len(strategies))
    parts: List[str] = [window_facts(conn), '']

    totals = conn.execute(
        'SELECT strategy_id, COUNT(*),'
        '       ROUND(SUM(pnl_net), 2),'
        '       SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END),'
        '       ROUND(AVG(pnl_net), 4),'
        '       ROUND(AVG(CASE WHEN pnl_net > 0 THEN pnl_net END), 3),'
        '       ROUND(AVG(CASE WHEN pnl_net <= 0 THEN pnl_net END), 3),'
        '       ROUND(AVG((closed_ts - opened_ts) / 1000.0), 1),'
        '       ROUND(AVG(entry_px), 4), ROUND(AVG(exit_px), 4),'
        '       ROUND(SUM(fees), 3)'
        ' FROM positions WHERE closed_ts IS NOT NULL'
        ' AND strategy_id IN (%s) GROUP BY strategy_id'
        ' ORDER BY SUM(pnl_net)' % placeholders,
        tuple(strategies)).fetchall()

    parts.append('## Closed-trade totals')
    parts.append('')
    if not totals:
        parts.append('NO CLOSED TRADES for %s. Convention 11: this is '
                     'NOT_TESTED, it is not a failure, and it must not be '
                     'read as evidence that the idea does not work.'
                     % ', '.join(strategies))
    else:
        rows = []
        for t in totals:
            win_rate = ('%.1f' % (100.0 * t[3] / t[1])) if t[1] else '?'
            rows.append([t[0], t[1], win_rate, t[2], t[4], t[5], t[6],
                         t[7], t[8], t[9], t[10]])
        parts.append(_table(
            ['strategy', 'trades', 'win_rate_%', 'total_pnl_net',
             'avg_pnl', 'avg_win', 'avg_loss', 'avg_hold_s', 'avg_entry_px',
             'avg_exit_px', 'total_fees'], rows))
        small = [t[0] for t in totals if t[1] < PROVISIONAL_BELOW]
        if small:
            parts.append('')
            parts.append('PROVISIONAL: %s have fewer than %d closed trades. '
                         'Any verdict on them is a shrug, not a result '
                         '(Convention 7). Say the sample size next to any '
                         'claim about them.'
                         % (', '.join(small), PROVISIONAL_BELOW))
    parts.append('')

    parts.append('## Exit reasons')
    parts.append('')
    exits = conn.execute(
        'SELECT strategy_id, exit_reason, COUNT(*),'
        '       ROUND(SUM(pnl_net), 2), ROUND(AVG(pnl_net), 3)'
        ' FROM positions WHERE closed_ts IS NOT NULL'
        ' AND strategy_id IN (%s)'
        ' GROUP BY strategy_id, exit_reason'
        ' ORDER BY strategy_id, COUNT(*) DESC' % placeholders,
        tuple(strategies)).fetchall()
    parts.append(_table(['strategy', 'exit_reason', 'count', 'total_pnl',
                         'avg_pnl'], exits))
    parts.append('')

    parts.append('## Individual trades: the %d worst and the %d best'
                 % (SAMPLE_TRADES // 2, SAMPLE_TRADES // 2))
    parts.append('')
    sample_rows = []
    for order in ('ASC', 'DESC'):
        for r in conn.execute(
                'SELECT strategy_id, pair, entry_px, exit_px, qty, stop_px,'
                '       target_px, pnl_net, fees, exit_reason, opened_ts,'
                '       closed_ts FROM positions'
                ' WHERE closed_ts IS NOT NULL AND strategy_id IN (%s)'
                ' ORDER BY pnl_net %s LIMIT ?'
                % (placeholders, order),
                tuple(strategies) + (SAMPLE_TRADES // 2,)).fetchall():
            sample_rows.append(list(r[:10]) + [_hold_seconds(r[10], r[11])])
    parts.append(_table(
        ['strategy', 'pair', 'entry_px', 'exit_px', 'qty', 'stop_px',
         'target_px', 'pnl_net', 'fees', 'exit_reason', 'hold_s'],
        sample_rows))
    parts.append('')

    parts.append('## Why it did NOT fire: skip reasons')
    parts.append('')
    parts.append('These are signals evaluated and skipped. A large count here '
                 'is a gate, not a loss. Convention 20: each reason is its '
                 'own counter and two drop causes never share one.')
    parts.append('')
    skips = conn.execute(
        'SELECT strategy_id, skip_reason, COUNT(*) FROM signals'
        ' WHERE strategy_id IN (%s) AND acted = 0'
        ' AND skip_reason IS NOT NULL'
        ' GROUP BY strategy_id, skip_reason'
        ' ORDER BY COUNT(*) DESC LIMIT 40' % placeholders,
        tuple(strategies)).fetchall()
    parts.append(_table(['strategy', 'skip_reason', 'count'], skips))
    acted = conn.execute(
        'SELECT strategy_id, SUM(acted), COUNT(*) FROM signals'
        ' WHERE strategy_id IN (%s) GROUP BY strategy_id' % placeholders,
        tuple(strategies)).fetchall()
    parts.append('')
    parts.append(_table(['strategy', 'signals_acted', 'signals_evaluated'],
                        acted))
    return '\n'.join(parts)


def cycle_evidence(conn: sqlite3.Connection) -> str:
    """The whole session: every strategy, the equity path, the gates."""
    parts: List[str] = [window_facts(conn), '', '## Every strategy that '
                        'closed a trade', '']
    totals = conn.execute(
        'SELECT strategy_id, COUNT(*),'
        '       SUM(CASE WHEN pnl_net > 0 THEN 1 ELSE 0 END),'
        '       ROUND(SUM(pnl_net), 2), ROUND(AVG(pnl_net), 4),'
        '       ROUND(AVG((closed_ts - opened_ts) / 1000.0), 1)'
        ' FROM positions WHERE closed_ts IS NOT NULL'
        ' GROUP BY strategy_id ORDER BY SUM(pnl_net)').fetchall()
    rows = [[t[0], t[1], '%.1f' % (100.0 * t[2] / t[1]) if t[1] else '?',
             t[3], t[4], t[5]] for t in totals]
    parts.append(_table(['strategy', 'trades', 'win_rate_%', 'total_pnl_net',
                         'avg_pnl', 'avg_hold_s'], rows))
    parts.append('')

    traded = {t[0] for t in totals}
    evaluated = {r[0] for r in conn.execute(
        "SELECT DISTINCT strategy_id FROM signals "
        "WHERE strategy_id LIKE 'PM_%'").fetchall()}
    never = sorted(evaluated - traded)
    parts.append('## Evaluated but NEVER traded')
    parts.append('')
    if never:
        parts.append('These fired zero trades. Convention 11: NOT_TESTED. '
                     'They are not failures and must not be counted as '
                     'evidence against their idea.')
        parts.append('')
        blockers = conn.execute(
            'SELECT strategy_id, skip_reason, COUNT(*) FROM signals'
            ' WHERE strategy_id IN (%s) AND acted = 0'
            ' AND skip_reason IS NOT NULL GROUP BY strategy_id, skip_reason'
            ' ORDER BY strategy_id, COUNT(*) DESC'
            % ','.join('?' * len(never)), tuple(never)).fetchall()
        parts.append(_table(['strategy', 'skip_reason', 'count'], blockers))
    else:
        parts.append('(none: every evaluated strategy traded at least once)')
    parts.append('')

    parts.append('## Equity path (every 25th snapshot)')
    parts.append('')
    snaps = conn.execute(
        'SELECT ts, equity FROM equity_snapshots ORDER BY ts').fetchall()
    parts.append(_table(['time_utc', 'equity'],
                        [[_iso(s[0]), round(s[1], 2)]
                         for s in snaps[::25]][-40:]))
    parts.append('')

    parts.append('## Top skip reasons across the whole loop')
    parts.append('')
    skips = conn.execute(
        "SELECT skip_reason, COUNT(*), COUNT(DISTINCT strategy_id)"
        " FROM signals WHERE acted = 0 AND skip_reason IS NOT NULL"
        " AND strategy_id LIKE 'PM_%' GROUP BY skip_reason"
        " ORDER BY COUNT(*) DESC LIMIT 30").fetchall()
    parts.append(_table(['skip_reason', 'count', 'distinct_strategies'],
                        skips))
    acted = conn.execute(
        "SELECT acted, COUNT(*) FROM signals WHERE strategy_id LIKE 'PM_%'"
        " GROUP BY acted").fetchall()
    parts.append('')
    parts.append(_table(['acted', 'signals'], acted))
    return '\n'.join(parts)


# ---------------------------------------------------------------------------
# The notes this script maintains
# ---------------------------------------------------------------------------
#
# Each entry names the exact file it rewrites, so a refresh replaces the note
# in place rather than growing a second one beside it with a newer date.

FAIR_VALUE_FAMILY = ('PM_fair_value_arb', 'PM_fair_value_arb_hft',
                     'PM_fair_value_arb_wide', 'PM_fair_value_arb_patient',
                     'PM_fair_value_arb_inverse')

CORRIDOR_FAMILY = ('PM_corridor_pair_live', 'PM_corridor_pair',
                   'PM_corridor_collector')

NOTES: Dict[str, Dict[str, Any]] = {
    'fair-value-arb-lesson': {
        'kind': 'lesson',
        'strategies': FAIR_VALUE_FAMILY,
        'subject': 'PM_fair_value_arb (and its four parameter variants)',
        'filename': '2026-08-18-fair-value-arb-spread-problem.md',
        'status': 'TESTED_FAILED',
        'failure_mode': 'spread_eats_edge',
    },
    'corridor-pair-lesson': {
        'kind': 'lesson',
        'strategies': CORRIDOR_FAMILY,
        'subject': 'PM_corridor_pair_live and the corridor family',
        'filename': '2026-08-18-corridor-pair-works.md',
        'status': 'TESTED_CONFIRMED (small sample)',
        'failure_mode': '',
    },
    'fair-value-arb-card': {
        'kind': 'card',
        'strategies': FAIR_VALUE_FAMILY,
        'subject': 'Fair Value Arbitrage',
        'filename': 'fair_value_arb.md',
    },
    'corridor-pair-card': {
        'kind': 'card',
        'strategies': CORRIDOR_FAMILY,
        'subject': 'Corridor Pair Live',
        'filename': 'corridor_pair_live.md',
    },
    'cycle-001': {
        'kind': 'cycle',
        'subject': 'cycle-001 day 1 lessons',
        'filename': '2026-08-18-cycle-001-day-1-lessons.md',
    },
}


def refresh(key: str, conn: sqlite3.Connection, skip_model: bool = False,
            out_dir: Optional[str] = None,
            timeout_s: int = llm_client.DEFAULT_TIMEOUT_S
            ) -> vault_writer.VaultWrite:
    """Rebuild one note."""
    spec = NOTES[key]
    vault_context = vault_reader.render_context()

    if spec['kind'] == 'cycle':
        evidence = cycle_evidence(conn)
        return vault_writer.write_cycle_summary(
            spec['subject'], evidence, vault_context=vault_context,
            out_dir=out_dir, filename=spec['filename'], skip_model=skip_model,
            timeout_s=timeout_s)

    evidence = strategy_evidence(conn, spec['strategies'])
    if spec['kind'] == 'lesson':
        return vault_writer.write_strategy_lesson(
            spec['subject'], evidence, vault_context=vault_context,
            status=spec['status'], failure_mode=spec['failure_mode'],
            out_dir=out_dir, filename=spec['filename'], skip_model=skip_model,
            timeout_s=timeout_s)
    return vault_writer.write_strategy_card(
        spec['subject'], evidence, vault_context=vault_context,
        out_dir=out_dir, filename=spec['filename'], skip_model=skip_model,
        timeout_s=timeout_s)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--note', action='append', default=[],
                        choices=sorted(NOTES), help='rebuild one note')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--list', action='store_true')
    parser.add_argument('--evidence-only', action='store_true',
                        help='print the evidence block, call no model')
    parser.add_argument('--skip-model', '--dry-run', action='store_true',
                        dest='skip_model',
                        help='write the deterministic FALLBACK note instead of '
                             'calling a model. This still WRITES a file. '
                             '--dry-run is kept as an alias because that is '
                             'what it was called first, but the name was a '
                             'trap: it wrote into the real vault.')
    parser.add_argument('--out-dir', default=None,
                        help='override the vault directory (for testing)')
    parser.add_argument('--db', default=DB_PATH)
    parser.add_argument('--timeout', type=int,
                        default=llm_client.DEFAULT_TIMEOUT_S)
    args = parser.parse_args(argv)

    if args.list:
        for key in sorted(NOTES):
            spec = NOTES[key]
            print('%-24s %-7s -> %s' % (key, spec['kind'], spec['filename']))
        return 0

    keys = sorted(NOTES) if args.all else args.note
    if not keys:
        parser.error('pass --note KEY, --all, or --list')

    conn = connect(args.db)
    try:
        if args.evidence_only:
            for key in keys:
                spec = NOTES[key]
                print('=' * 70)
                print('# %s -> %s' % (key, spec['filename']))
                print('=' * 70)
                print(cycle_evidence(conn) if spec['kind'] == 'cycle'
                      else strategy_evidence(conn, spec['strategies']))
                print()
            return 0

        failures = 0
        for key in keys:
            result = refresh(key, conn, skip_model=args.skip_model,
                             out_dir=args.out_dir, timeout_s=args.timeout)
            print('%-24s %s' % (key, llm_client.dump_json(result.to_dict(),
                                                          indent=None)))
            if not result.written or (not result.used_model
                                      and not args.skip_model):
                failures += 1
        # Convention 20: report the count, do not let a fallback pass as a
        # success just because a file appeared on disk.
        print('\n%d/%d notes were composed by a model.'
              % (len(keys) - failures, len(keys)))
        return 1 if failures else 0
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
