"""Relabel graveyard rows that could never have traded: FAIL -> NOT_TESTED.

WHY THIS EXISTS (Raven ruling R-002, 2026-08-17)
------------------------------------------------
D-249 made contract sizing honest: `InstrumentSpec.size_for` returns 0 when
the account cannot afford a single contract. At `notional_cap_usd: 100`
against initial margins of $900-$2,600, that is EVERY futures instrument, at
every price, on every bar. No futures signal can ever become a position.

The sizing is correct. The LABEL was not. `vectorized_harness.py` defines
NOT_TESTED as "the harness structurally could not have run this" but gated it
only on bar count, so unaffordable instruments fell through to FAIL. A
strategy whose every signal was rejected for lack of capital did not run and
fail; it did not run (convention 11). Reporting it as FAIL claims we tested
an idea and it lost money, which is a claim about the idea we never earned.

`vectorized_harness.py` now gates on affordability too, so rows built from
here on are labeled correctly at the source. This script fixes the 535,425
rows already on disk, which would otherwise need a multi-hour full re-sweep
to correct a label that is a pure function of (instrument, notional cap).

WHAT IT WILL AND WILL NOT TOUCH
-------------------------------
Relabels a row only when ALL of these hold:
  - verdict is FAIL
  - it placed zero trades
  - its instrument provably cannot be sized at the cap, PRICE-INDEPENDENTLY

That last condition is the careful one. Futures size off `initial_margin`,
which does not depend on price, so "unaffordable" is decidable from the row
alone. Options size off `price * multiplier`, which is NOT decidable without
the series price - so option rows are SKIPPED and counted, never guessed at.
A skipped row is a number this script owes you, not a silent drop
(convention 20).

PASS rows are never touched: a PASS requires trades, and a row with trades
was sizable by definition. The 155 distinct findings cannot move.

USAGE
-----
    python3 backtest/relabel_unsizable.py              # dry run, writes nothing
    python3 backtest/relabel_unsizable.py --apply      # back up, then relabel
"""
import argparse
import collections
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.instruments import spec_for

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAVEYARD_FILE = os.path.join(ROOT, 'research', 'graveyard', 'v0_graveyard_full.json')
ARCHIVE_DIR = os.path.join(ROOT, 'research', 'graveyard', 'archive')

SWEEP_PROC = 'backtest/run_incremental_graveyard.py'
DEFAULT_CAP = 100.0     # config.yaml risk.notional_cap_usd


def sweep_is_running() -> bool:
    """True if the incremental graveyard runner is alive. It holds the whole
    graveyard in memory and rewrites it after every ticker, so a relabel
    landing mid-sweep would be clobbered on its next save."""
    try:
        out = subprocess.run(['pgrep', '-f', SWEEP_PROC],
                             capture_output=True, text=True)
        return out.returncode == 0 and bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        return True     # cannot tell -> assume it is. Refusing is cheap.


def unsizable_verdict(entry: dict, cap: float):
    """Can this row's instrument be sized at `cap`?

    Returns (decision, detail) where decision is True (provably unsizable),
    False (sizable), or None (undecidable from the row alone - the caller
    must count it, not assume either way).
    """
    asset_class = entry.get('asset_class')
    ticker = entry.get('ticker')
    if not asset_class or not ticker:
        return None, 'row has no asset_class/ticker'
    try:
        spec = spec_for(ticker, asset_class)
    except Exception as exc:                       # noqa: BLE001 - report, never guess
        return None, f'spec lookup failed: {exc}'
    if not spec.integer_only:
        return False, 'fractional sizing: always affordable'
    per_unit = spec.initial_margin
    if not per_unit:
        # Options and any contract without a margin figure size off price,
        # which this row does not carry. Undecidable, so we decide nothing.
        return None, f'{spec.symbol} sizes off price; row carries no price'
    if per_unit > cap:
        return True, f'one {spec.symbol} needs ${per_unit:,.0f}, cap is ${cap:,.0f}'
    return False, f'one {spec.symbol} needs ${per_unit:,.0f}, cap is ${cap:,.0f}'


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--graveyard', default=GRAVEYARD_FILE)
    ap.add_argument('--cap', type=float, default=DEFAULT_CAP,
                    help='notional_cap_usd the rows were built under')
    ap.add_argument('--apply', action='store_true',
                    help='actually write the relabeled file (default is a dry run)')
    ap.add_argument('--force', action='store_true',
                    help='relabel even while the sweep is running (do not)')
    args = ap.parse_args()

    if not os.path.exists(args.graveyard):
        raise SystemExit(f'no graveyard at {args.graveyard}')
    if sweep_is_running() and not args.force:
        print(f'REFUSING: {SWEEP_PROC} is RUNNING and would clobber this on its '
              f'next save. Wait for it to exit. (--force overrides.)')
        return 2

    print(f'reading {args.graveyard} ...')
    with open(args.graveyard) as f:
        data = json.load(f)
    entries = data.get('entries', [])
    print(f'{len(entries):,} rows, cap ${args.cap:,.0f}')

    relabeled = 0
    skipped_undecidable = collections.Counter()
    by_instrument = collections.Counter()
    detail_by_instrument = {}
    # Rows we deliberately leave alone, kept apart so the accounting closes.
    untouched_not_fail = untouched_has_trades = untouched_sizable = 0

    for e in entries:
        if e.get('verdict') != 'FAIL':
            untouched_not_fail += 1
            continue
        if e.get('trades'):
            # Placed trades, therefore sizable. Its FAIL is a real verdict.
            untouched_has_trades += 1
            continue
        decision, detail = unsizable_verdict(e, args.cap)
        if decision is None:
            skipped_undecidable[detail] += 1
            continue
        if not decision:
            untouched_sizable += 1
            continue
        e['verdict'] = 'NOT_TESTED'
        e['not_tested_reason'] = 'unsizable_at_cap'
        e['not_tested_detail'] = detail
        e['relabeled_by'] = 'relabel_unsizable.py (R-002)'
        relabeled += 1
        inst = e.get('instrument') or spec_for(e['ticker'], e['asset_class']).symbol
        by_instrument[inst] += 1
        detail_by_instrument[inst] = detail

    # ACCOUNTING IDENTITY (convention 20): every row is in exactly one bucket.
    accounted = (relabeled + sum(skipped_undecidable.values()) + untouched_not_fail
                 + untouched_has_trades + untouched_sizable)
    assert accounted == len(entries), (
        f'accounting does not close: {accounted} != {len(entries)}')

    print(f'\nTO RELABEL (FAIL -> NOT_TESTED, unsizable_at_cap): {relabeled:,}')
    for inst, n in by_instrument.most_common():
        print(f'    {inst:<6s} {n:>7,}   {detail_by_instrument[inst]}')
    print(f'\nLEFT ALONE')
    print(f'    not a FAIL (PASS/NOT_TESTED/etc):  {untouched_not_fail:>7,}')
    print(f'    FAIL but placed trades (real):     {untouched_has_trades:>7,}')
    print(f'    FAIL, no trades, but SIZABLE:      {untouched_sizable:>7,}')
    if skipped_undecidable:
        print(f'\nSKIPPED - undecidable, counted not guessed:')
        for reason, n in skipped_undecidable.most_common():
            print(f'    {n:>7,}  {reason}')
    print(f'\naccounting closes: {accounted:,} == {len(entries):,} rows')

    if not args.apply:
        print('\nDRY RUN - nothing written. Re-run with --apply.')
        return 0
    if not relabeled:
        print('\nnothing to relabel; leaving the file untouched.')
        return 0

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S')
    backup = os.path.join(ARCHIVE_DIR, f'v0_graveyard_full.pre-R002-relabel.{stamp}.json')
    shutil.copy2(args.graveyard, backup)
    print(f'\nbacked up to {backup}')

    # Header counters mirror what run_incremental_graveyard.py writes, so the
    # file stays shape-compatible with every reader.
    data['generated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    data['total_tests'] = len(entries)
    data['passed'] = sum(1 for e in entries if e.get('verdict') == 'PASS')
    data['failed'] = sum(1 for e in entries if e.get('verdict') == 'FAIL')
    data['not_tested'] = sum(1 for e in entries if e.get('verdict') == 'NOT_TESTED')
    data['unsizable_at_cap'] = relabeled
    data['entries'] = entries

    tmp = args.graveyard + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(data, f, indent=2, allow_nan=False, default=str)
    os.replace(tmp, args.graveyard)     # atomic: no reader sees a half file
    print(f'relabeled {relabeled:,} rows.')
    print('\nNEXT: regenerate the graveyard consumers (pooled_analysis,')
    print('asset_class_analysis, conditional_edge, run_inversions, summarize).')
    return 0


if __name__ == '__main__':
    sys.exit(main())
