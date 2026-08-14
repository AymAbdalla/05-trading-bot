"""Full universe graveyard builder v2.

Tests all strategies (v0 + strategy lab) across all 157 tickers x 5 timeframes x 9 exit configs.
Uses vectorized harness for speed.
"""
import os, sys, json, time, logging, csv, glob
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.vectorized_harness import VectorizedBacktestHarness, EXIT_CONFIGS
from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
from strategies.builtin.strategy_lab import STRATEGY_LAB_STRATEGIES
from backtest.data_loader import load_csv

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'data')
GRAVEYARD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'research', 'graveyard')
GRAVEYARD_FILE = os.path.join(GRAVEYARD_DIR, 'v0_graveyard_full.json')

# Combine all entry strategies
ALL_STRATEGIES = ENTRY_STRATEGIES_EXPANDED + STRATEGY_LAB_STRATEGIES

# Binance crypto (monthly CSVs)
BINANCE_PAIRS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
BINANCE_TIMEFRAMES = ['15m', '1h']

ALL_TIMEFRAMES = ['1d', '1wk', '1h', '5m', '15m']


def load_binance_merged(pair, tf):
    pattern = os.path.join(DATA_DIR, f"{pair}-{tf}-*.csv")
    files = sorted(glob.glob(pattern))
    candles = []
    for f in files:
        with open(f, 'r') as fh:
            reader = csv.reader(fh)
            next(reader, None)
            for row in reader:
                if len(row) < 6: continue
                try:
                    candles.append({
                        'ts': int(float(row[0])),
                        'open': float(row[1]),
                        'high': float(row[2]),
                        'low': float(row[3]),
                        'close': float(row[4]),
                        'volume': float(row[5]),
                    })
                except (ValueError, IndexError): continue
    candles.sort(key=lambda c: c['ts'])
    seen = set(); deduped = []
    for c in candles:
        if c['ts'] not in seen:
            seen.add(c['ts']); deduped.append(c)
    return deduped


def discover_yf_tickers():
    """Discover all tickers from CSV filenames."""
    tickers = set()
    for f in os.listdir(DATA_DIR):
        if f.endswith('.csv') and '_' in f:
            # Extract ticker (everything before last _tf.csv)
            base = f.rsplit('_', 1)[0]
            tickers.add(base)
    return sorted(tickers)


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
    
    # Load ticker universe for sector tagging
    universe_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'ticker_universe.json')
    with open(universe_path) as f:
        universe = json.load(f)
    
    # Build ticker -> sector mapping
    ticker_sector = {}
    for sector, tickers in universe.items():
        for t in tickers:
            safe = t.replace('^', '').replace('=', '_').replace('-', '_')
            ticker_sector[safe] = sector

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
                test_sets.append((display, tf, candles, 'Crypto'))

    # yfinance/Alpaca tickers
    yf_tickers = discover_yf_tickers()
    for ticker in yf_tickers:
        sector = ticker_sector.get(ticker, 'Unknown')
        for tf in ALL_TIMEFRAMES:
            filepath = os.path.join(DATA_DIR, f"{ticker}_{tf}.csv")
            candles = load_csv(filepath)
            if len(candles) > 200:
                test_sets.append((ticker, tf, candles, sector))

    exit_configs = list(EXIT_CONFIGS.keys())
    n_strategies = len(ALL_STRATEGIES)
    n_exits = len(exit_configs)

    logger.info(f"Strategies: {n_strategies} ({len(ENTRY_STRATEGIES_EXPANDED)} v0 + {len(STRATEGY_LAB_STRATEGIES)} strategy lab)")
    logger.info(f"Exit configs: {n_exits}")
    logger.info(f"Test sets: {len(test_sets)}")
    logger.info(f"Total backtests: {len(test_sets) * n_strategies * n_exits}")

    start_time = time.time()

    for ticker, tf, candles, sector in test_sets:
        n = len(candles)
        test_candles = candles[int(n * 0.8):]
        if len(test_candles) < 100:
            continue

        logger.info(f"Testing {ticker} {tf} [{sector}] ({len(test_candles)} candles)...")

        try:
            reports = harness.run_sweep(
                test_candles, ticker, tf,
                strategies=ALL_STRATEGIES,
                exit_configs=exit_configs,
            )
        except Exception as e:
            logger.error(f"  ERROR: {e}")
            continue

        for r in reports:
            total_tests += 1
            r['sector'] = sector
            # Convert numpy types
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
        output = {
            'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
            'total_tests': total_tests,
            'passed': passed,
            'failed': failed,
            'inversions_flagged': inversions,
            'entries': all_reports,
        }
        with open(GRAVEYARD_FILE, 'w') as f:
            json.dump(output, f, indent=2, default=str)

    elapsed = time.time() - start_time

    logger.info(f"\n{'='*60}")
    logger.info(f"GRAVEYARD COMPLETE (FULL UNIVERSE)")
    logger.info(f"{'='*60}")
    logger.info(f"Total tests: {total_tests}")
    logger.info(f"Passed: {passed}")
    logger.info(f"Failed: {failed}")
    logger.info(f"Inversions flagged: {inversions}")
    logger.info(f"Time: {elapsed:.1f}s ({total_tests/max(elapsed,1):.1f} tests/sec)")
    logger.info(f"Saved to: {GRAVEYARD_FILE}")

    if passed > 0:
        logger.info(f"\nPASSING STRATEGIES:")
        for r in all_reports:
            if r.get('verdict') == 'PASS':
                logger.info(f"  {r['strategy']} + {r['exit_config']} on {r['ticker']} {r['timeframe']} [{r.get('sector','?')}]: "
                           f"PF={r['pf']:.2f}, {r['trades']} trades, ret={r['return_pct']:.2f}%")


if __name__ == '__main__':
    main()
