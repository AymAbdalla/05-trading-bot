"""Purge contract-instrument rows from the graveyard so they get re-run
under the D-249 sizing fix.

WHY THIS EXISTS
---------------
D-249 fixed contract sizing at the shared cost-model layer: before the fix,
`CostModel` floored `contracts` to a minimum of 1, so a $100 account could
"trade" one MES contract that needs ~$1,800 of initial margin. After the fix,
`InstrumentSpec.size_for` is actually reached and returns 0 when the account
cannot afford a single contract.

The fix landed in `backtest/cost_model.py` WITHOUT a `COST_MODEL_VERSION`
bump - the tag stayed '2026-08-13' on both sides of it. So pre-fix and
post-fix rows are indistinguishable by their own metadata, and the project's
"never pool across cost_model_version" convention cannot separate them.

The remedy is deliberately blunt and therefore safe: drop EVERY contract
row and let the incremental runner rebuild them under current code. We do
not try to tell pre-fix rows from post-fix ones, because nothing in the data
supports that distinction. Purging all of them is correct whichever side of
the fix a given row came from.

SCOPE
-----
Only FUTURES and OPTIONS are affected. `InstrumentSpec.is_contract` is true
only for those two; EQUITY / ETF / CRYPTO never enter the contract-sizing
path, so their rows are untouched by D-249 and are left alone. Purging them
would throw away good work for no reason.

USAGE
-----
    python3 backtest/purge_stale_futures.py                 # dry run, writes nothing
    python3 backtest/purge_stale_futures.py --apply         # back up, then purge

After a successful --apply, re-run the incremental builder to rebuild the
purged rows under the fixed cost model:

    python3 backtest/run_incremental_graveyard.py

SAFETY
------
Refuses to touch the file while `run_incremental_graveyard.py` is alive.
That runner holds the whole graveyard in memory and rewrites it after every
ticker, so a purge landing mid-sweep would be silently clobbered on the very
next save. `--force` overrides, but there is no good reason to use it.
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

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GRAVEYARD_FILE = os.path.join(ROOT, 'research', 'graveyard', 'v0_graveyard_full.json')
ARCHIVE_DIR = os.path.join(ROOT, 'research', 'graveyard', 'archive')

# The only asset classes that route through InstrumentSpec contract sizing.
CONTRACT_CLASSES = {'FUTURES', 'OPTIONS'}

SWEEP_PROC = 'backtest/run_incremental_graveyard.py'


def sweep_is_running() -> bool:
    """True if the incremental graveyard runner is alive.

    Matches how the queued chain detects it (`pgrep -f`), so both agree.
    """
    try:
        out = subprocess.run(['pgrep', '-f', SWEEP_PROC],
                             capture_output=True, text=True)
        return out.returncode == 0 and bool(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        # Cannot tell -> assume it IS running. Refusing a safe purge is
        # cheap; clobbering a 6-hour sweep is not.
        return True


def load_graveyard(path: str, attempts: int = 6, delay: float = 8.0) -> dict:
    """Read the graveyard, tolerating a partial file.

    The runner rewrites this file wholesale after each ticker, so a read can
    land mid-`json.dump` and see truncated JSON. Retry rather than crash.
    """
    last = None
    for i in range(attempts):
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError) as e:
            last = e
            print(f'  partial read (attempt {i + 1}/{attempts}): {e}', file=sys.stderr)
            time.sleep(delay)
    raise SystemExit(f'could not get a clean read of {path}: {last}')


def summarize(entries: list, label: str) -> None:
    by_class = collections.Counter(e.get('asset_class') for e in entries)
    print(f'\n{label}: {len(entries)} entries')
    for k, v in by_class.most_common():
        print(f'    {k}: {v}')


def rebuild_summary(existing: dict, entries: list) -> dict:
    """Recompute the graveyard's header counters for the surviving entries.

    Mirrors the fields `run_incremental_graveyard.py` writes so the purged
    file stays shape-compatible with every reader (judge.py, pooled_analysis,
    summarize_graveyard).
    """
    out = dict(existing)
    out['generated'] = time.strftime('%Y-%m-%d %H:%M:%S')
    out['total_tests'] = len(entries)
    out['passed'] = sum(1 for e in entries if e.get('verdict') == 'PASS')
    out['failed'] = sum(1 for e in entries if e.get('verdict') != 'PASS')
    out['inversions_flagged'] = sum(1 for e in entries if e.get('inversion_flagged'))
    out['entries'] = entries
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--graveyard', default=GRAVEYARD_FILE)
    ap.add_argument('--apply', action='store_true',
                    help='actually write the purged file (default is a dry run)')
    ap.add_argument('--force', action='store_true',
                    help='purge even while the graveyard sweep is running (do not)')
    args = ap.parse_args()

    if not os.path.exists(args.graveyard):
        raise SystemExit(f'no graveyard at {args.graveyard}')

    running = sweep_is_running()
    if running:
        msg = (f'{SWEEP_PROC} is RUNNING. It rewrites the graveyard after every '
               f'ticker, so a purge now would be clobbered on its next save.')
        if not args.force:
            print(f'REFUSING: {msg}')
            print('Wait for the sweep to exit, then re-run. (--force overrides.)')
            raise SystemExit(2)
        print(f'WARNING: {msg}\nProceeding anyway because --force was passed.')

    print(f'reading {args.graveyard} ...')
    data = load_graveyard(args.graveyard)
    entries = data.get('entries', [])
    summarize(entries, 'BEFORE')

    purge = [e for e in entries if e.get('asset_class') in CONTRACT_CLASSES]
    keep = [e for e in entries if e.get('asset_class') not in CONTRACT_CLASSES]

    print(f'\nto purge (contract instruments, D-249 sizing): {len(purge)}')
    if purge:
        by_ticker = collections.Counter(e.get('ticker') for e in purge)
        for k, v in by_ticker.most_common():
            print(f'    {k}: {v}')
        by_verdict = collections.Counter(e.get('verdict') for e in purge)
        print(f'  verdicts being discarded: {dict(by_verdict)}')
        passes = [e for e in purge if e.get('verdict') in ('PASS', 'PASS_BENCHMARK')]
        if passes:
            # Loud on purpose: a discarded PASS is the one case where someone
            # might reasonably want to look before letting it go.
            print(f'  NOTE: {len(passes)} of the purged rows are PASS/PASS_BENCHMARK.')
            for e in passes[:10]:
                print(f'    {e.get("strategy")} + {e.get("exit_config")} on '
                      f'{e.get("ticker")} {e.get("timeframe")}')

    summarize(keep, 'AFTER')

    if not args.apply:
        print('\nDRY RUN - nothing written. Re-run with --apply to purge.')
        return

    if not purge:
        print('\nnothing to purge; leaving the file untouched.')
        return

    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    stamp = time.strftime('%Y%m%dT%H%M%S')
    backup = os.path.join(ARCHIVE_DIR, f'v0_graveyard_full.pre-D249-purge.{stamp}.json')
    shutil.copy2(args.graveyard, backup)
    print(f'\nbacked up to {backup}')

    out = rebuild_summary(data, keep)
    tmp = args.graveyard + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(out, f, indent=2, default=str)
    os.replace(tmp, args.graveyard)  # atomic: no reader ever sees a half file

    print(f'purged {len(purge)} contract rows; {len(keep)} remain.')
    print('\nNEXT: rebuild the purged rows under the fixed cost model:')
    print('    python3 backtest/run_incremental_graveyard.py')


if __name__ == '__main__':
    main()
