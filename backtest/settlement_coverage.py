"""Coverage and agreement for the settlement resolution ledger (proposal 038).

This is the ACCEPTANCE TEST for the repair, and it is deliberately a
`backtest/` tool rather than anything the loop imports. Rule 6: the only
consumers of `market_resolutions` are `backtest/` and
`agents/forge_shadow_eval.py`. A resolution record exists at window CLOSE,
after every entry and exit decision for that window, so a strategy reading it
is look-ahead or a bug.

## The two numbers, and why they are separate

**Coverage** (038's kill condition, rule 2). Over 200 or more closed positions
booked AFTER the ledger went live, resolution must be available from the ledger
for 99% or more of the distinct `(pair, outcome_side)` market-sides those
positions touch. Reported as a fraction with BOTH numerator and denominator
(convention 20). Baseline before the repair: 325/864 = 37.6% at 2026-08-19
11:30 UTC. Below 95% is FAILED and reverted, not tuned. Backfilled rows do NOT
count - `LIVE_SOURCES` is the default filter here for exactly that reason.

**Agreement** (038's kill condition, second clause). Wherever the ledger and
the old sibling inference BOTH have an answer, they must agree. The required
disagreement rate is 0.00, and a single disagreement means one of the two is
wrong and the ledger cannot be trusted until it is known which.

That check is only worth anything because the two are INDEPENDENT reads. The
ledger writes `source='venue'` from the CLOB's `tokens[].winner` field; the
sibling inference is recovered from `positions.exit_px`, which the paper
adapter set from Gamma's `outcomePrices` via `prices.resolution_price`.
Different endpoint, different field, different failure modes.

## The sibling inference, and its bias

Resolution is recoverable today only from a position on the same market-side
held to settlement - `exit_reason` is the bare 'stop' (0.00) or 'target' (1.00)
rather than a distinct settlement reason, and everything sold early is prefixed
'sell:'. `outcome_side` is not a column; it lives in
`signals.features_json`, reached through `positions.signal_id`.

The method is SOUND - of the pairs where both sides were independently
recovered, every one shows exactly one side at 1.00, which is the arithmetic a
binary must satisfy - and BIASED, one way. A winning side is sold early by
`profit_target` and leaves no settlement row; a losing side rots to 0.00 and
records one. Measured 2026-08-19: 29.9% of singly-recovered sides settled 1.00
against a ~50% unbiased benchmark. Never use this map as a sample. It is here
to be CHECKED AGAINST, and to be backfilled from under its own marked source.

## Contradictions

A market-side carrying BOTH 0.00 and 1.00 is arithmetically impossible for one
side of one binary. Two exist, and they are 038's named first test cases:

    sol-updown-5m-1787056800 / Up
    btc-updown-5m-1787134200 / Down

They are reported as `contradictory`, never resolved to a value, and never
backfilled. Picking one would be a coin flip wearing a measurement's clothes.
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.resolution_ledger import (  # noqa: E402
    LIVE_SOURCES, SOURCE_SIBLING_INFERENCE_BACKFILL, ensure_schema,
    resolution_row_for, table_exists, write_resolutions)

#: `exit_reason` values that mean SETTLEMENT rather than a sale. Everything the
#: loop sells is prefixed 'sell:'; settlement alone is bare. Keying on the
#: reason AND on the price, because either one alone has a false positive: a
#: sale can land exactly at 0.00, and a bare reason could in principle be
#: written by a future path that is not settlement.
SETTLEMENT_REASONS = ('stop', 'target')

#: The only two prices a binary can settle at.
SETTLEMENT_PRICES = (0.0, 1.0)


def open_ro(db_path):
    """Read-only connection. The live loop holds this file open in WAL."""
    return sqlite3.connect('file:{0}?mode=ro'.format(db_path), uri=True)


def sibling_inference_map(conn, since_ms=None):
    """`(pair, outcome_side)` -> 1.0, 0.0, or None where CONTRADICTORY.

    Returns `(resolved, report)`. `resolved` holds only the market-sides with
    exactly one settlement price observed; a side seen at both 0.00 and 1.00 is
    left OUT of it and listed in `report['contradictory']` instead, because
    picking one of two impossible answers is a coin flip that looks like a
    measurement.

    `report['touched']` is every distinct market-side any closed position
    touched, recoverable or not - it is the DENOMINATOR of the coverage
    fraction, and it is counted here rather than re-derived so the numerator
    and denominator can never come from two different queries.

    `since_ms` filters on `positions.opened_ts`, which is in MILLISECONDS. The
    ledger's own `window_ts` and `resolved_ts` are in SECONDS. Mixing them is a
    factor-of-1000 error that produces an empty result and looks like a market
    that was never traded.
    """
    sql = ('SELECT p.pair, p.exit_px, p.exit_reason, s.features_json '
           'FROM positions p LEFT JOIN signals s ON s.id = p.signal_id '
           'WHERE p.closed_ts IS NOT NULL')
    params = []
    if since_ms is not None:
        sql += ' AND p.opened_ts >= ?'
        params.append(int(since_ms))

    observed = {}
    report = {'closed_positions': 0, 'no_outcome_side': 0,
              'touched': set(), 'contradictory': []}
    for pair, exit_px, reason, features_json in conn.execute(sql, params):
        report['closed_positions'] += 1
        side = None
        if features_json:
            try:
                side = json.loads(features_json).get('outcome_side')
            except (ValueError, TypeError, AttributeError):
                side = None
        if not side or not pair:
            # No signal row, no features_json, or no outcome_side on it. The
            # market-side key itself is unavailable, so this position cannot
            # even be COUNTED in the denominator, let alone resolved. Named
            # and counted rather than dropped (convention 20).
            report['no_outcome_side'] += 1
            continue
        key = (str(pair), str(side))
        report['touched'].add(key)
        if reason in SETTLEMENT_REASONS and exit_px in SETTLEMENT_PRICES:
            observed.setdefault(key, set()).add(float(exit_px))

    resolved = {}
    for key, prices in observed.items():
        if len(prices) == 1:
            resolved[key] = next(iter(prices))
        else:
            report['contradictory'].append(
                {'market_slug': key[0], 'outcome_side': key[1],
                 'prices': sorted(prices)})
    report['recoverable'] = len(resolved)
    report['touched_count'] = len(report['touched'])
    report['settled_one'] = sum(1 for v in resolved.values() if v == 1.0)
    return resolved, report


def coverage(conn, since_ms=None, sources=LIVE_SOURCES):
    """038's kill condition, as a fraction with numerator AND denominator.

    The denominator is every distinct `(pair, outcome_side)` a closed position
    touched in the window; the numerator is how many of those the LEDGER can
    answer. `sources` defaults to `LIVE_SOURCES`, which excludes
    `sibling_inference_backfill` - rule 4 measures coverage on markets FETCHED
    AFTER the ledger landed, and letting recovered history count toward it
    would let the repair pass on data that predates it.

    Reports `verdict` against 038's own thresholds and NOT_TESTED below the
    200-closed-position floor, because convention 11 says NOT_TESTED means
    "could not run", never "ran and found nothing" - and 038 says explicitly:
    do not grade a partial sample.
    """
    _resolved, report = sibling_inference_map(conn, since_ms)
    touched = report['touched']
    if not table_exists(conn):
        # The ledger has not landed in THIS database yet. That is NOT_TESTED,
        # and saying so is the whole point: reporting it as 0/889 would read
        # like a recorder that ran and found nothing.
        return {
            'closed_positions': report['closed_positions'],
            'no_outcome_side': report['no_outcome_side'],
            'numerator': None, 'denominator': len(touched),
            'fraction': None, 'sources': list(sources),
            'verdict': 'NOT_TESTED',
            'reason': 'market_resolutions table absent from this database; '
                      'the ledger activates at the next loop restart',
            'ledger_table_present': False,
            'missing_sample': [], 'missing_total': None,
            'baseline_note': '325/864 = 37.6% at 2026-08-19 11:30 UTC, '
                             'pre-repair',
        }
    covered, missing = 0, []
    for slug, side in sorted(touched):
        if resolution_row_for(conn, slug, side, sources) is not None:
            covered += 1
        else:
            missing.append({'market_slug': slug, 'outcome_side': side})
    denominator = len(touched)
    fraction = (float(covered) / denominator) if denominator else None
    if report['closed_positions'] < 200:
        verdict = 'NOT_TESTED'
    elif fraction is None:
        verdict = 'NOT_TESTED'
    elif fraction >= 0.99:
        verdict = 'PASS'
    elif fraction >= 0.95:
        verdict = 'INCOMPLETE'
    else:
        verdict = 'FAILED'
    return {
        'closed_positions': report['closed_positions'],
        'no_outcome_side': report['no_outcome_side'],
        'numerator': covered,
        'denominator': denominator,
        'fraction': fraction,
        'sources': list(sources) if sources is not None else None,
        'ledger_table_present': True,
        'verdict': verdict,
        'missing_sample': missing[:20],
        'missing_total': len(missing),
        'baseline_note': '325/864 = 37.6% at 2026-08-19 11:30 UTC, pre-repair',
    }


def disagreements(conn, sources=LIVE_SOURCES):
    """Where the ledger and the sibling inference BOTH answer, do they agree?

    Returns a dict carrying the overlap size, the disagreement list and the
    rate. 038 requires the rate to be exactly 0.00; anything above it means one
    of the two reads is wrong and the ledger cannot be trusted until it is
    known which. The check has teeth only because the two are independent
    reads - see the module docstring.

    Contradictory market-sides are reported separately and are NOT counted as
    disagreements: they are a fault in the inference, not evidence about the
    ledger, and pooling them would let a known-bad input manufacture a failure.
    """
    inferred, report = sibling_inference_map(conn)
    if not table_exists(conn):
        return {'overlap': 0, 'disagreements': [], 'disagreement_count': 0,
                'rate': None, 'required_rate': 0.0, 'verdict': 'NOT_TESTED',
                'reason': 'market_resolutions table absent from this database',
                'contradictory_inference': report['contradictory']}
    overlap, bad = 0, []
    for (slug, side), inferred_px in sorted(inferred.items()):
        row = resolution_row_for(conn, slug, side, sources)
        if row is None or row['resolved_px'] is None:
            continue
        overlap += 1
        if float(row['resolved_px']) != float(inferred_px):
            bad.append({'market_slug': slug, 'outcome_side': side,
                        'ledger_px': row['resolved_px'],
                        'ledger_source': row['source'],
                        'inferred_px': inferred_px})
    return {
        'overlap': overlap,
        'disagreements': bad,
        'disagreement_count': len(bad),
        'rate': (float(len(bad)) / overlap) if overlap else None,
        'required_rate': 0.0,
        'verdict': ('NOT_TESTED' if not overlap
                    else ('PASS' if not bad else 'FAILED')),
        'contradictory_inference': report['contradictory'],
    }


def backfill(conn, dry_run=True):
    """Write the recoverable history under `sibling_inference_backfill`.

    PERMITTED by rule 4 and MARKED, so the recovered market-sides are not lost
    and can still never be mistaken for observations. These rows are excluded
    from `coverage()` by default and must never count toward the kill
    condition's number.

    `INSERT OR IGNORE` inside `write_resolutions` means this can never
    overwrite a live `venue` row. Contradictory market-sides are skipped and
    counted: a side seen at both 0.00 and 1.00 has no value to write.

    Default is `dry_run=True`. This writes into a database the shadow loop
    holds open, so making the destructive direction the one you have to ask for
    is the point.
    """
    inferred, report = sibling_inference_map(conn)
    rows = [(slug, side, px, None)
            for (slug, side), px in sorted(inferred.items())]
    result = {
        'candidates': len(rows),
        'contradictory_skipped': len(report['contradictory']),
        'source': SOURCE_SIBLING_INFERENCE_BACKFILL,
        'dry_run': bool(dry_run),
        'written': 0,
    }
    if dry_run:
        return result
    ensure_schema(conn)
    result['written'] = write_resolutions(
        conn, rows, SOURCE_SIBLING_INFERENCE_BACKFILL)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Coverage and agreement for the settlement resolution '
                    'ledger (Forge proposal 038).')
    parser.add_argument('--db', default='db/trading.db')
    parser.add_argument(
        '--since', type=float, default=None,
        help='Unix SECONDS. Only closed positions opened at or after this '
             'count. Pass the ledger changeover time T to measure the kill '
             'condition on markets fetched after the ledger landed.')
    parser.add_argument(
        '--backfill', action='store_true',
        help='WRITE the recoverable history under source '
             'sibling_inference_backfill. Requires a writable --db.')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    if args.backfill:
        conn = sqlite3.connect(args.db, timeout=15.0)
    else:
        conn = open_ro(args.db)

    since_ms = None if args.since is None else int(args.since * 1000)
    out = {
        'db': args.db,
        'since_sec': args.since,
        'coverage': coverage(conn, since_ms),
        'agreement': disagreements(conn),
    }
    if args.backfill:
        out['backfill'] = backfill(conn, dry_run=False)

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    cov = out['coverage']
    if cov.get('reason'):
        print('NOTE      {0}'.format(cov['reason']))
    print('COVERAGE  {0}/{1} = {2}  [{3}]'.format(
        'n/a' if cov['numerator'] is None else cov['numerator'],
        cov['denominator'],
        'n/a' if cov['fraction'] is None else '%.1f%%' % (100 * cov['fraction']),
        cov['verdict']))
    print('          closed positions {0}, no outcome_side {1}, sources {2}'
          .format(cov['closed_positions'], cov['no_outcome_side'],
                  cov['sources']))
    agr = out['agreement']
    print('AGREEMENT overlap {0}, disagreements {1}, rate {2}  [{3}]'.format(
        agr['overlap'], agr['disagreement_count'],
        'n/a' if agr['rate'] is None else '%.4f' % agr['rate'],
        agr['verdict']))
    for row in agr['disagreements']:
        print('   DISAGREE {market_slug} {outcome_side} ledger={ledger_px} '
              '({ledger_source}) inferred={inferred_px}'.format(**row))
    for row in agr['contradictory_inference']:
        print('   CONTRADICTORY INFERENCE {market_slug} {outcome_side} '
              '{prices}'.format(**row))
    if 'backfill' in out:
        print('BACKFILL  wrote {written} of {candidates} candidates '
              '(skipped {contradictory_skipped} contradictory) as '
              '{source}'.format(**out['backfill']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
