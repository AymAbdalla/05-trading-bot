"""Fast go/no-go: all 30 entry strategies across 3 pairs, 12 months.
Loosened confirmation stack (volume 1.2x instead of 1.5x, no location filter).
No stress probes for speed. Runs the go/no-go checkpoint only.
"""
import sys, os, glob, logging, yaml, json, time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.harness import BacktestHarness
from backtest.data_loader import load_csv
from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
from indicators.macd_stoch import macd_crossover, stochastic_rsi

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def load_pair(pair_symbol, tf='15m'):
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'data')
    files = sorted(glob.glob(os.path.join(data_dir, f"{pair_symbol}-{tf}-*.csv")))
    if not files:
        return []
    all_candles = []
    for f in files:
        all_candles.extend(load_csv(f))
    all_candles.sort(key=lambda c: c['ts'])
    seen = set(); deduped = []
    for c in all_candles:
        if c['ts'] not in seen:
            seen.add(c['ts']); deduped.append(c)
    return deduped


def main():
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    with open(config_path) as f:
        config = yaml.safe_load(f)

    # Loosen confirmation stack for more trades
    conf = config['strategy']['confirmation']
    conf['volume_min_ratio'] = 1.2  # was 1.5
    conf['location_atr_mult'] = 5.0  # effectively disable location filter (was 1.5)
    conf['rsi_max_entry'] = 70  # was 60, allow more entries

    harness = BacktestHarness(config)

    pairs = [('BTCUSDT', 'BTC/USDT'), ('ETHUSDT', 'ETH/USDT'), ('SOLUSDT', 'SOL/USDT')]

    print("\n" + "="*80)
    print("T7 GO/NO-GO: 30 strategies x 3 pairs x 12 months (loosened confirmation)")
    print("="*80)

    all_verdicts = {}

    for binance_sym, display_pair in pairs:
        logger.info(f"\nLoading {display_pair}...")
        candles_15m = load_pair(binance_sym, '15m')
        candles_1h = load_pair(binance_sym, '1h')

        if len(candles_15m) < 200:
            logger.error(f"Insufficient data for {display_pair}")
            continue

        logger.info(f"  {len(candles_15m)} 15m candles, {len(candles_1h)} 1h candles")

        # Split: use full data, test on last 20%
        train_pct = 0.6; val_pct = 0.2
        n = len(candles_15m)
        test = candles_15m[int(n * 0.8):]

        print(f"\n--- {display_pair} (test: {len(test)} candles) ---")
        print(f"{'Strategy':<30s} {'Trades':>6s} {'PF':>7s} {'Ret%':>7s} {'BH%':>7s} {'Twin':>6s} {'bBH':>4s} {'bTwin':>5s} {'Verdict'}")
        print("-" * 90)

        pair_verdicts = {}
        for strategy in ENTRY_STRATEGIES_EXPANDED:
            result = harness.run_strategy_on_candles(
                strategy, test, candles_1h, display_pair
            )
            r = result.to_report()
            v = harness.go_no_go({strategy.name: result}, min_pf=1.15)
            verdict = 'PASS' if v[strategy.name]['pass'] else 'FAIL'
            bh = 'Y' if r['beats_buy_hold'] else 'N'
            twin = 'Y' if r['beats_random_twin'] else 'N'

            # PF fields are None when infinite (zero losing trades)
            pf_s = 'inf' if r['profit_factor'] is None else f"{r['profit_factor']:.2f}"
            twin_s = 'inf' if r['random_twin_pf'] is None else f"{r['random_twin_pf']:.2f}"
            print(f"{strategy.name:<30s} {r['trade_count']:>6d} {pf_s:>7s} "
                  f"{r['strategy_return_pct']:>7.2f} {r['buy_hold_return_pct']:>7.2f} "
                  f"{twin_s:>6s} {bh:>4s} {twin:>5s} {verdict}")

            pair_verdicts[strategy.name] = v[strategy.name]

            # Strategy inversion for PF < 0.5
            if result.profit_factor < 0.5 and result.trade_count >= 10:
                print(f"  >>> INVERSION: PF={result.profit_factor:.2f} < 0.5, pattern may be anti-correlated")
                print(f"      Recommended: test as exit signal (fade)")

        all_verdicts[display_pair] = pair_verdicts

    # Summary
    print("\n" + "="*80)
    print("SUMMARY")
    print("="*80)
    total_pass = 0; total_fail = 0
    for pair, verdicts in all_verdicts.items():
        for name, v in verdicts.items():
            if v['pass']:
                total_pass += 1
                print(f"  PASS: {pair} / {name}")
            else:
                total_fail += 1

    print(f"\nTotal: {total_pass} passed, {total_fail} failed out of {total_pass + total_fail}")

    if total_pass > 0:
        print("\nPROCEED to T8. Passing strategies can be used.")
    else:
        print("\nNO STRATEGIES PASSED. All go to graveyard for Quant's diagnosis.")


if __name__ == '__main__':
    main()
