"""Lab v5 P4 precheck: is the variance-ratio fingerprint a TRAIT or WEATHER?

Source: references/strategy-lab-v5.md, P4 "Fingerprint Router", kill
condition (a):

    "trait instability - cross-half Spearman correlation of instrument VRs
    < 0.3 => the fingerprint is weather, not character, and routing dies
    before P&L is even read."

This script IS that kill condition, run first and alone. It touches no
strategy P&L anywhere - that is the point: the router's discovery variable
must be judged on data that never meets a backtest result, or P4 becomes
the winner-filtering it explicitly promises not to be.

METHOD
Lo & MacKinlay variance ratio: VR(q) = Var(q-period log returns) /
(q * Var(1-period log returns)). VR < 1 leans mean-reverting, VR > 1 leans
trending. Computed per instrument on DAILY bars, separately on the first
and second calendar halves of each series. If the trait is real, an
instrument's rank among its peers should persist across halves: Spearman
rank correlation across instruments, per metric.

The v5 doc names two metrics: VR(5,1) and VR(20,5) = Var(20d)/(4*Var(5d)).

VERDICT RULE (pre-registered, from the doc): Spearman >= 0.3 on a metric =
that fingerprint is stable enough to route on. Below 0.3 on BOTH metrics =
P4 dies here.

Run: python3 backtest/vr_fingerprint.py
Writes research/graveyard/vr_fingerprint.json
"""
import glob
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.data_loader import load_csv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'backtest', 'data')

MIN_BARS_PER_HALF = 200      # a VR on fewer daily bars is mostly noise
SPEARMAN_BAR = 0.3           # the doc's own kill threshold

# Metadata files that aren't tradable OHLCV series (mirrors the graveyard's
# skip list).
SKIP = {
    'funding_okx', 'funding_gate', 'deribit_hvol', 'stablecoin', 'btc_marketcap',
    'btc_price', 'eth_marketcap', 'eth_price', 'sol_marketcap', 'crypto_global',
    'crypto_dominance', 'google_trends', 'reddit_wsb', 'finra_shortvol', 'VIX',
    'VX_F',
}


def log_returns(closes, step):
    """Non-overlapping step-period log returns."""
    out = []
    for i in range(step, len(closes), step):
        a, b = closes[i - step], closes[i]
        if a > 0 and b > 0:
            out.append(math.log(b / a))
    return out


def variance(xs):
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    return sum((x - m) ** 2 for x in xs) / (n - 1)


def vr(closes, q, base):
    """VR(q, base): Var(q-period) / ((q/base) * Var(base-period)).
    Non-overlapping windows; simple estimator - we need ranks, not
    asymptotics."""
    vq = variance(log_returns(closes, q))
    vb = variance(log_returns(closes, base))
    if vq is None or vb is None or vb <= 0:
        return None
    return vq / ((q / base) * vb)


def spearman(pairs):
    """Spearman rank correlation of [(x, y), ...] without scipy."""
    n = len(pairs)
    if n < 3:
        return None

    def ranks(vals):
        order = sorted(range(n), key=lambda i: vals[i])
        r = [0.0] * n
        i = 0
        while i < n:                      # average ranks for ties
            j = i
            while j + 1 < n and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1.0
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx = ranks([p[0] for p in pairs])
    ry = ranks([p[1] for p in pairs])
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    sx = math.sqrt(sum((v - mx) ** 2 for v in rx))
    sy = math.sqrt(sum((v - my) ** 2 for v in ry))
    if sx == 0 or sy == 0:
        return None
    return cov / (sx * sy)


def main():
    per_instrument = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, '*_1d.csv'))):
        ticker = os.path.basename(path)[:-len('_1d.csv')]
        if ticker in SKIP:
            continue
        candles = load_csv(path)
        closes = [c['close'] for c in candles]
        half = len(closes) // 2
        if half < MIN_BARS_PER_HALF:
            continue
        first, second = closes[:half], closes[half:]
        row = {}
        for label, q, base in (('vr_5_1', 5, 1), ('vr_20_5', 20, 5)):
            a, b = vr(first, q, base), vr(second, q, base)
            if a is None or b is None:
                row = None
                break
            row[label] = {'first_half': round(a, 4), 'second_half': round(b, 4)}
        if row:
            per_instrument[ticker] = row

    results = {}
    for label in ('vr_5_1', 'vr_20_5'):
        pairs = [(v[label]['first_half'], v[label]['second_half'])
                 for v in per_instrument.values()]
        rho = spearman(pairs)
        results[label] = {
            'instruments': len(pairs),
            'spearman_cross_half': None if rho is None else round(rho, 4),
            'stable': bool(rho is not None and rho >= SPEARMAN_BAR),
        }

    any_stable = any(r['stable'] for r in results.values())
    verdict = ('FINGERPRINT STABLE - P4 may proceed to routed backtests'
               if any_stable else
               'FINGERPRINT UNSTABLE - P4 dies here (kill condition a), '
               'no routed backtest should be run')

    out = {
        'precheck': 'lab_v5_p4_kill_condition_a',
        'source': 'references/strategy-lab-v5.md',
        'spearman_bar': SPEARMAN_BAR,
        'min_bars_per_half': MIN_BARS_PER_HALF,
        'results': results,
        'verdict': verdict,
        'note': ('computed on daily closes only, split at each series '
                 'calendar midpoint; touches no strategy P&L anywhere'),
        'per_instrument': per_instrument,
    }
    dest = os.path.join(ROOT, 'research', 'graveyard', 'vr_fingerprint.json')
    with open(dest, 'w') as f:
        json.dump(out, f, indent=1)

    print(f"instruments with two qualifying halves: {len(per_instrument)}")
    for label, r in results.items():
        print(f"{label}: cross-half Spearman = {r['spearman_cross_half']} "
              f"(bar {SPEARMAN_BAR}) -> {'STABLE' if r['stable'] else 'unstable'}")
    print(verdict)
    print(f"saved: {dest}")
    return 0 if any_stable else 1


if __name__ == '__main__':
    sys.exit(main())
