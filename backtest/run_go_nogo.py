"""Run the T7 go/no-go backtest on real historical data.

Loads 12 months of Binance spot data from CSV files,
runs all strategies through the backtest harness, and
produces a go/no-go report for each pair.
"""
import sys
import os
import glob
import logging

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.harness import BacktestHarness, ENTRY_STRATEGIES
from backtest.data_loader import load_csv
from backtest.report import generate_full_report
import yaml

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def load_pair_data(pair_symbol: str, tf: str = '15m') -> list:
    """Load all monthly CSVs for a pair and timeframe, sorted chronologically."""
    data_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'data')
    pattern = os.path.join(data_dir, f"{pair_symbol}-{tf}-*.csv")
    files = sorted(glob.glob(pattern))

    if not files:
        logger.error(f"No CSV files found for {pair_symbol} {tf}: {pattern}")
        return []

    all_candles = []
    for f in files:
        candles = load_csv(f, pair_symbol, tf)
        all_candles.extend(candles)
        logger.info(f"  {os.path.basename(f)}: {len(candles)} candles")

    # Sort by timestamp and dedup
    all_candles.sort(key=lambda c: c['ts'])
    seen = set()
    deduped = []
    for c in all_candles:
        if c['ts'] not in seen:
            seen.add(c['ts'])
            deduped.append(c)

    logger.info(f"Total {pair_symbol} {tf}: {len(deduped)} candles "
                f"({len(files)} files)")
    return deduped


def main():
    # Load config
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    with open(config_path) as f:
        config = yaml.safe_load(f)

    harness = BacktestHarness(config)

    pairs = [
        ('BTCUSDT', 'BTC/USDT'),
        ('ETHUSDT', 'ETH/USDT'),
        ('SOLUSDT', 'SOL/USDT'),
    ]

    all_results = {}
    all_verdicts = {}

    for binance_symbol, display_pair in pairs:
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing {display_pair}")
        logger.info(f"{'='*60}")

        # Load 15m signal candles and 1h regime candles
        candles_15m = load_pair_data(binance_symbol, '15m')
        candles_1h = load_pair_data(binance_symbol, '1h')

        if len(candles_15m) < 200 or len(candles_1h) < 100:
            logger.error(f"Insufficient data for {display_pair}, skipping")
            continue

        logger.info(f"Running backtest on {display_pair}...")
        results = harness.run_full_backtest(candles_15m, candles_1h, display_pair)
        verdicts = harness.go_no_go(results)

        all_results[display_pair] = results
        all_verdicts[display_pair] = verdicts

        # Run stress probes for each strategy
        stress_results = {}
        for strategy in ENTRY_STRATEGIES:
            logger.info(f"  Stress probes for {strategy.name}...")
            stress_results[strategy.name] = harness.run_stress_probes(
                strategy, candles_15m, candles_1h, display_pair
            )

        # Generate report
        report_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            'backtest', 'reports', f'{binance_symbol.lower()}-go-nogo-report.md'
        )
        report = generate_full_report(
            test_results=results,
            walk_forward_results={},
            stress_results=stress_results,
            pair=display_pair,
            go_no_go=verdicts,
            output_path=report_path,
        )

        logger.info(f"Report saved to {report_path}")

    # Summary
    logger.info(f"\n{'='*60}")
    logger.info(f"T7 GO/NO-GO SUMMARY")
    logger.info(f"{'='*60}")

    total_pass = 0
    total_fail = 0
    for pair, verdicts in all_verdicts.items():
        for name, v in verdicts.items():
            status = 'PASS' if v['pass'] else 'FAIL'
            logger.info(f"  {pair} / {name}: {status}")
            if v['pass']:
                total_pass += 1
            else:
                total_fail += 1

    logger.info(f"\nTotal: {total_pass} passed, {total_fail} failed")

    if total_pass > 0:
        logger.info("PROCEED to T8 (strategy sandbox). Passing strategies can be used.")
    else:
        logger.info("NO STRATEGIES PASSED. Cut patterns, revisit confirmation stack, or reconsider strategy family.")


if __name__ == '__main__':
    main()
