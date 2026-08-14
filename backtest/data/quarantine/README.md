# Quarantined Data Files - DO NOT BACKTEST AGAINST THESE

Files moved here on 2026-08-12 after failing split-gap integrity checks
(see backtest/data/INTEGRITY_REPORT.md and backtest/check_data_integrity.py).

What happened: the Alpaca download script fetched RAW (unadjusted) prices and
overwrote adjusted yfinance files with the same names. Unadjusted stock splits
appear as fake crashes (AMZN -95%, NVDA -90%, TSLA -66%) that corrupt every
pattern, stop, and benchmark computed across them.

Status by group:

- **AMZN/GOOGL/NVDA/TSLA/SNDL/XL* daily+weekly**: REPLACED in backtest/data/
  with fresh split+dividend adjusted yfinance downloads. The raw originals
  remain here as archive only.
- **XLB/XLE/XLK/XLU/XLY 5m/15m/1h, NVAX_1h, MULN intraday**: intraday history
  beyond 60 days is not available from yfinance. Re-download with the FIXED
  Alpaca script (backtest/download_alpaca.py now requests adjustment='all')
  AFTER rotating the Alpaca API key.
- **MULN (all timeframes)**: delisted from Yahoo (which is exactly why it is
  the survivorship canary). Needs a delisted-coverage source (Sharadar/Norgate)
  or the adjusted Alpaca download while its history remains available there.
- **SOXS**: source data is broken even in adjusted daily form (a -94% gap
  survives adjustment). Do not use until a trustworthy source is found.
- **NG_F**: gaps are CONTINUOUS-FUTURES CONTRACT ROLLS, not splits. No split
  adjustment can fix this; futures backtests need back-adjusted contract data.

Weekly bars for LABD/SPXU/TZA/UVXY/WEAT were rebuilt locally from clean daily
data because Yahoo's own weekly bars are inconsistent around split dates.

Convention going forward: ALL price data in backtest/data/ is split- and
dividend-adjusted. The loader (data_loader.py) flags >40% close-to-open gaps
on load, and check_data_integrity.py re-scans the whole directory.
