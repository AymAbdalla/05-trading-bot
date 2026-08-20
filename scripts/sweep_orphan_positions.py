#!/usr/bin/env python3
"""Sweep orphaned `positions` rows left behind by a dead shadow process (D-353).

WHY THIS EXISTS
---------------
`closed_ts IS NULL` is supposed to mean "this position is live right now". Every
process death breaks that invariant: the rows the dead loop held open are never
closed by anyone, so they accumulate forever. D-353 measured the damage - any
analysis keying `closed_ts IS NULL` as "currently open" over-counts several-fold,
and lifetime `sum(pnl_net)` silently EXCLUDES the premium those rows spent, so
the book reads better than it was.

THE BOUNDARY IS A PROCESS START TIME, NOT A DATE (D-363 R1)
-----------------------------------------------------------
An orphan is a row that was open BEFORE the currently-owning process started.
Rows opened at or after that instant belong to the live loop and MUST NOT be
touched - sweeping one would book a loss on a position that is still trading.
Pass `--pid` and the boundary is read from the OS (`ps -o lstart`); pass
`--boundary-ms` only when the owning process is already gone.

Because the boundary is the live process's own start, the orphan cohort is
FROZEN while that process runs: nothing can create a new pre-boundary NULL row.
That is what makes it safe to sweep a database that is being written to.

BOOKING: EXIT AT 0.00, FULL PREMIUM REALIZED AS LOSS (D-353 R2)
---------------------------------------------------------------
The premium was genuinely spent and never recovered; the position is dead.
Flat-booking (exit at entry, pnl 0) would perpetuate exactly the understatement
D-353 exists to fix. `pnl_gross`/`pnl_net`/`r_multiple` are computed with the
SAME arithmetic `engine/adapters/paper.py:close_position` uses, so a swept row
is arithmetically indistinguishable from an ordinary close at 0.00 - one
booking convention, not two.

Swept rows carry `exit_reason = 'orphaned:process_death'`. That string is the
ONLY marker downstream code has, and D-353 R4 requires them excluded from 038's
settlement coverage - see `backtest/settlement_coverage.py`, which filters on it.
They are not entries, not exits, and not resolutions.

SAFETY
------
- Snapshots the database through the sqlite BACKUP API first (never `cp`: a live
  WAL database copied with `cp` is a torn read).
- ONE transaction per book. The row count changed must equal the census taken
  inside that same transaction, or everything rolls back.
- `PRAGMA integrity_check` after commit.
- `--dry-run` is the default posture for a first look; nothing writes without
  `--apply`.

Usage:
    python3 scripts/sweep_orphan_positions.py --db db/trading.db --pid 22570
    python3 scripts/sweep_orphan_positions.py --db db/trading.db --pid 22570 --apply
"""

import argparse
import os
import sqlite3
import subprocess
import sys
import time

ORPHAN_EXIT_REASON = 'orphaned:process_death'


def process_start_ms(pid: int) -> int:
    """Epoch ms at which `pid` started, read from the OS.

    `ps -o lstart` is the only field that gives an ABSOLUTE start instant;
    `etime` is a duration and turning it into an instant means reading the wall
    clock twice and subtracting, which drifts. Raises if the pid is gone: a
    boundary guessed for a dead process would sweep live rows.
    """
    out = subprocess.run(['ps', '-o', 'lstart=', '-p', str(pid)],
                         capture_output=True, text=True)
    stamp = (out.stdout or '').strip()
    if not stamp:
        raise SystemExit(
            'REFUSING: pid %d is not running, so its start time cannot be read. '
            'Pass --boundary-ms explicitly if you know it.' % pid)
    return int(time.mktime(time.strptime(stamp, '%a %b %d %H:%M:%S %Y')) * 1000)


def snapshot(db_path: str) -> str:
    """Back the database up through the sqlite backup API. Returns the path."""
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    dest_path = '%s.presweep-%s.bak' % (db_path, stamp)
    src = sqlite3.connect('file:%s?mode=ro' % db_path, uri=True)
    dest = sqlite3.connect(dest_path)
    with dest:
        src.backup(dest)
    dest.close()
    src.close()
    return dest_path


def census(conn, boundary_ms: int):
    """(count, cost_basis) of pre-boundary rows still open. Read inside the txn."""
    row = conn.execute(
        'SELECT COUNT(*), COALESCE(SUM(entry_px * qty), 0.0) FROM positions '
        'WHERE closed_ts IS NULL AND opened_ts < ?', (boundary_ms,)).fetchone()
    return int(row[0]), float(row[1])


def sweep(db_path: str, boundary_ms: int, apply: bool) -> dict:
    # A generous busy timeout: the owning loop writes on every poll, so
    # BEGIN IMMEDIATE will collide. Failing instantly on a lock would read as
    # "the sweep does not work" when it just needed to wait one poll.
    conn = sqlite3.connect(db_path, timeout=60.0)
    conn.row_factory = sqlite3.Row
    result = {'db': db_path, 'boundary_ms': boundary_ms, 'applied': False}

    # A single transaction: census and UPDATE must see the same rows. Counting
    # outside the transaction and updating inside it is the classic way to book
    # a loss on a row that became live in between.
    conn.execute('BEGIN IMMEDIATE')
    try:
        count, cost_basis = census(conn, boundary_ms)
        result['orphans'] = count
        result['cost_basis'] = cost_basis
        live = conn.execute(
            'SELECT COUNT(*) FROM positions WHERE closed_ts IS NULL '
            'AND opened_ts >= ?', (boundary_ms,)).fetchone()[0]
        result['left_live'] = int(live)

        if not apply or count == 0:
            conn.execute('ROLLBACK')
            conn.close()
            return result

        now_ms = int(time.time() * 1000)
        # `fees` is whatever the ENTRY booked; there is no sell fee because no
        # sell ever happened. COALESCE because the column is NULL until a close.
        cur = conn.execute(
            "UPDATE positions SET "
            "  closed_ts = ?, "
            "  exit_px = 0.0, "
            "  pnl_gross = (0.0 - entry_px) * qty, "
            "  fees = COALESCE(fees, 0.0), "
            "  pnl_net = (0.0 - entry_px) * qty - COALESCE(fees, 0.0), "
            "  r_multiple = CASE "
            "    WHEN (entry_px - COALESCE(stop_px, 0.0)) * qty > 0 "
            "    THEN ((0.0 - entry_px) * qty - COALESCE(fees, 0.0)) "
            "         / ((entry_px - COALESCE(stop_px, 0.0)) * qty) "
            "    ELSE 0.0 END, "
            "  exit_reason = ? "
            "WHERE closed_ts IS NULL AND opened_ts < ?",
            (now_ms, ORPHAN_EXIT_REASON, boundary_ms))
        changed = cur.rowcount
        result['changed'] = changed

        # Convention 20: a count that does not match is a REFUSAL, not a
        # warning. If these disagree the transaction saw two different row
        # sets and no partial sweep is acceptable.
        if changed != count:
            conn.execute('ROLLBACK')
            conn.close()
            raise SystemExit(
                'REFUSING: census said %d rows, UPDATE changed %d. Rolled back.'
                % (count, changed))
        conn.execute('COMMIT')
        result['applied'] = True
        result['closed_ts'] = now_ms
    except Exception:
        try:
            conn.execute('ROLLBACK')
        except sqlite3.Error:
            pass
        conn.close()
        raise

    integrity = conn.execute('PRAGMA integrity_check').fetchone()[0]
    result['integrity_check'] = integrity
    remaining = conn.execute(
        'SELECT COUNT(*) FROM positions WHERE closed_ts IS NULL '
        'AND opened_ts < ?', (boundary_ms,)).fetchone()[0]
    result['remaining_pre_boundary_open'] = int(remaining)
    conn.close()
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', required=True)
    p.add_argument('--pid', type=int, default=None,
                   help='pid of the process that OWNS this db; the sweep '
                        'boundary is its start time')
    p.add_argument('--boundary-ms', type=int, default=None,
                   help='explicit boundary in epoch ms (only when the owning '
                        'process is already gone)')
    p.add_argument('--apply', action='store_true',
                   help='actually write. Without it this is a dry run.')
    p.add_argument('--no-snapshot', action='store_true',
                   help='skip the pre-sweep backup (tests only)')
    args = p.parse_args(argv)

    if not os.path.exists(args.db):
        print('no such database: %s' % args.db, file=sys.stderr)
        return 1
    if (args.pid is None) == (args.boundary_ms is None):
        print('give exactly one of --pid or --boundary-ms', file=sys.stderr)
        return 1

    boundary_ms = (args.boundary_ms if args.boundary_ms is not None
                   else process_start_ms(args.pid))

    if args.apply and not args.no_snapshot:
        path = snapshot(args.db)
        print('snapshot: %s' % path)

    res = sweep(args.db, boundary_ms, args.apply)
    print('db                : %s' % res['db'])
    print('boundary (epoch ms): %d  (%s UTC)'
          % (boundary_ms,
             time.strftime('%Y-%m-%dT%H:%M:%SZ',
                           time.gmtime(boundary_ms / 1000.0))))
    print('orphans           : %d' % res['orphans'])
    print('cost basis (USD)  : %.2f' % res['cost_basis'])
    print('left live         : %d' % res['left_live'])
    if res['applied']:
        print('rows swept        : %d' % res['changed'])
        print('integrity_check   : %s' % res['integrity_check'])
        print('still open pre-bnd: %d' % res['remaining_pre_boundary_open'])
    else:
        print('DRY RUN - nothing written. Re-run with --apply.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
