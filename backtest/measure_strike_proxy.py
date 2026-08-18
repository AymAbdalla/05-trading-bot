"""Score the proxy strike against the Gamma oracle. The named harness for it.

`engine/polymarket/strike.py` serves a 60-second TWAP built from Binance.US 1m
klines as a stand-in for the Chainlink TWAP that Gamma does not publish. That
substitution is only defensible if its error is measured, so this is the thing
that measures it.

METHOD
------
For each completed 5m window the oracle has resolved:
  1. proxy_open  = TWAP60 ending at window_ts
  2. proxy_close = TWAP60 ending at window_ts + 300
  3. predicted   = UP if proxy_close >= proxy_open else DOWN
  4. truth       = the Gamma oracle's `resolved_outcome`
  5. bucket the result by |move| in bps

The prediction uses the proxy and NOTHING else - no orderbook, no market price.
That is the point. Any accuracy it shows is the proxy's, not a strategy's.

WHAT THE OUTPUT MEANS
---------------------
The headline rate is close to useless on its own, because the proxy's error is
overwhelmingly concentrated at small moves. The BUCKETED table is the result.
A single "85% accurate" number would hide that the proxy is a coin flip below
1 bp, which is the only fact that actually constrains strategy design.

Windows the oracle has not resolved are DROPPED and counted, never inferred
from price (convention 11). An unresolved window is not a wrong prediction and
must not be scored as one.

USAGE
    env -u PYTHONPATH python3 backtest/measure_strike_proxy.py --windows 500
    env -u PYTHONPATH python3 backtest/measure_strike_proxy.py --json out.json

EXIT CODES
    0  measurement completed (whatever it found)
    2  too few windows scored to say anything (see --min-windows)
"""
import argparse
import json
import logging
import os
import sys
import time
from collections import Counter
from typing import Dict, List, Optional

import requests

# Run as a script from anywhere: this module is invoked directly and `backtest/`
# is not the package root, so the repo root has to go on the path before the
# first project import. Matches the pattern in build_graveyard.py.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.client import PolymarketClient
from engine.polymarket.markets import (get_btc_updown_5m_checked,
                                       current_window_ts)
from engine.polymarket.strike import (KLINES_URL, TWAP_LOOKBACK_SEC, _ohlc4,
                                      STRIKE_PROXY_NOISE_FLOOR_BPS)

logger = logging.getLogger(__name__)

WINDOW_DURATION = 300
MAX_KLINES_LIMIT = 1000

# Bucket edges in bps. The first two are deliberately narrow: that is where the
# proxy fails, and a coarse bucketing would average the failure away.
BUCKETS = ((0.0, 1.0), (1.0, 2.0), (2.0, 5.0), (5.0, 10.0), (10.0, float('inf')))

# Cumulative thresholds worth reporting, because these are the numbers a
# strategy's entry gate gets compared against.
CUMULATIVE = (1.0, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0)


def fetch_klines(session: requests.Session, start_ts: int, end_ts: int,
                 symbol: str = 'BTCUSDT',
                 timeout: float = 25.0) -> Dict[int, list]:
    """Every 1m bar in [start_ts, end_ts], paged around the 1000-bar cap.

    Paging matters: 500 windows is 2500 minutes, which is well past one
    request. Silently taking the first 1000 bars would score only the most
    recent fifth of the requested range while reporting the full count.
    """
    bars: Dict[int, list] = {}
    cursor = start_ts
    while cursor <= end_ts:
        try:
            resp = session.get(
                KLINES_URL,
                params={'symbol': symbol, 'interval': '1m',
                        'startTime': cursor * 1000,
                        'endTime': end_ts * 1000,
                        'limit': MAX_KLINES_LIMIT},
                timeout=timeout)
        except requests.RequestException as exc:
            logger.warning('klines request failed at cursor %s: %s', cursor, exc)
            break
        if resp.status_code != 200:
            logger.warning('klines HTTP %s at cursor %s', resp.status_code, cursor)
            break
        try:
            payload = resp.json()
        except ValueError:
            logger.warning('klines bad JSON at cursor %s', cursor)
            break
        if not isinstance(payload, list) or not payload:
            break

        newest = cursor
        for bar in payload:
            try:
                open_ts = int(bar[0]) // 1000
            except (TypeError, ValueError, IndexError):
                continue
            bars[open_ts] = bar
            newest = max(newest, open_ts)
        if newest <= cursor:
            break                      # no forward progress; stop rather than spin
        cursor = newest + 60
    return bars


def measure(windows: int = 120, symbol: str = 'BTCUSDT',
            client: Optional[PolymarketClient] = None) -> dict:
    """Replay `windows` completed 5m windows and score the proxy on each."""
    client = client or PolymarketClient()
    now_window = current_window_ts()
    # +2 windows of slack so the TWAP lookback at the oldest window still has
    # a bar behind it.
    start = now_window - (windows + 2) * WINDOW_DURATION
    bars = fetch_klines(client.session, start - TWAP_LOOKBACK_SEC,
                        now_window + WINDOW_DURATION, symbol=symbol)

    def twap60(at_ts: int) -> Optional[float]:
        bar = bars.get(at_ts - TWAP_LOOKBACK_SEC)
        return None if bar is None else _ohlc4(bar)

    scored: List[dict] = []
    drops: Counter = Counter()

    for i in range(windows, 0, -1):
        ts = now_window - i * WINDOW_DURATION
        market, status = get_btc_updown_5m_checked(client, ts)
        if market is None:
            drops['not_listed' if status == 'not_found' else status] += 1
            continue
        if not market.is_binary:
            drops['not_binary'] += 1
            continue
        truth = market.resolved_outcome
        if truth is None:
            drops['unresolved'] += 1
            continue
        p_open, p_close = twap60(ts), twap60(ts + WINDOW_DURATION)
        if p_open is None:
            drops['no_kline_at_open'] += 1
            continue
        if p_close is None:
            drops['no_kline_at_close'] += 1
            continue

        move_bps = (p_close - p_open) / p_open * 10_000.0
        predicted = 'UP' if p_close >= p_open else 'DOWN'
        scored.append({
            'window_ts': ts,
            'proxy_open': p_open,
            'proxy_close': p_close,
            'move_bps': move_bps,
            'abs_move_bps': abs(move_bps),
            'predicted': predicted,
            'oracle': truth.strip().upper(),
            'agree': predicted == truth.strip().upper(),
        })

    # Convention 20: the accounting identity is ASSERTED, not hoped for. Every
    # requested window either got scored or got a categorised drop.
    assert len(scored) + sum(drops.values()) == windows, (
        'window accounting broken: scored={} drops={} requested={}'.format(
            len(scored), sum(drops.values()), windows))

    buckets = []
    for lo, hi in BUCKETS:
        sub = [r for r in scored if lo <= r['abs_move_bps'] < hi]
        dis = sum(1 for r in sub if not r['agree'])
        buckets.append({
            'lo_bps': lo,
            'hi_bps': None if hi == float('inf') else hi,
            'n': len(sub),
            'disagreements': dis,
            # None, not 0.0, when the bucket is empty. A rate over zero samples
            # is undefined and printing 0% would read as "perfect here".
            'rate_pct': (None if not sub else round(dis / len(sub) * 100.0, 1)),
        })

    cumulative = []
    for thr in CUMULATIVE:
        sub = [r for r in scored if r['abs_move_bps'] >= thr]
        dis = sum(1 for r in sub if not r['agree'])
        cumulative.append({
            'threshold_bps': thr,
            'n': len(sub),
            'disagreements': dis,
            'rate_pct': (None if not sub else round(dis / len(sub) * 100.0, 1)),
        })

    total_dis = sum(1 for r in scored if not r['agree'])
    return {
        'measured_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
        'symbol': symbol,
        'twap_lookback_sec': TWAP_LOOKBACK_SEC,
        'configured_noise_floor_bps': STRIKE_PROXY_NOISE_FLOOR_BPS,
        'windows_requested': windows,
        'windows_scored': len(scored),
        'drops': dict(drops),
        'headline_disagreement_pct': (None if not scored else
                                      round(total_dis / len(scored) * 100.0, 1)),
        'buckets': buckets,
        'cumulative': cumulative,
    }


def render(result: dict) -> str:
    lines = []
    lines.append('=' * 66)
    lines.append('PROXY STRIKE vs GAMMA ORACLE')
    lines.append('=' * 66)
    lines.append(f"  measured at    : {result['measured_at']}")
    lines.append(f"  symbol         : {result['symbol']}  (TWAP {result['twap_lookback_sec']}s)")
    lines.append(f"  windows scored : {result['windows_scored']} of {result['windows_requested']}")
    lines.append(f"  drops          : {result['drops'] or 'none'}")
    lines.append(f"  headline rate  : {result['headline_disagreement_pct']}%"
                 '   <- do not quote this alone; see the buckets')
    lines.append('')
    lines.append('  |move| bucket        n   disagree     rate')
    for b in result['buckets']:
        hi = 'inf' if b['hi_bps'] is None else f"{b['hi_bps']:g}"
        rate = 'n/a' if b['rate_pct'] is None else f"{b['rate_pct']}%"
        lines.append(f"  {b['lo_bps']:>6g} - {hi:>6}  {b['n']:>6} {b['disagreements']:>10} {rate:>8}")
    lines.append('')
    lines.append('  cumulative (this is what an entry gate is compared against)')
    for c in result['cumulative']:
        rate = 'n/a' if c['rate_pct'] is None else f"{c['rate_pct']}%"
        lines.append(f"    |move| >= {c['threshold_bps']:>4g} bps :"
                     f" n={c['n']:>4}  disagree={c['disagreements']:>3}  rate={rate:>7}")
    lines.append('')
    floor = result['configured_noise_floor_bps']
    match = next((c for c in result['cumulative']
                  if c['threshold_bps'] == floor), None)
    if match and match['rate_pct'] is not None:
        lines.append(f"  configured noise floor is {floor:g} bps -> measured error"
                     f" {match['rate_pct']}% on n={match['n']}")
        if match['n'] < 100:
            lines.append('  WARNING: n < 100. Convention 7 - this is a shrug, not a verdict.')
    else:
        lines.append(f"  configured noise floor {floor:g} bps was NOT scored"
                     ' (no windows at or above it). NOT_TESTED.')
    lines.append('=' * 66)
    return '\n'.join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--windows', type=int, default=120,
                   help='completed 5m windows to replay (default 120)')
    p.add_argument('--symbol', default='BTCUSDT')
    p.add_argument('--min-windows', type=int, default=20,
                   help='exit 2 if fewer than this many windows scored')
    p.add_argument('--json', dest='json_path',
                   help='also write the full result to this path')
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')
    result = measure(windows=args.windows, symbol=args.symbol)
    print(render(result))

    if args.json_path:
        with open(args.json_path, 'w') as f:
            # allow_nan=False: a non-finite must fail at write time, not turn
            # into an `Infinity` token that json.loads accepts and every other
            # parser rejects (convention 19).
            json.dump(result, f, indent=2, allow_nan=False)
        print(f'wrote {args.json_path}')

    if result['windows_scored'] < args.min_windows:
        print(f"\nNOT_TESTED: only {result['windows_scored']} windows scored,"
              f" need {args.min_windows}. This is 'could not run', not 'no error'.")
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
