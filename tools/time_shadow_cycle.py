"""Measure what one Polymarket shadow cycle actually costs, in wall time.

Why this exists
---------------
"The loop polls every 5 seconds" says nothing about whether it CAN. The poll
interval is a sleep; the thing that bounds it is the work between sleeps, and
until this script existed that number had never been measured - it had only
been inferred from the gap between two stats lines in a log, which folds the
sleep, the DB writes and the strategy evaluations into one figure.

This times `PolymarketShadowLoop.build_context` - the network-bound phase, and
the only phase that scales with the number of assets - directly, against the
LIVE public APIs, and reports the distribution rather than a single sample. One
sample of a network call is a coin flip.

    env -u PYTHONPATH python3 tools/time_shadow_cycle.py --samples 5
    env -u PYTHONPATH python3 tools/time_shadow_cycle.py --samples 5 --no-parallel

Read-only. It calls the same GET-only client the loop uses, writes into a
THROWAWAY sqlite file under a temp directory, and never touches `db/trading.db`
or the paper adapter's real CSV. It cannot open a position: it never calls
`evaluate_strategy` or the adapter.

## How to read the output, and the two ways it lies

1. **Convention 17.** Parallelising only REMOVES waiting, so the number will
   improve. That is the exact shape of a false positive. The control is
   `--no-parallel`, which runs the same code path with the executor disabled:
   if the round-trip COUNT reported by `client.stats['requests']` differs
   between the two modes, the speedup is not a speedup, it is fewer reads.
2. **A live measurement is a measurement of a moment.** Binance.US and Gamma
   latency at 06:00 UTC on a quiet Tuesday is not their latency during a move.
   Take the median, quote the spread, and re-run before citing.
"""
import argparse
import os
import statistics
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.assets import SHADOW_ASSETS          # noqa: E402
from engine.polymarket.client import PolymarketClient       # noqa: E402
from engine.polymarket.markets import current_window_ts     # noqa: E402
from engine.polymarket.shadow_loop import (PolymarketShadowLoop,  # noqa: E402
                                           ShadowStore)


def _percentile(values, q):
    """Nearest-rank percentile. No numpy dependency for a five-sample list."""
    if not values:
        return None
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q * (len(ordered) - 1)))))
    return ordered[idx]


def _report(label, samples):
    if not samples:
        print('  {:<28} no samples'.format(label))
        return
    print('  {:<28} n={:<3} median={:.3f}s  mean={:.3f}s  min={:.3f}s  '
          'max={:.3f}s'.format(label, len(samples), statistics.median(samples),
                               statistics.mean(samples), min(samples),
                               max(samples)))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--samples', type=int, default=5,
                   help='context builds per asset (default: %(default)s)')
    p.add_argument('--assets', default=','.join(SHADOW_ASSETS))
    p.add_argument('--no-parallel', action='store_true',
                   help='THE CONTROL. Disables the fetch executor so the same '
                        'code path runs sequentially.')
    p.add_argument('--no-15m', action='store_true')
    p.add_argument('--spot-ttl', type=float, default=None,
                   help='override the spot cache TTL. Pass 0 to DISABLE the '
                        'cache. Do this on BOTH sides when comparing '
                        'parallel against sequential: the spot read goes '
                        'direct to Binance.US and is NOT counted in '
                        'client.stats, so a cache hit is a round trip that '
                        'the request counter cannot see.')
    p.add_argument('--full-cycle', action='store_true',
                   help='time `run_cycle` instead of `build_context` alone. '
                        'This ALSO runs manage_exits, every strategy '
                        'evaluation and the sqlite writes, so it is the number '
                        'the poll interval actually has to cover. It writes '
                        'signals rows into the THROWAWAY db and can open paper '
                        'positions in a throwaway adapter; it cannot reach '
                        'db/trading.db or the real decision CSV.')
    p.add_argument('--gap-sec', type=float, default=1.0,
                   help='pause between samples so a keep-alive connection is '
                        'not the only thing being measured')
    args = p.parse_args(argv)

    assets = tuple(a.strip() for a in args.assets.split(',') if a.strip())
    tmpdir = tempfile.mkdtemp(prefix='pm-timing-')
    client = PolymarketClient()

    loop = PolymarketShadowLoop(
        client=client,
        store=ShadowStore(os.path.join(tmpdir, 'timing.db')),
        log_dir=tmpdir,
        assets=assets,
        include_15m=not args.no_15m,
        # No candle source: the 5m candle pull is refreshed at most once a
        # minute and would land in one arbitrary sample out of five, turning a
        # median into a description of which sample got unlucky.
        candle_source=None,
        parallel_fetches=not args.no_parallel,
        **({} if args.spot_ttl is None
           else {'spot_cache_ttl_sec': args.spot_ttl}),
    )

    print('=' * 72)
    print('POLYMARKET CONTEXT TIMING - read-only, throwaway db')
    print('  assets          : {}'.format(', '.join(assets)))
    print('  include_15m     : {}'.format(loop.include_15m))
    print('  parallel        : {} (width {})'.format(
        loop.parallel_fetches, loop.fetch_workers))
    print('  spot cache ttl  : {:.1f}s{}'.format(
        loop.spot_cache_ttl_sec,
        '  (DISABLED)' if loop.spot_cache_ttl_sec <= 0 else ''))
    print('  samples/asset   : {}'.format(args.samples))
    print('=' * 72)

    per_asset = {a: [] for a in assets}
    cycle_totals = []

    for i in range(args.samples):
        now = time.time()
        window_ts = current_window_ts(now)
        cycle_start = time.perf_counter()
        if args.full_cycle:
            detail = loop.run_cycle(now=now)
            print('  sample {}/{} FULL {:.3f}s  status={}'.format(
                i + 1, args.samples, time.perf_counter() - cycle_start,
                detail.get('status')))
        else:
            for asset in assets:
                t0 = time.perf_counter()
                _ctx, status, _detail = loop.build_context(window_ts, now, asset)
                elapsed = time.perf_counter() - t0
                per_asset[asset].append(elapsed)
                print('  sample {}/{} {:<4} {:.3f}s  status={}'.format(
                    i + 1, args.samples, asset, elapsed, status))
        cycle_totals.append(time.perf_counter() - cycle_start)
        if i + 1 < args.samples and args.gap_sec > 0:
            time.sleep(args.gap_sec)

    print('-' * 72)
    for asset in assets:
        _report('build_context ' + asset, per_asset[asset])
    _report('FULL run_cycle' if args.full_cycle else 'ALL ASSETS (one cycle)',
            cycle_totals)
    if args.full_cycle:
        print('  strategies/asset: {}  ->  {} evaluations/cycle'.format(
            len(loop.strategies), len(loop.strategies) * len(assets)))
        print('  identity_ok     : {}'.format(loop.stats()['identity_ok']))

    print('-' * 72)
    print('  step timings (seconds, summed over every build_context call):')
    for key in sorted(loop.timings):
        if key.endswith('_calls'):
            continue
        calls = loop.timings.get(key + '_calls') or 0
        total = loop.timings[key]
        avg = (total / calls) if calls else 0.0
        print('    {:<34} total={:.3f}s  calls={:<4} avg={:.3f}s'.format(
            key, total, calls, avg))

    print('-' * 72)
    reqs = dict(getattr(client, 'stats', {}) or {})
    print('  client requests : {}'.format(reqs.get('requests')))
    print('  client failures : {}'.format(reqs.get('failures')))
    print('  retries         : {}'.format(reqs.get('retries')))
    print('  spot reads      : {} (cache hits: {})'.format(
        int(loop.timings.get('spot_calls', 0)),
        sum(v for k, v in loop.health.items() if k.startswith('spot_cache_hit'))))
    print('  NOTE: compare `client requests` AND `spot reads` between '
          '--no-parallel\n        and the default. If either moved, the '
          'speedup is fewer reads, not\n        faster reads (convention 17). '
          '`client requests` alone is NOT enough:\n        the spot and kline '
          'reads bypass `client.get` and never reach that\n        counter, so '
          'a spot cache hit is invisible to it. That hole cost one\n        '
          'wrong measurement before this line existed.')

    median_cycle = statistics.median(cycle_totals) if cycle_totals else 0.0
    print('-' * 72)
    print('  MEDIAN CONTEXT PHASE PER CYCLE: {:.3f}s'.format(median_cycle))
    print('  This is the NETWORK phase only. A full cycle also runs '
          'manage_exits,')
    print('  the strategy evaluations and the sqlite writes. Do not read this '
          'as a')
    print('  sustainable poll interval on its own.')
    loop.store.close()
    client.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
