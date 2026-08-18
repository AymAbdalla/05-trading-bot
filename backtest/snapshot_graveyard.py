#!/usr/bin/env python3
"""Streaming snapshot of the graveyard headline numbers (convention 17 baseline).

Reads `research/graveyard/v0_graveyard_full.json` object-by-object instead of
`json.load`ing 389MB into RAM, and writes the pre-resweep baseline that the
post-sweep comparison is made against.

Usage:  env -u PYTHONPATH python3 backtest/snapshot_graveyard.py <out.json>
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GRAVEYARD = ROOT / 'research' / 'graveyard' / 'v0_graveyard_full.json'

# The nine strategies that produced zero trades in the pre-fix sweep
# (docs/handoffs/2026-08-17-nonfiring-nine-diagnosis.md).
NONFIRING_NINE = [
    'rsi_extreme',
    'C2',
    'V2_vwap_magnet',
    'V2_vwap_magnet_sessionatr',
    'V3_intraday_momentum_crypto',
    'V4_trend_reclaim',
    'V5_capitulation_equity',
    'V5_forced_flow_crypto',
    'C5_btc_dominance_rotation',
]


def iter_entries(path):
    """Yield each entry dict from the "entries" array without loading the file.

    The writer uses json.dump(indent=2), so every entry object opens on a line
    that is exactly six spaces + '{' and closes on six spaces + '}'. Rather
    than trusting that, accumulate from the first '{' after "entries" and use
    raw_decode on the buffer, which is correct for any formatting.
    """
    dec = json.JSONDecoder()
    buf = ''
    started = False
    with open(path) as f:
        for chunk in iter(lambda: f.read(1 << 22), ''):
            buf += chunk
            if not started:
                key = buf.find('"entries"')
                if key == -1:
                    # keep a tail in case the key straddles a chunk boundary
                    buf = buf[-32:]
                    continue
                bracket = buf.find('[', key)
                if bracket == -1:
                    continue
                buf = buf[bracket + 1:]
                started = True
            while True:
                stripped = buf.lstrip(' \n\r\t,')
                if not stripped or stripped[0] != '{':
                    buf = stripped
                    break
                try:
                    obj, end = dec.raw_decode(stripped)
                except ValueError:
                    buf = stripped
                    break
                yield obj
                buf = stripped[end:]


def main():
    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else (
        ROOT / 'docs' / 'handoffs' / 'pre-resweep-snapshot.json')

    verdicts = Counter()
    total = 0
    distinct = set()
    tf_series = defaultdict(set)          # timeframe -> {(ticker, timeframe)}
    tf_rows = Counter()
    nine = {s: {'rows': 0, 'total_trades': 0, 'verdicts': Counter(),
                'max_trades': 0, 'reasons': Counter()}
            for s in NONFIRING_NINE}
    strat_rows = Counter()
    strat_trades = Counter()
    c2_stale = 0
    stale_reason = 'needs 840 bars, scan window is 260'

    for e in iter_entries(GRAVEYARD):
        total += 1
        v = e.get('verdict') or 'UNKNOWN'
        verdicts[v] += 1
        # distinct_findings: one finding per (strategy, ticker, timeframe),
        # collapsing the 11 exit configs (convention 2).
        distinct.add((e.get('strategy'), e.get('ticker'), e.get('timeframe')))
        tf = e.get('timeframe')
        tf_series[tf].add(e.get('ticker'))
        tf_rows[tf] += 1
        s = e.get('strategy')
        strat_rows[s] += 1
        strat_trades[s] += int(e.get('trades') or 0)
        if s in nine:
            n = nine[s]
            n['rows'] += 1
            t = int(e.get('trades') or 0)
            n['total_trades'] += t
            n['max_trades'] = max(n['max_trades'], t)
            n['verdicts'][v] += 1
            r = e.get('reason') or e.get('not_tested_reason') or ''
            if r:
                n['reasons'][r] += 1
        if s == 'C2':
            blob = json.dumps(e)
            if stale_reason in blob:
                c2_stale += 1

    snapshot = {
        'snapshot_of': str(GRAVEYARD.relative_to(ROOT)),
        'graveyard_generated': None,
        'total_rows': total,
        'distinct_findings': len(distinct),
        'verdict_counts': dict(verdicts),
        'per_timeframe': {
            tf: {'rows': tf_rows[tf], 'distinct_tickers': len(tf_series[tf])}
            for tf in sorted(tf_rows)
        },
        'nonfiring_nine': {
            s: {
                'rows': d['rows'],
                'total_trades': d['total_trades'],
                'max_trades_in_any_row': d['max_trades'],
                'verdicts': dict(d['verdicts']),
                'reasons': dict(d['reasons'].most_common(5)),
            }
            for s, d in nine.items()
        },
        'c2_stale_rows_matching_reason': c2_stale,
        'stale_reason_string': stale_reason,
        'per_strategy_rows': dict(strat_rows.most_common()),
        'per_strategy_total_trades': dict(strat_trades.most_common()),
    }

    with open(GRAVEYARD) as f:
        head = f.read(400)
    for line in head.splitlines():
        if '"generated"' in line:
            snapshot['graveyard_generated'] = line.split(':', 1)[1].strip().strip('",')
            break

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, 'w') as f:
        json.dump(snapshot, f, indent=2, allow_nan=False, sort_keys=False)
    print(f"wrote {out_path}")
    print(f"total_rows={total} distinct_findings={len(distinct)} "
          f"verdicts={dict(verdicts)} c2_stale={c2_stale}")


if __name__ == '__main__':
    main()
