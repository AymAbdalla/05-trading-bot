"""Incremental graveyard builder.

Only tests NEW tickers/strategies not already in the graveyard JSON.
Merges results with existing graveyard. Adding tickers or strategies
never re-runs old combinations.

Usage: python backtest/run_incremental_graveyard.py
  - Detects what's already in the graveyard
  - Only runs new (ticker, timeframe, strategy) combinations
  - Merges into the existing graveyard file
"""
import os, sys, json, time, logging, csv, glob
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.vectorized_harness import VectorizedBacktestHarness, EXIT_CONFIGS, GATE_VERSION
from backtest.cost_model import COST_MODEL_VERSION
from backtest.instruments import resolve_asset_class
from strategies.builtin.expanded import ENTRY_STRATEGIES_EXPANDED
from strategies.builtin.strategy_lab import STRATEGY_LAB_STRATEGIES
from strategies.builtin.strategy_lab_v2 import STRATEGY_LAB_V2_STRATEGIES
from strategies.builtin.strategy_lab_v3 import STRATEGY_LAB_V3_STRATEGIES
from strategies.builtin.strategy_lab_v4 import STRATEGY_LAB_V4_STRATEGIES
from strategies.builtin.strategy_lab_v5 import STRATEGY_LAB_V5_STRATEGIES
from backtest.data_loader import load_csv

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'data')
GRAVEYARD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'research', 'graveyard')
GRAVEYARD_FILE = os.path.join(GRAVEYARD_DIR, 'v0_graveyard_full.json')

ALL_STRATEGIES = (ENTRY_STRATEGIES_EXPANDED + STRATEGY_LAB_STRATEGIES
                  + STRATEGY_LAB_V2_STRATEGIES + STRATEGY_LAB_V3_STRATEGIES
                  + STRATEGY_LAB_V4_STRATEGIES + STRATEGY_LAB_V5_STRATEGIES)

BINANCE_PAIRS = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
BINANCE_TIMEFRAMES = ['15m', '1h']
ALL_TIMEFRAMES = ['1d', '1wk', '1h', '5m', '15m']

# Tickers that aren't real OHLCV data files (metadata files, etc.)
SKIP_FILES = {
    'funding_okx', 'funding_gate', 'deribit_hvol', 'stablecoin', 'btc_marketcap',
    'btc_price', 'eth_marketcap', 'eth_price', 'sol_marketcap', 'crypto_global',
    'crypto_dominance', 'google_trends', 'reddit_wsb', 'finra_shortvol', 'VIX',
    'VX_F',
}


def load_binance_merged(pair, tf):
    pattern = os.path.join(DATA_DIR, f"{pair}-{tf}-*.csv")
    files = sorted(glob.glob(pattern))
    candles = []
    for f in files:
        with open(f, 'r') as fh:
            reader = csv.reader(fh)
            # Binance kline files are HEADERLESS: peek at row 1 and keep it
            # if numeric (skipping it unconditionally dropped the first
            # candle of every monthly file - re-audit NEW-2).
            first = next(reader, None)
            rows = ([first] if first and first[0].replace('.', '').isdigit() else [])
            for row in rows + list(reader):
                if len(row) < 6: continue
                try:
                    ts = int(float(row[0]))
                    # Binance switched to MICROSECOND stamps at 2025-01-01;
                    # mixed units corrupt interval inference and re-open the
                    # regime-lookahead hole (re-audit NEW-1). Normalize to ms.
                    if ts >= 1e14:
                        ts //= 1000
                    candles.append({
                        'ts': ts,
                        'open': float(row[1]), 'high': float(row[2]),
                        'low': float(row[3]), 'close': float(row[4]),
                        'volume': float(row[5]),
                    })
                except (ValueError, IndexError): continue
    candles.sort(key=lambda c: c['ts'])
    seen = set(); deduped = []
    for c in candles:
        if c['ts'] not in seen: seen.add(c['ts']); deduped.append(c)
    return deduped


def discover_yf_tickers():
    """Discover all tickers from CSV filenames, excluding metadata files."""
    tickers = set()
    for f in os.listdir(DATA_DIR):
        if not f.endswith('.csv') or '_' not in f:
            continue
        base = f.rsplit('_', 1)[0]
        # Skip metadata files
        if any(base.startswith(skip) or base == skip for skip in SKIP_FILES):
            continue
        tickers.add(base)
    return sorted(tickers)


def load_existing_graveyard():
    """Load existing graveyard entries. Returns set of (ticker, timeframe, strategy, exit_config) already tested."""
    if not os.path.exists(GRAVEYARD_FILE):
        logger.info("No existing graveyard found. Starting fresh.")
        return [], set()

    with open(GRAVEYARD_FILE) as f:
        data = json.load(f)

    entries = data.get('entries', [])
    existing_keys = set()
    for e in entries:
        key = (e.get('ticker'), e.get('timeframe'), e.get('strategy'), e.get('exit_config'))
        existing_keys.add(key)

    logger.info(f"Existing graveyard: {len(entries)} entries, {len(existing_keys)} unique combinations")
    return entries, existing_keys


def main():
    import yaml
    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.yaml')
    with open(config_path) as f:
        config = yaml.safe_load(f)
    conf = config['strategy']['confirmation']
    conf['volume_min_ratio'] = 1.2
    conf['location_atr_mult'] = 5.0
    conf['rsi_max_entry'] = 70

    # Venue-accurate costs (backtest/cost_model.py), not the flat crypto
    # taker rate. Every entry is stamped with cost_model_version; the
    # assertions reject pooling entries with mismatched stamps, so this run
    # can never be merged with the pre-cost-model graveyard by accident.
    config['use_cost_model'] = True

    harness = VectorizedBacktestHarness(config)

    # Load ticker universe for sector tagging
    universe_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'ticker_universe.json')
    with open(universe_path) as f:
        universe = json.load(f)
    ticker_sector = {}
    for sector, tickers in universe.items():
        for t in tickers:
            safe = t.replace('^', '').replace('=', '_').replace('-', '_')
            ticker_sector[safe] = sector

    # Load existing graveyard
    existing_entries, existing_keys = load_existing_graveyard()

    # Build all possible test sets
    all_test_sets = []

    # Binance crypto
    for pair in BINANCE_PAIRS:
        display = f"{pair[:3]}/USDT"
        for tf in BINANCE_TIMEFRAMES:
            candles = load_binance_merged(pair, tf)
            if len(candles) > 200:
                all_test_sets.append((display, tf, candles, 'Crypto'))

    # yfinance/Alpaca tickers
    yf_tickers = discover_yf_tickers()
    for ticker in yf_tickers:
        sector = ticker_sector.get(ticker, 'Unknown')
        for tf in ALL_TIMEFRAMES:
            filepath = os.path.join(DATA_DIR, f"{ticker}_{tf}.csv")
            candles = load_csv(filepath)
            if len(candles) > 200:
                all_test_sets.append((ticker, tf, candles, sector))

    exit_configs = list(EXIT_CONFIGS.keys())

    # Determine what needs to run
    needed_test_sets = []
    skipped = 0
    for ticker, tf, candles, sector in all_test_sets:
        has_new = False
        for strategy in ALL_STRATEGIES:
            for exit_config in exit_configs:
                key = (ticker, tf, strategy.name, exit_config)
                if key not in existing_keys:
                    has_new = True
                    break
            if has_new:
                break
        if has_new:
            needed_test_sets.append((ticker, tf, candles, sector))
        else:
            skipped += 1

    # Count actual new combinations
    total_new = 0
    for ticker, tf, _, _ in needed_test_sets:
        for strategy in ALL_STRATEGIES:
            for exit_config in exit_configs:
                key = (ticker, tf, strategy.name, exit_config)
                if key not in existing_keys:
                    total_new += 1

    logger.info(f"Strategies: {len(ALL_STRATEGIES)} "
                f"({len(ENTRY_STRATEGIES_EXPANDED)} v0 + {len(STRATEGY_LAB_STRATEGIES)} lab v1 "
                f"+ {len(STRATEGY_LAB_V2_STRATEGIES)} lab v2 "
                f"+ {len(STRATEGY_LAB_V3_STRATEGIES)} lab v3 "
                f"+ {len(STRATEGY_LAB_V4_STRATEGIES)} lab v4 "
                f"+ {len(STRATEGY_LAB_V5_STRATEGIES)} lab v5)")
    logger.info(f"Exit configs: {len(exit_configs)}")
    logger.info(f"Total test sets: {len(all_test_sets)}")
    logger.info(f"Already tested (skip): {skipped} test sets")
    logger.info(f"Need to test: {len(needed_test_sets)} test sets")
    logger.info(f"New backtests to run: {total_new}")
    logger.info(f"Graveyard will have: {len(existing_entries) + total_new} total entries after completion")

    if total_new == 0:
        logger.info("Nothing new to test. Graveyard is up to date.")
        return

    all_reports = list(existing_entries)  # Start with existing
    total_tests = 0
    passed = 0
    failed = 0
    inversions = 0

    start_time = time.time()

    for ticker, tf, candles, sector in needed_test_sets:
        n = len(candles)
        test_candles = candles[int(n * 0.8):]
        if len(test_candles) < 100:
            # VISIBLE skip, not a silent `continue` (D-223): a bare continue
            # here hid every weekly series for the life of the project. An
            # untestable series must leave a record saying so.
            logger.warning(f'SKIP {ticker} {tf}: test slice {len(test_candles)} bars '
                           f'< 100 minimum (series has {n} bars)')
            for s in ALL_STRATEGIES:
                for exit_config in exit_configs:
                    key = (ticker, tf, s.name, exit_config)
                    if key in existing_keys:
                        continue
                    existing_keys.add(key)
                    all_reports.append({
                        'ticker': ticker, 'timeframe': tf, 'strategy': s.name,
                        'exit_config': exit_config, 'sector': sector,
                        'trades': 0, 'verdict': 'NOT_TESTED',
                        'not_tested_reason': (f'test slice {len(test_candles)} bars '
                                              f'< 100 minimum'),
                        'gate_version': GATE_VERSION, 'inversion_flagged': False,
                        'cost_model_version': COST_MODEL_VERSION,
                        'asset_class': resolve_asset_class(ticker, sector),
                    })
            continue

        # Determine which strategies are new for this ticker+tf
        new_strategies = []
        for s in ALL_STRATEGIES:
            # Check if ANY exit config for this strategy is missing
            for exit_config in exit_configs:
                key = (ticker, tf, s.name, exit_config)
                if key not in existing_keys:
                    new_strategies.append(s)
                    break

        if not new_strategies:
            continue

        logger.info(f"Testing {ticker} {tf} [{sector}] ({len(test_candles)} candles, {len(new_strategies)} new strategies)...")

        try:
            reports = harness.run_sweep(
                test_candles, ticker, tf,
                strategies=new_strategies,
                exit_configs=exit_configs,
                sector=sector,   # resolves the cost regime (CRYPTO/EQUITY/ETF/FUTURES)
            )
        except Exception as e:
            logger.error(f"  ERROR: {e}")
            continue

        for r in reports:
            # Only add if not already in graveyard
            key = (r.get('ticker'), r.get('timeframe'), r.get('strategy'), r.get('exit_config'))
            if key in existing_keys:
                continue
            existing_keys.add(key)

            total_tests += 1
            r['sector'] = sector
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

        # Save after each ticker
        os.makedirs(GRAVEYARD_DIR, exist_ok=True)
        total_passed = sum(1 for e in all_reports if e.get('verdict') == 'PASS')
        total_failed = sum(1 for e in all_reports if e.get('verdict') != 'PASS')
        total_inv = sum(1 for e in all_reports if e.get('inversion_flagged'))

        output = {
            'generated': time.strftime('%Y-%m-%d %H:%M:%S'),
            'total_tests': len(all_reports),
            'passed': total_passed,
            'failed': total_failed,
            'inversions_flagged': total_inv,
            'strategies_tested': len(ALL_STRATEGIES),
            'exit_configs_tested': len(exit_configs),
            'entries': all_reports,
        }
        with open(GRAVEYARD_FILE, 'w') as f:
            json.dump(output, f, indent=2, default=str)

    elapsed = time.time() - start_time

    logger.info(f"\n{'='*60}")
    logger.info(f"INCREMENTAL GRAVEYARD UPDATE COMPLETE")
    logger.info(f"{'='*60}")
    logger.info(f"New tests run: {total_tests}")
    logger.info(f"New passes: {passed}")
    logger.info(f"New fails: {failed}")
    logger.info(f"New inversions: {inversions}")
    logger.info(f"Time: {elapsed:.1f}s")
    logger.info(f"Total graveyard entries: {len(all_reports)}")
    logger.info(f"Saved to: {GRAVEYARD_FILE}")

    if passed > 0:
        logger.info(f"\nNEW PASSING STRATEGIES:")
        for r in all_reports[-total_tests:]:
            if r.get('verdict') == 'PASS':
                logger.info(f"  {r['strategy']} + {r['exit_config']} on {r['ticker']} {r['timeframe']} [{r.get('sector','?')}]: "
                           f"PF={r['pf']:.2f}, {r['trades']} trades, ret={r['return_pct']:.2f}%")


if __name__ == '__main__':
    main()
