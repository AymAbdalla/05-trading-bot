# Data Sources: What We Have, What Costs Money, What Needs Aym

**Date:** 2026-08-13
**Purpose:** every dataset the strategy library needs, its source, and its
status. Updated whenever a source is added or a gap closes.

## Acquired, free, no key required

| Dataset | Source | Status | Unblocks |
|---|---|---|---|
| OHLCV equities/ETFs/futures (5m,15m,1h,1d,1wk) | yfinance + Alpaca | 936 clean files, split+dividend adjusted | everything |
| Crypto OHLCV (BTC/ETH/SOL 15m,1h) | Binance kline archives | present, microsecond bug fixed | crypto lane |
| **Session tags** (NYSE + CME) | `exchange_calendars` | **3,018 NYSE + 3,095 CME sessions** with holidays and early closes | 3.1, 3.3, all time-anchored strategies |
| **Split history** | yfinance | **55 tickers with splits since 2015** | 2.4 Ghost Levels |
| **Perp funding rates** | ccxt / **Kraken Futures** | **8,758 hourly points per pair, 1 full year** | 1.1 Funding Shadow |
| Expiration calendar | derived (Fri/Mon/Wed) | computed in-strategy, no download | 4.1, 4.2 |

### Note on the funding venue
Binance global and Bybit both return **HTTP 451 from a US IP** (geo-blocked).
Verified reachable from here: **krakenfutures, deribit, bitget**. The
downloader tries them in that order and records the venue on every row,
because funding differs between venues and attribution matters. Binance.US
lists no perps at all, which is exactly why the strategy uses an offshore
venue as the SIGNAL source while execution stays on Binance.US spot.

Single-call funding history returns ~1,000 hourly points (6 weeks), but
strategy 1.1 needs a 90-day trailing percentile. The downloader paginates
back 400 days.

## Acquired with a key we already have

| Dataset | Source | Status | Unblocks |
|---|---|---|---|
| **Premarket / extended-hours bars** | Alpaca (keys in .env) | downloading, 04:00-09:30 ET 5m bars for 25 core tickers | 2.2 Gap Context Engine |

Premarket bars are stored SEPARATELY (`backtest/data/aux/*_premarket_5m.csv`),
never merged into the regular series: mixing extended hours into the main
bars would silently change every indicator that assumes regular-session data.

## Partial: needs one free signup from Aym

| Dataset | Blocker | Effort | Unblocks |
|---|---|---|---|
| **CPI release dates** | needs a free FRED API key | ~30 seconds at https://fred.stlouisfed.org/docs/api/api_key.html then add `FRED_API_KEY=...` to `.env` | completes 3.2 Release Overshoot Fade |

Current macro calendar has **173 events**: NFP (derived: first Friday of each
month, 08:30 ET) and FOMC (published schedule, stable years ahead). CPI is the
missing third leg. The downloader picks it up automatically once the key
exists: `python3 backtest/download_strategy_data.py calendar`.

## Cannot be obtained free: needs money or a decision

| Dataset | Why blocked | Cost | Blocks |
|---|---|---|---|
| Real options chains (IV surface, OI) | no free historical chain data | CBOE DataShop / ORATS / Polygon, roughly $100-600/mo | true options backtests; the current overlay uses synthetic Black-Scholes with NO IV smile and is optimistic by construction |
| Survivorship-complete equity history | delisted names are not on free feeds | CRSP / Norgate / Sharadar, roughly $50-500/mo | removing survivorship bias; MULN/SNDL are the only delisted names present and only as canaries |
| Halt tape with reason codes | not in free feeds | UTP/CTA or Polygon | 2.7 Halt Resumption Drift |
| Intraday quote data with spreads | not in free feeds | paid | realistic spread modelling (currently a flat assumption) |

## Structural gaps, no purchase needed, just work

| Gap | Note |
|---|---|
| 4h timeframe | not downloaded for any instrument; resample from 1h or download directly |
| Futures settlement prices | 3.1 needs prior settlement. CME session tags now exist, so settlement can be approximated as the RTH close. That is an approximation and should be labelled as one in the strategy docstring |
| Surprise-ticker scanner | v2 component, unbuilt; 2.7 depends on it |

## What needs Aym, in priority order

1. **FRED API key** (free, 30 seconds). Add `FRED_API_KEY=...` to `.env`.
   Completes the macro calendar. Everything else about 3.2 is built.
2. **Decision on paid data.** Nothing is blocked TODAY that would change the
   current verdict, but real options chains are the difference between the
   options overlay being a fee-structure model and being tradeable evidence.
   Not urgent while v0 shows zero edge.
3. Nothing else. Every other source is acquired or derivable.
