# T7 Handoff: Backtest Harness

**Date:** 2026-08-12
**Built by:** Raven (directly on Aym's machine)
**Task:** T7 - Backtest harness (THE moment of truth)

## What was built

### `backtest/harness.py` - Full backtest engine
- Chronological train/val/test split (60/20/20, no shuffling)
- Strategy execution on historical candle data with confirmation stack
- Stop, target, and signal-exit handling
- PnL computation: gross, net (after fees), R-multiple
- Buy-and-hold benchmark (buy at first candle, hold to last, compare)
- Random-entry twin baseline (seeded, reproducible, same notional/stop/target)
- Walk-forward validation (rolling windows, reports average not best)
- Stress probes: fee doubling, slippage doubling, execution delay (1 candle), parameter jitter (+/-10%)
- Go/no-go checkpoint: PF >= 1.15, beats buy-and-hold, beats random twin by >= 0.15, >= 20 trades
- Per-trade simulation mode for execution-delay stress probe

### `backtest/data_loader.py` - Data loading
- CSV loader (Binance.US historical downloads)
- DB loader (from engine's SQLite)
- Live API fetch (ccxt, 270 days = ~9 months)
- CSV saver (for caching fetched data)

### `backtest/report.py` - Report generation
- Text reports with go/no-go verdicts
- Walk-forward summary tables
- Stress probe comparison tables
- Full report with all sections

## What was NOT built (deferred)
- Pre-registration enforcement (currently manual, not coded as a gate)
- Graveyard logging from backtest failures (will wire when Quant agent is set up at T14)
- Actual historical data download (needs Aym to run or API access)

## Test results

66/66 passed across T4+T5+T6+T7:
- T4 (scanner): 13 tests
- T5 (risk gate): 16 tests
- T6 (paper adapter): 14 tests
- T7 (backtest): 23 tests

## How to run the backtest

```python
from backtest.harness import BacktestHarness
from backtest.data_loader import fetch_historical
from backtest.report import generate_full_report

# Load 9 months of data
candles_15m = fetch_historical('BTC/USDT', '15m', days=270)
candles_1h = fetch_historical('BTC/USDT', '1h', days=270)

# Run backtest
harness = BacktestHarness(config)
results = harness.run_full_backtest(candles_15m, candles_1h, 'BTC/USDT')
verdicts = harness.go_no_go(results)

# Walk-forward
wf = harness.run_walk_forward(candles_15m, candles_1h, 'BTC/USDT')

# Stress probes (per strategy)
stress = {}
for strategy in ENTRY_STRATEGIES:
    stress[strategy.name] = harness.run_stress_probes(
        strategy, candles_15m, candles_1h, 'BTC/USDT'
    )

# Generate report
report = generate_full_report(results, wf, stress, 'BTC/USDT', verdicts,
                              output_path='backtest/reports/btc-report.md')
```

## Next steps

T7 is built but needs real data to run the go/no-go. Two options:
1. Fetch data via ccxt API (live, takes a few minutes per pair)
2. Aym downloads CSVs from Binance.US historical data page

Once data is loaded, run the backtest and see if any patterns pass. If they do, proceed to T8 (strategy sandbox). If they don't, cut patterns and iterate.
