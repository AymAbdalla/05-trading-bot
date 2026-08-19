"""Verify `market_tape`'s complement key (Forge proposal 036,
pm_complement_pair_keying) and report the no-arbitrage measurement it exists
to unblock.

`strategies/polymarket/dip_arb.py` now stamps `condition_id` (Gamma's join
key) and `complement_id` (the other token on the same two-outcome market,
read directly off the `Market` object at write time - never inferred from
price) onto every `market_tape` row. This module is the check that the key
actually removes the ambiguity the old mid-sum heuristic could not: it finds
complement PAIRS by an exact key join only (`a.complement_id = b.market_id
AND b.complement_id = a.market_id AND a.ts = b.ts`), so a pair either exists
by construction or it does not - there is no candidate to choose among.

Two things this module measures, and one thing it enforces:

  1. The no-arbitrage distribution: `best_ask_a + best_ask_b` over every
     synchronous complement pair. This is the number the old 7.85% finding
     was NOT_TESTED for want of (convention 11) - a mid-sum matcher that
     over-matches 61.7% of the time cannot support it.
  2. The ambiguity fraction, which must be exactly 0.000 by construction: a
     nonzero value here would mean this module's own join is wrong, not that
     the market is ambiguous.
  3. The NULL-`condition_id` fraction. Historical rows (written before this
     column existed) are NULL and that is documented, acceptable and
     expected to dominate for a while after deploy. A NULL fraction above 5%
     AFTER `since_ts` is a wiring failure - the discovery pass stopped
     handing `observe()` a `condition_id` it used to have - and the
     prescribed fix is REVERT (see the handoff), never a fallback matcher:
     a partially-keyed tape would silently re-admit the exact heuristic this
     column exists to retire.

Read-only throughout (mode=ro): the shadow loop may be writing to
`db/trading.db` while this runs.
"""
import json
import os
import sqlite3
import sys
from typing import Any, Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from agents.forge_shadow_eval import (ShadowUnreadable, _connect_ro,  # noqa: E402
                                       _finite, _require_tables)

DEFAULT_DB = os.path.join(ROOT, 'db', 'trading.db')

#: Below this many synchronous pairs the no-arbitrage distribution is a small
#: sample, not a measurement - convention 11: NOT_TESTED, not "found nothing".
MIN_PAIRS_FOR_MEASUREMENT = 1000

#: Above this NULL-`condition_id` fraction (measured after `since_ts`), the
#: key is not doing its job and the fix is REVERT, not a fallback matcher.
NULL_FRACTION_FAIL_THRESHOLD = 0.05


def complement_pairs(conn: sqlite3.Connection,
                      since_ts: Optional[float] = None) -> List[Dict[str, Any]]:
    """Every synchronous complement pair in `market_tape`, keyed exactly.

    A pair is two rows sharing one `condition_id` and one `ts`, where each
    row's `complement_id` names the other row's `market_id` - both
    directions, so a stale or one-sided `complement_id` (a bug, not a real
    complement) can never produce a pair. `token_a < token_b` keeps each pair
    once rather than twice (the join is symmetric).
    """
    where = ['a.condition_id IS NOT NULL', 'a.market_id < b.market_id']
    params: List[Any] = []
    if since_ts is not None:
        where.append('a.ts >= ?')
        params.append(float(since_ts))
    sql = (
        'SELECT a.condition_id, a.ts, a.market_id AS token_a, '
        'b.market_id AS token_b, a.best_ask AS ask_a, b.best_ask AS ask_b, '
        'a.mid AS mid_a, b.mid AS mid_b '
        'FROM market_tape a JOIN market_tape b '
        'ON a.condition_id = b.condition_id AND a.ts = b.ts '
        'AND a.complement_id = b.market_id AND b.complement_id = a.market_id '
        'WHERE ' + ' AND '.join(where))
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _ambiguity_fraction(pairs: List[Dict[str, Any]]) -> Optional[float]:
    """Fraction of pairs that required choosing among more than one
    candidate partner. Always 0.0 when any pairs exist: `complement_pairs`
    joins on the stored key alone, so a pair with more than one candidate
    could not have been returned in the first place. Computed rather than
    hardcoded so a future change to the join that reintroduces ambiguity
    trips this number instead of silently reading 0.
    """
    if not pairs:
        return None
    seen = {}
    ambiguous = 0
    for p in pairs:
        key = (p['condition_id'], p['ts'], p['token_a'])
        if key in seen and seen[key] != p['token_b']:
            ambiguous += 1
        seen[key] = p['token_b']
    return ambiguous / len(pairs)


def sum_distribution(pairs: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The no-arbitrage measurement: distribution of `ask_a + ask_b`.

    Pairs missing either ask (one side's book was empty this tick) are
    excluded and counted separately - a missing quote is not a sum of zero.
    """
    sums = []
    missing_ask = 0
    for p in pairs:
        a, b = _finite(p.get('ask_a')), _finite(p.get('ask_b'))
        if a is None or b is None:
            missing_ask += 1
            continue
        sums.append(a + b)
    if not sums:
        return {
            'n': 0,
            'missing_ask': missing_ask,
            'mean': None, 'min': None, 'max': None,
            'below_one': None, 'at_one': None, 'above_one': None,
        }
    below = sum(1 for s in sums if s < 1.0)
    at = sum(1 for s in sums if s == 1.0)
    above = len(sums) - below - at
    return {
        'n': len(sums),
        'missing_ask': missing_ask,
        'mean': sum(sums) / len(sums),
        'min': min(sums),
        'max': max(sums),
        'below_one': below,
        'below_one_fraction': below / len(sums),
        'at_one': at,
        'above_one': above,
    }


def null_condition_fraction(conn: sqlite3.Connection,
                             since_ts: Optional[float] = None) -> Dict[str, Any]:
    where = 'WHERE ts >= ?' if since_ts is not None else ''
    params = [float(since_ts)] if since_ts is not None else []
    total = conn.execute(
        f'SELECT COUNT(*) FROM market_tape {where}', params).fetchone()[0]
    null_n = conn.execute(
        f'SELECT COUNT(*) FROM market_tape {where}'
        f"{' AND' if where else ' WHERE'} condition_id IS NULL",
        params).fetchone()[0]
    return {
        'total_rows': total,
        'null_condition_id_rows': null_n,
        'null_fraction': (null_n / total) if total else None,
    }


def evaluate(db_path: str = DEFAULT_DB, since_ts: Optional[float] = None,
             min_pairs: int = MIN_PAIRS_FOR_MEASUREMENT) -> Dict[str, Any]:
    """Full complement-key evaluation.

    `status`:
      'unreadable'            - the DB could not be read (convention 11: NOT
                                 an empty 'ok').
      'insufficient_data'     - fewer than `min_pairs` synchronous pairs
                                 exist yet. NOT a failure - convention 11
                                 again, the measurement has not been taken,
                                 nothing was tried and found absent.
      'failed_null_threshold' - the >5% NULL rule tripped. The prescribed fix
                                 is REVERT, decided by a human, never applied
                                 automatically by this module.
      'ok'                    - >= min_pairs pairs, NULL fraction in bounds.
    """
    try:
        conn = _connect_ro(db_path)
    except ShadowUnreadable as exc:
        return {
            'status': 'unreadable',
            'db_path': db_path,
            'error': str(exc),
            'note': 'NOT_TESTED, not empty. Convention 11.',
        }

    try:
        _require_tables(conn, ('market_tape',))
        pairs = complement_pairs(conn, since_ts=since_ts)
        nulls = null_condition_fraction(conn, since_ts=since_ts)
    except (ShadowUnreadable, sqlite3.Error) as exc:
        return {
            'status': 'unreadable',
            'db_path': db_path,
            'error': f'{type(exc).__name__}: {exc}',
            'note': 'NOT_TESTED, not empty. Convention 11.',
        }
    finally:
        conn.close()

    ambiguity_fraction = _ambiguity_fraction(pairs)
    result = {
        'db_path': os.path.relpath(db_path, ROOT)
                   if db_path.startswith(ROOT) else db_path,
        'since_ts': since_ts,
        'min_pairs': min_pairs,
        'pairs_found': len(pairs),
        'ambiguity_fraction': ambiguity_fraction,
        'no_arbitrage_distribution': sum_distribution(pairs),
        'null_condition_id': nulls,
    }

    null_fraction = nulls['null_fraction']
    if null_fraction is not None and null_fraction > NULL_FRACTION_FAIL_THRESHOLD:
        result['status'] = 'failed_null_threshold'
        result['note'] = (
            f'{null_fraction:.1%} of tape rows carry NULL condition_id, '
            f'above the {NULL_FRACTION_FAIL_THRESHOLD:.0%} threshold. '
            'Prescribed fix is REVERT (docs/handoffs/from-raven/'
            '2026-08-19-proposal-036.md task 2), never a fallback matcher.')
    elif len(pairs) < min_pairs:
        result['status'] = 'insufficient_data'
        result['note'] = (
            f'{len(pairs)} synchronous pairs, below the {min_pairs} floor. '
            'NOT_TESTED (convention 11), not a negative result.')
    else:
        result['status'] = 'ok'
        result['note'] = None
    return result


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=DEFAULT_DB)
    parser.add_argument('--since-ts', type=float, default=None,
                        help='DipArb absolute-second clock cutoff; excludes '
                             'rows written before this (e.g. before a '
                             'restart that first wrote condition_id)')
    parser.add_argument('--min-pairs', type=int,
                        default=MIN_PAIRS_FOR_MEASUREMENT)
    args = parser.parse_args(argv)
    result = evaluate(args.db, since_ts=args.since_ts, min_pairs=args.min_pairs)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result.get('status') in ('ok', 'insufficient_data') else 1


if __name__ == '__main__':
    sys.exit(main())
