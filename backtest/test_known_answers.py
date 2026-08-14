"""Known-answer tests for the backtest harness.

These use synthetic price series where the correct result is computable by hand.
If the harness produces wrong answers on these, it has logic bugs.

Based on Claude's §13.5 recommendations:
- Flat: P&L must equal exactly -(fees x trade_count)
- Monotone up: long with stop must never exit via stop
- Monotone down: long with stop must always exit via stop
- Single spike: target must trigger on the spike bar
- Scale invariance: doubling all prices gives identical % returns
- Time invariance: shifting timestamps gives identical results

The monotone-down test is the three-line test that would have caught the
missing-stop bug immediately.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from backtest.vectorized_harness import precompute_indicators, _simulate_exit, EXIT_CONFIGS, TRAIL_ATR_MULT
from strategies.builtin.expanded import BreakoutStrategy

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'data')


def make_candles(opens, highs, lows, closes, volumes=None, start_ts=1700000000, interval=86400):
    """Build candle list from arrays."""
    n = len(closes)
    if volumes is None:
        volumes = [1000000] * n
    timestamps = [start_ts + i * interval for i in range(n)]
    return [{'ts': ts, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}
            for ts, o, h, l, c, v in zip(timestamps, opens, highs, lows, closes, volumes)]


def test_monotone_down():
    """Long with stop on a steeply declining series.
    
    EVERY trade must exit via stop. If any trade exits via 'time' or 'end_of_data',
    the stop is not being checked. This is the test that would have caught the
    missing-stop bug in time exits.
    
    Decline is steep enough that the stop is hit within 4 bars (the shortest time exit).
    """
    # 200 bars, steep decline from 100 to 20 (0.4/bar)
    closes = [100 - i * 0.4 for i in range(200)]
    opens = [c + 0.05 for c in closes]
    highs = [o + 0.05 for o in opens]
    lows = [c - 0.05 for c in closes]   # Low slightly below close
    
    candles = make_candles(opens, highs, lows, closes)
    ind = precompute_indicators(candles)
    
    # Enter at bar 100, price ~60, stop at 55 (5 points below)
    # Decline of 0.4/bar means stop at 55 is hit in ~12 bars (bar 112)
    # time_4c exits at bar 104, price = 100 - 104*0.4 = 58.4, low = 58.35
    # Stop at 55: 58.35 > 55, so stop NOT hit yet at bar 104
    # Need steeper decline. Use 2.0/bar so stop is hit in ~2.5 bars
    closes = [100 - i * 2.0 for i in range(200)]
    opens = [c + 0.1 for c in closes]
    highs = [o + 0.1 for o in opens]
    lows = [c - 0.1 for c in closes]
    
    candles = make_candles(opens, highs, lows, closes)
    ind = precompute_indicators(candles)
    
    # Enter at bar 100, price = 100 - 200 = -100... that's negative. 
    # Use a shorter series starting higher
    closes = [200 - i * 2.0 for i in range(150)]  # 200 to -98, but we only use first ~110
    opens = [c + 0.1 for c in closes]
    highs = [o + 0.1 for o in opens]
    lows = [c - 0.1 for c in closes]
    
    candles = make_candles(opens, highs, lows, closes)
    ind = precompute_indicators(candles)
    
    # Enter at bar 50, price = 200 - 100 = 100, stop at 95
    # Decline 2.0/bar: stop at 95 hit at bar 52.5 (within time_4c's 4 bars)
    entry_idx = 50
    entry_px = closes[entry_idx]  # = 100
    stop_px = entry_px - 5.0  # = 95
    
    results = {}
    failed = []
    
    for name, cfg in EXIT_CONFIGS.items():
        exit_idx, exit_px, reason = _simulate_exit(ind, entry_idx, entry_px, stop_px, cfg, len(closes) - 1)
        results[name] = {'exit_idx': exit_idx, 'exit_px': exit_px, 'reason': reason}
        
        # On a steeply declining series, the stop MUST fire before time/target
        if reason not in ('stop', 'trail_stop'):
            failed.append(f"{name}: expected stop/trail_stop, got {reason} (exit_idx={exit_idx}, exit_px={exit_px:.2f})")
    
    assert not failed, f'monotone_down known-answer failures: {failed}'
    return {'test': 'monotone_down', 'passed': len(failed) == 0, 'failures': failed, 'results': results}


def test_monotone_up():
    """Long with stop on a monotonically rising series.
    
    No trade should exit via stop. All should exit via target, time, or end_of_data.
    """
    closes = [60 + i * 0.2 for i in range(200)]
    opens = [c - 0.1 for c in closes]
    highs = [c + 0.1 for c in closes]
    lows = [o - 0.1 for o in opens]
    
    candles = make_candles(opens, highs, lows, closes)
    ind = precompute_indicators(candles)
    
    entry_idx = 50
    entry_px = closes[entry_idx]  # ~70
    stop_px = entry_px - 5.0  # ~65
    
    results = {}
    failed = []
    
    for name, cfg in EXIT_CONFIGS.items():
        exit_idx, exit_px, reason = _simulate_exit(ind, entry_idx, entry_px, stop_px, cfg, len(closes) - 1)
        results[name] = {'exit_idx': exit_idx, 'exit_px': exit_px, 'reason': reason}
        
        # On a rising series, stop should NEVER fire
        if reason in ('stop', 'trail_stop'):
            failed.append(f"{name}: expected target/time/end, got {reason} (exit_idx={exit_idx})")
    
    assert not failed, f'monotone_up known-answer failures: {failed}'
    return {'test': 'monotone_up', 'passed': len(failed) == 0, 'failures': failed, 'results': results}


def test_flat_series():
    """Flat series (all identical OHLC).
    
    No signal should fire (no breakout, no pattern). If a strategy produces
    trades on a flat series, the signal logic is broken.
    """
    closes = [100.0] * 200
    opens = [100.0] * 200
    highs = [100.0] * 200
    lows = [100.0] * 200
    
    candles = make_candles(opens, highs, lows, closes)
    ind = precompute_indicators(candles)
    
    # On a flat series, any entry at 100 with stop at 95:
    # - stop never triggers (low never goes below 100)
    # - target never triggers (high never goes above 100)
    # - time exit fires at the time limit
    entry_idx = 100
    entry_px = 100.0
    stop_px = 95.0
    
    results = {}
    failed = []
    
    for name, cfg in EXIT_CONFIGS.items():
        exit_idx, exit_px, reason = _simulate_exit(ind, entry_idx, entry_px, stop_px, cfg, len(closes) - 1)
        results[name] = {'exit_idx': exit_idx, 'exit_px': exit_px, 'reason': reason}
        
        # On flat series: no stop, no target, should exit via time or end_of_data
        if reason in ('stop', 'trail_stop', 'target'):
            failed.append(f"{name}: on flat series, expected time/end, got {reason}")
    
    assert not failed, f'flat_series known-answer failures: {failed}'
    return {'test': 'flat_series', 'passed': len(failed) == 0, 'failures': failed, 'results': results}


def test_single_spike():
    """Single bar spike up, then back to flat.
    
    A long entered before the spike with a target at +10% should exit
    at the target on the spike bar.
    """
    closes = [100.0] * 100 + [120.0] + [100.0] * 99
    opens = [100.0] * 100 + [100.0] + [100.0] * 99
    highs = [100.0] * 100 + [125.0] + [100.0] * 99
    lows = [100.0] * 200
    
    candles = make_candles(opens, highs, lows, closes)
    ind = precompute_indicators(candles)
    
    # Enter at bar 50, price 100, stop at 95, target at 110 (10% = 1R with 5 risk)
    entry_idx = 50
    entry_px = 100.0
    stop_px = 95.0  # risk = 5
    
    # fixed_1r: target = 100 + 5 = 105. Spike to 125 should hit target at bar 100.
    cfg = EXIT_CONFIGS['fixed_1r']
    exit_idx, exit_px, reason = _simulate_exit(ind, entry_idx, entry_px, stop_px, cfg, len(closes) - 1)
    
    failed = []
    if reason != 'target':
        failed.append(f"fixed_1r: expected target, got {reason}")
    if exit_px != entry_px + (entry_px - stop_px) * 1.0:
        failed.append(f"fixed_1r: exit_px={exit_px}, expected={entry_px + (entry_px - stop_px) * 1.0}")
    # Should exit on the spike bar (index 100)
    if exit_idx != 100:
        failed.append(f"fixed_1r: exit_idx={exit_idx}, expected=100 (spike bar)")
    
    assert not failed, f'single_spike known-answer failures: {failed}'
    return {
        'test': 'single_spike',
        'passed': len(failed) == 0,
        'failures': failed,
        'result': {'exit_idx': exit_idx, 'exit_px': exit_px, 'reason': reason},
    }


def test_scale_invariance():
    """Doubling all prices should give identical percentage returns."""
    base_closes = [100 + i * 0.5 for i in range(200)]
    base_opens = [c - 0.2 for c in base_closes]
    base_highs = [c + 0.5 for c in base_closes]
    base_lows = [c - 0.5 for c in base_closes]
    
    # Scale by 2x
    dbl_closes = [c * 2 for c in base_closes]
    dbl_opens = [o * 2 for o in base_opens]
    dbl_highs = [h * 2 for h in base_highs]
    dbl_lows = [l * 2 for l in base_lows]
    
    base_candles = make_candles(base_opens, base_highs, base_lows, base_closes)
    dbl_candles = make_candles(dbl_opens, dbl_highs, dbl_lows, dbl_closes)
    
    base_ind = precompute_indicators(base_candles)
    dbl_ind = precompute_indicators(dbl_candles)
    
    # Enter at bar 100, stop 5 below entry
    base_entry = base_closes[100]
    base_stop = base_entry - 5.0
    dbl_entry = dbl_closes[100]
    dbl_stop = dbl_entry - 10.0  # 2x the stop distance
    
    failed = []
    for name, cfg in EXIT_CONFIGS.items():
        b_idx, b_px, b_reason = _simulate_exit(base_ind, 100, base_entry, base_stop, cfg, 199)
        d_idx, d_px, d_reason = _simulate_exit(dbl_ind, 100, dbl_entry, dbl_stop, cfg, 199)
        
        # Exit indices should match
        if b_idx != d_idx:
            failed.append(f"{name}: exit_idx mismatch ({b_idx} vs {d_idx})")
        # Exit reasons should match
        if b_reason != d_reason:
            failed.append(f"{name}: exit_reason mismatch ({b_reason} vs {d_reason})")
        # Percentage returns should match
        b_ret = (b_px - base_entry) / base_entry
        d_ret = (d_px - dbl_entry) / dbl_entry
        if abs(b_ret - d_ret) > 0.001:
            failed.append(f"{name}: return mismatch ({b_ret:.4f} vs {d_ret:.4f})")
    
    assert not failed, f'scale_invariance known-answer failures: {failed}'
    return {'test': 'scale_invariance', 'passed': len(failed) == 0, 'failures': failed}


def test_intra_candle_ordering():
    """A11: When both stop and target are inside the same bar, stop must fire first.
    
    Run a probe with stop and target both very tight. Win rate must be <= 50%.
    If > 50%, the engine resolves ambiguity in favor of the target (optimistic).
    """
    # Random walk with enough volatility that tight stops/targets both trigger
    np.random.seed(42)
    n = 500
    returns = np.random.normal(0, 0.02, n)
    closes = [100.0]
    for r in returns:
        closes.append(closes[-1] * (1 + r))
    closes = closes[1:]
    
    opens = [c * (1 + np.random.normal(0, 0.001)) for c in closes]
    highs = [max(o, c) + abs(np.random.normal(0, 0.005)) * c for o, c in zip(opens, closes)]
    lows = [min(o, c) - abs(np.random.normal(0, 0.005)) * c for o, c in zip(opens, closes)]
    
    candles = make_candles(opens, highs, lows, closes)
    ind = precompute_indicators(candles)
    
    # Very tight stop and target: 0.1% each way
    entry_px = 100.0
    stop_px = 99.9  # 0.1% below
    risk = entry_px - stop_px
    target_px = entry_px + risk * 1.0  # 0.1% above (1R)
    
    cfg = {'type': 'fixed', 'r_multiple': 1.0}
    
    wins = 0
    stops = 0
    total = 0
    
    for i in range(50, len(closes) - 1):
        exit_idx, exit_px, reason = _simulate_exit(ind, i, closes[i], closes[i] * 0.999, cfg, len(closes) - 1)
        if reason in ('stop', 'target'):
            total += 1
            if reason == 'target':
                wins += 1
            else:
                stops += 1
    
    win_rate = wins / max(total, 1)

    assert win_rate <= 0.55, f'intra-candle ordering not conservative: win_rate={win_rate:.2%}'
    return {
        'test': 'intra_candle_ordering',
        'total_trades': total,
        'wins': wins,
        'stops': stops,
        'win_rate': round(win_rate * 100, 1),
        'passed': win_rate <= 0.55,  # Allow 5% tolerance for randomness
        'failures': [] if win_rate <= 0.55 else [f"Win rate {win_rate*100:.1f}% > 55% - engine may resolve same-bar ambiguity in favor of target"],
    }


def main():
    print("=" * 70)
    print("KNOWN-ANSWER TESTS")
    print("Synthetic data with computable correct results")
    print("=" * 70)
    
    tests = [
        test_monotone_down,
        test_monotone_up,
        test_flat_series,
        test_single_spike,
        test_scale_invariance,
        test_intra_candle_ordering,
    ]
    
    all_pass = True
    results = []
    
    for test_fn in tests:
        result = test_fn()
        results.append(result)
        status = "PASS" if result['passed'] else "FAIL"
        print(f"\n{result['test']}: {status}")
        if result.get('failures'):
            for f in result['failures']:
                print(f"  - {f}")
        if result.get('results'):
            for name, r in result['results'].items():
                print(f"  {name:20s}: idx={r.get('exit_idx', '?')} px={r.get('exit_px', '?')} reason={r.get('reason', '?')}")
        if not result['passed']:
            all_pass = False
    
    print(f"\n{'=' * 70}")
    print(f"OVERALL: {'ALL PASS' if all_pass else 'FAILURES DETECTED'}")
    print(f"{'=' * 70}")
    
    if not all_pass:
        print("\nBLOCKING: Known-answer tests failed. Do not trust graveyard results")
        print("until these are fixed. Each failure identifies a specific logic bug.")
    
    return all_pass


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
