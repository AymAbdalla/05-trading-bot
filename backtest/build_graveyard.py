"""Multi-asset graveyard builder.

Tests all 30 entry strategies across all tickers and timeframes.
Writes results to a single graveyard JSON file that Quant/Forge will read.

Optimized for speed: tests on last 20% of data (test holdout), no stress probes.
Runs long strategies only (short strategies added in V3+ with futures data).
"""
import os, sys, glob, json, time, logging, yaml
import csv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.harness import BacktestHarness
from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'data')
GRAVEYARD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'research', 'graveyard')
# Event-harness graveyard. MUST NOT share a filename with the vectorized
# pipeline's v0_graveyard.json - the two harnesses have different semantics
# and silently overwriting each other's results was an audited bug.
GRAVEYARD_FILE = os.path.join(GRAVEYARD_DIR, 'v0_graveyard_event.json')

# All yfinance tickers (single CSV per ticker+tf, not monthly splits)
YFINANCE_TICKERS = [
    'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA',
    'JPM', 'XOM', 'JNJ', 'HD',
    'RBLX', 'DUOL', 'SOFI',
    'XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'SPY', 'QQQ',
    'ES_F', 'NQ_F', 'CL_F', 'GC_F',
    'BTC_USD', 'ETH_USD',
]

TIMEFRAMES = ['1d', '1wk', '1h', '5m', '15m']

# Binance crypto (monthly CSVs that need merging)
BINANCE_PAIRS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
BINANCE_TIMEFRAMES = ['15m', '1h']


def load_yfinance_csv(filepath):
    """Load a yfinance CSV file."""
    candles = []
    if not os.path.exists(filepath):
        return candles
    with open(filepath, 'r') as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                candles.append({
                    'ts': int(row['ts']),
                    'open': float(row['open']),
                    'high': float(row['high']),
                    'low': float(row['low']),
                    'close': float(row['close']),
                    'volume': float(row['volume']),
                })
            except (ValueError, KeyError):
                continue
    return candles


def load_binance_merged(pair, tf):
    """Load and merge all monthly Binance CSVs for a pair+timeframe."""
    pattern = os.path.join(DATA_DIR, f"{pair}-{tf}-*.csv")
    files = sorted(glob.glob(pattern))
    candles = []
    for f in files:
        with open(f, 'r') as fh:
            reader = csv.reader(fh)
            # Headerless files: keep row 1 if numeric (NEW-2).
            first = next(reader, None)
            rows = ([first] if first and first[0].replace('.', '').isdigit() else [])
            for row in rows + list(reader):
                if len(row) < 6:
                    continue
                try:
                    ts = int(float(row[0]))
                    if ts >= 1e14:  # microsecond era (2025+) -> ms (NEW-1)
                        ts //= 1000
                    candles.append({
                        'ts': ts,
                        'open': float(row[1]),
                        'high': float(row[2]),
                        'low': float(row[3]),
                        'close': float(row[4]),
                        'volume': float(row[5]),
                    })
                except (ValueError, IndexError):
                    continue
    candles.sort(key=lambda c: c['ts'])
    # Dedup
    seen = set(); deduped = []
    for c in candles:
        if c['ts'] not in seen:
            seen.add(c['ts']); deduped.append(c)
    return deduped


def main():
    # Load config with loosened confirmation
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    with open(config_path) as f:
        config = yaml.safe_load(f)
    conf = config['strategy']['confirmation']
    conf['volume_min_ratio'] = 1.2
    conf['location_atr_mult'] = 5.0  # effectively disabled
    conf['rsi_max_entry'] = 70

    harness = BacktestHarness(config)

    graveyard = {
        'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
        'total_tests': 0,
        'passed': 0,
        'failed': 0,
        'inversions_flagged': 0,
        'entries': [],
    }

    # Build list of all (ticker, display_name, timeframe, candles) to test
    test_sets = []

    # Binance crypto
    for pair in BINANCE_PAIRS:
        display = f"{pair[:3]}/USDT"
        for tf in BINANCE_TIMEFRAMES:
            candles = load_binance_merged(pair, tf)
            if len(candles) > 200:
                test_sets.append((f"{pair}_{tf}", display, tf, candles))

    # yfinance tickers
    for ticker in YFINANCE_TICKERS:
        display = ticker.replace('_', '=').replace('_', '-')
        for tf in TIMEFRAMES:
            filepath = os.path.join(DATA_DIR, f"{ticker}_{tf}.csv")
            candles = load_yfinance_csv(filepath)
            if len(candles) > 200:
                test_sets.append((f"{ticker}_{tf}", ticker.replace('_', '='), tf, candles))

    logger.info(f"Total test sets: {len(test_sets)}")
    logger.info(f"Total strategies: {len(ENTRY_STRATEGIES_EXPANDED)}")
    logger.info(f"Total backtests to run: {len(test_sets) * len(ENTRY_STRATEGIES_EXPANDED)}")

    for test_id, display_name, tf, candles in test_sets:
        # Use last 20% as test set
        n = len(candles)
        test_candles = candles[int(n * 0.8):]

        if len(test_candles) < 100:
            continue

        # Regime candles: full series is safe here - the harness aligns them
        # by TIMESTAMP per bar, so only candles closed before each test bar
        # are ever used. (Same-timeframe regime is still a simplification vs
        # the SPEC's 1h regime series; noted in the graveyard metadata.)
        regime_candles = candles

        logger.info(f"\nTesting {display_name} {tf} ({len(test_candles)} test candles)...")

        for strategy in ENTRY_STRATEGIES_EXPANDED:
            graveyard['total_tests'] += 1
            try:
                result = harness.run_strategy_on_candles(
                    strategy, test_candles, regime_candles, display_name
                )
                r = result.to_report()
                pf = result.profit_factor
                # Infinite PF (zero losing trades) is a red flag, never a pass.
                passed = (pf != float('inf') and pf >= 1.15 and
                         r['beats_buy_hold'] and
                         r['beats_random_twin'] and
                         r['trade_count'] >= 20)

                entry = {
                    'test_id': test_id,
                    'ticker': display_name,
                    'timeframe': tf,
                    'strategy': strategy.name,
                    'trades': r['trade_count'],
                    'pf': r['profit_factor'],           # None means infinite, not missing
                    'gross_pf': r['gross_pf'],
                    'win_rate': round(r['win_rate'], 4),
                    'return_pct': round(r['strategy_return_pct'], 2),
                    'total_pnl_usd': r['total_pnl'],
                    'buy_hold_pct': round(r['buy_hold_return_pct'], 2),
                    'buy_hold_pnl_usd': r['buy_hold_pnl_usd'],
                    'random_twin_pf': r['random_twin_pf'],
                    'beats_buy_hold': r['beats_buy_hold'],
                    'beats_random_twin': r['beats_random_twin'],
                    'verdict': 'PASS' if passed else 'FAIL',
                    'inversion_flagged': False,
                }

                if passed:
                    graveyard['passed'] += 1
                    logger.info(f"  PASS: {strategy.name} on {display_name} {tf} "
                               f"(PF={pf:.2f}, {r['trade_count']} trades)")
                else:
                    graveyard['failed'] += 1
                    # Flag inversion for PF < 0.5 with enough trades
                    if pf != float('inf') and pf < 0.5 and r['trade_count'] >= 10:
                        entry['inversion_flagged'] = True
                        entry['inversion_note'] = (
                            f"PF={pf:.2f} on {r['trade_count']} trades. "
                            f"Pattern may be anti-correlated. Test as exit signal (fade). "
                            f"NOT YET TESTED: inversion is a flag only, no inverted variant was run."
                        )
                        graveyard['inversions_flagged'] += 1

                graveyard['entries'].append(entry)

            except Exception as e:
                logger.error(f"  ERROR: {strategy.name} on {display_name} {tf}: {e}")
                graveyard['entries'].append({
                    'test_id': test_id, 'ticker': display_name, 'timeframe': tf,
                    'strategy': strategy.name, 'verdict': 'ERROR', 'error': str(e),
                })
                graveyard['failed'] += 1

        # Save graveyard periodically (every test set)
        os.makedirs(GRAVEYARD_DIR, exist_ok=True)
        with open(GRAVEYARD_FILE, 'w') as f:
            json.dump(graveyard, f, indent=2)

    # Final summary
    logger.info(f"\n{'='*60}")
    logger.info(f"GRAVEYARD COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"Total tests: {graveyard['total_tests']}")
    logger.info(f"Passed: {graveyard['passed']}")
    logger.info(f"Failed: {graveyard['failed']}")
    logger.info(f"Inversions flagged: {graveyard['inversions_flagged']}")
    logger.info(f"Saved to: {GRAVEYARD_FILE}")

    # Show passing strategies
    if graveyard['passed'] > 0:
        logger.info(f"\nPASSING STRATEGIES:")
        for e in graveyard['entries']:
            if e['verdict'] == 'PASS':
                logger.info(f"  {e['strategy']} on {e['ticker']} {e['timeframe']}: "
                           f"PF={e['pf']}, {e['trades']} trades, ret={e['return_pct']}%")
    else:
        logger.info(f"\nNo strategies passed. All go to graveyard for Forge's diagnosis.")


if __name__ == '__main__':
    main()
