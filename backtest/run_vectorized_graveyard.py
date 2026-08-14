"""Full vectorized graveyard builder.

Runs all 30 entry strategies x 9 exit configs across all tickers and timeframes.
Writes results to research/graveyard/v0_graveyard.json.

Uses the vectorized harness for 100x speedup over the old per-candle version.
"""
import os, sys, glob, json, time, logging, csv
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.vectorized_harness import VectorizedBacktestHarness, EXIT_CONFIGS
from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
from backtest.data_loader import load_csv

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'data')
GRAVEYARD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'research', 'graveyard')
GRAVEYARD_FILE = os.path.join(GRAVEYARD_DIR, 'v0_graveyard.json')

# yfinance/Alpaca tickers (single CSV per ticker+tf)
YFINANCE_TICKERS = [
    'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA',
    'JPM', 'XOM', 'JNJ', 'HD', 'DIS', 'BA', 'CAT',
    'RBLX', 'DUOL', 'SOFI', 'PLTR', 'SNDL', 'MULN',
    'XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'XLP', 'XLB', 'XLRE', 'XLU',
    'SPY', 'QQQ', 'IWM', 'DIA',
]

# Futures (yfinance format)
FUTURES_TICKERS = ['ES_F', 'NQ_F', 'CL_F', 'GC_F']

# Crypto on yfinance
CRYPTO_YF = ['BTC_USD', 'ETH_USD']

# Binance crypto (monthly CSVs)
BINANCE_PAIRS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
BINANCE_TIMEFRAMES = ['15m', '1h']

# All timeframes for yfinance/Alpaca tickers
ALL_TIMEFRAMES = ['1d', '1wk', '1h', '5m', '15m']


def load_binance_merged(pair, tf):
    """Load and merge all monthly Binance CSVs."""
    pattern = os.path.join(DATA_DIR, f"{pair}-{tf}-*.csv")
    files = sorted(glob.glob(pattern))
    candles = []
    for f in files:
        with open(f, 'r') as fh:
            reader = csv.reader(fh)
            next(reader, None)  # skip header if present
            for row in reader:
                if len(row) < 6:
                    continue
                try:
                    candles.append({
                        'ts': int(float(row[0])),
                        'open': float(row[1]),
                        'high': float(row[2]),
                        'low': float(row[3]),
                        'close': float(row[4]),
                        'volume': float(row[5]),
                    })
                except (ValueError, IndexError):
                    continue
    candles.sort(key=lambda c: c['ts'])
    seen = set(); deduped = []
    for c in candles:
        if c['ts'] not in seen:
            seen.add(c['ts']); deduped.append(c)
    return deduped


def main():
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    with open(config_path) as f:
        config = yaml.safe_load(f)
    conf = config['strategy']['confirmation']
    conf['volume_min_ratio'] = 1.2
    conf['location_atr_mult'] = 5.0
    conf['rsi_max_entry'] = 70

    harness = VectorizedBacktestHarness(config)

    all_reports = []
    total_tests = 0
    passed = 0
    failed = 0
    inversions = 0

    # Build test sets
    test_sets = []

    # Binance crypto
    for pair in BINANCE_PAIRS:
        display = f"{pair[:3]}/USDT"
        for tf in BINANCE_TIMEFRAMES:
            candles = load_binance_merged(pair, tf)
            if len(candles) > 200:
                test_sets.append((f"{pair}_{tf}", display, tf, candles))

    # yfinance + Alpaca + futures + crypto
    for ticker in YFINANCE_TICKERS + FUTURES_TICKERS + CRYPTO_YF:
        for tf in ALL_TIMEFRAMES:
            filepath = os.path.join(DATA_DIR, f"{ticker}_{tf}.csv")
            candles = load_csv(filepath)
            if len(candles) > 200:
                display = ticker.replace('_', '=').replace('_', '-')
                test_sets.append((f"{ticker}_{tf}", ticker, tf, candles))

    exit_configs = list(EXIT_CONFIGS.keys())
    n_strategies = len(ENTRY_STRATEGIES_EXPANDED)
    n_exits = len(exit_configs)

    logger.info(f"Test sets: {len(test_sets)}")
    logger.info(f"Strategies: {n_strategies}")
    logger.info(f"Exit configs: {n_exits}")
    logger.info(f"Total backtests: {len(test_sets) * n_strategies * n_exits}")

    start_time = time.time()

    for test_id, ticker, tf, candles in test_sets:
        # Use last 20% as test set
        n = len(candles)
        test_candles = candles[int(n * 0.8):]
        if len(test_candles) < 100:
            continue

        logger.info(f"Testing {ticker} {tf} ({len(test_candles)} candles)...")

        try:
            reports = harness.run_sweep(
                test_candles, ticker, tf,
                strategies=ENTRY_STRATEGIES_EXPANDED,
                exit_configs=exit_configs,
            )
        except Exception as e:
            logger.error(f"  ERROR on {ticker} {tf}: {e}")
            continue

        for r in reports:
            total_tests += 1
            # Convert numpy types to Python natives for JSON
            for k, v in r.items():
                if isinstance(v, (np.floating, np.integer)):
                    r[k] = float(v)
                elif isinstance(v, (np.bool_)):
                    r[k] = bool(v)

            if r.get('verdict') == 'PASS':
                passed += 1
                logger.info(f"  PASS: {r['strategy']} + {r['exit_config']} on {ticker} {tf} "
                           f"(PF={r['pf']:.2f}, {r['trades']} trades)")
            else:
                failed += 1
                if r.get('inversion_flagged'):
                    inversions += 1

            all_reports.append(r)

        # Save periodically
        os.makedirs(GRAVEYARD_DIR, exist_ok=True)
        VectorizedBacktestHarness.write_graveyard(all_reports, GRAVEYARD_FILE)

    elapsed = time.time() - start_time

    # Final summary
    logger.info(f"\n{'='*60}")
    logger.info(f"GRAVEYARD COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Total tests: {total_tests}")
    logger.info(f"Passed: {passed}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Inversions flagged: {inversions}")
    logger.info(f"Time: {elapsed:.1f}s ({total_tests/elapsed:.1f} tests/sec)")
    logger.info(f"Saved to: {GRAVEYARD_FILE}")

    if passed > 0:
        logger.info(f"\nPASSING STRATEGIES:")
        for r in all_reports:
            if r.get('verdict') == 'PASS':
                logger.info(f"  {r['strategy']} + {r['exit_config']} on {r['ticker']} {r['timeframe']}: "
                           f"PF={r['pf']:.2f}, {r['trades']} trades, ret={r['return_pct']:.2f}%")


if __name__ == '__main__':
    main()
