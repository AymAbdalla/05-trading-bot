"""Acquire the auxiliary data strategy-lab-v2 needs, using free sources only.

What each dataset unblocks:
  premarket bars   -> 2.2 Gap Context Engine (premarket volume percentile)
  split history    -> 2.4 Ghost Levels (pre-split round-number coordinates)
  session tags     -> 3.1 Overnight Inventory Flush, 3.3 Globex VWAP,
                      and correct RTH/Globex separation everywhere
  macro calendar   -> 3.2 Release Overshoot Fade
  funding rates    -> 1.1 Funding Shadow

Sources, all free, no paid feeds:
  Alpaca (keys in .env)      premarket/extended-hours bars
  yfinance                   split history
  exchange_calendars         NYSE + CME sessions, holidays, early closes
  ccxt (public endpoints)    perp funding rates, no API key needed
  derived/static             NFP (first Friday) and FOMC (published years out)

Usage:
  python3 backtest/download_strategy_data.py premarket [N]
  python3 backtest/download_strategy_data.py splits
  python3 backtest/download_strategy_data.py sessions
  python3 backtest/download_strategy_data.py calendar
  python3 backtest/download_strategy_data.py funding
"""
import csv
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'backtest', 'data')
AUX_DIR = os.path.join(DATA_DIR, 'aux')
load_dotenv(os.path.join(ROOT, '.env'))

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

# Liquid, optionable names most of the v2 equity strategies target.
CORE = ['AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA', 'SPY', 'QQQ',
        'JPM', 'XOM', 'AMD', 'ADBE', 'CAT', 'DIS', 'BA', 'ORCL', 'WMT',
        'IWM', 'DIA', 'NFLX', 'INTC', 'CSCO', 'PFE', 'KO']


def _ensure_aux():
    os.makedirs(AUX_DIR, exist_ok=True)


def premarket(limit: int = None):
    """Extended-hours 5m bars (04:00-09:30 ET) via Alpaca.

    Written SEPARATELY from the regular bars, never merged: mixing extended
    hours into the main series would silently change every indicator that
    assumes regular-session bars.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    key, secret = os.environ.get('ALPACA_API_KEY'), os.environ.get('ALPACA_API_SECRET')
    if not key or not secret:
        logger.error('ALPACA keys missing from .env')
        return
    client = StockHistoricalDataClient(key, secret)
    _ensure_aux()

    end = datetime.now() - timedelta(minutes=20)   # SIP delay window
    start = end - timedelta(days=700)
    tickers = CORE[:limit] if limit else CORE
    ok = 0
    for ticker in tickers:
        try:
            req = StockBarsRequest(symbol_or_symbols=ticker,
                                   timeframe=TimeFrame(5, TimeFrameUnit.Minute),
                                   start=start, end=end, limit=100000,
                                   adjustment='all')
            df = client.get_stock_bars(req).df
            if df is None or len(df) == 0:
                logger.warning(f'  {ticker}: no bars')
                continue
            df = df.reset_index()
            tsc = 'timestamp' if 'timestamp' in df.columns else df.columns[1]
            et = df[tsc].dt.tz_convert('America/New_York')
            pre = df[(et.dt.strftime('%H:%M') >= '04:00') &
                     (et.dt.strftime('%H:%M') < '09:30')]
            if len(pre) == 0:
                logger.warning(f'  {ticker}: no premarket bars in window')
                continue
            path = os.path.join(AUX_DIR, f'{ticker}_premarket_5m.csv')
            with open(path, 'w', newline='') as f:
                w = csv.writer(f)
                w.writerow(['ts', 'open', 'high', 'low', 'close', 'volume'])
                for _, r in pre.iterrows():
                    w.writerow([int(r[tsc].timestamp() * 1000),
                                round(float(r['open']), 6), round(float(r['high']), 6),
                                round(float(r['low']), 6), round(float(r['close']), 6),
                                int(r['volume']) if r['volume'] == r['volume'] else 0])
            logger.info(f'  {ticker}: {len(pre)} premarket bars')
            ok += 1
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f'  {ticker}: {str(e)[:70]}')
    logger.info(f'premarket done: {ok}/{len(tickers)} tickers')


def splits():
    """Split history for Ghost Levels (2.4): pre-split round numbers keep
    acting as levels because the crowd's muscle memory does not split."""
    import yfinance as yf
    _ensure_aux()
    out = {}
    tickers = sorted({f.rsplit('_', 1)[0] for f in os.listdir(DATA_DIR)
                      if f.endswith('_1d.csv')})
    for i, ticker in enumerate(tickers, 1):
        symbol = ticker.replace('_', '-')
        if ticker.endswith('_F'):
            continue          # futures do not split
        try:
            s = yf.Ticker(symbol).splits
            if s is None or len(s) == 0:
                continue
            recent = {str(d.date()): float(r) for d, r in s.items()
                      if d.year >= 2015 and float(r) != 1.0}
            if recent:
                out[ticker] = recent
            time.sleep(0.15)
        except Exception:
            continue
        if i % 40 == 0:
            logger.info(f'  splits progress {i}/{len(tickers)} ({len(out)} with splits)')
    path = os.path.join(AUX_DIR, 'splits.json')
    with open(path, 'w') as f:
        json.dump(out, f, indent=1, sort_keys=True)
    logger.info(f'splits done: {len(out)} tickers with splits since 2015 -> {path}')


def sessions():
    """Session tags: NYSE + CME open/close per date, including holidays and
    early closes. Replaces the weekday approximation everywhere."""
    import exchange_calendars as xcals
    _ensure_aux()
    out = {}
    for name, code in (('NYSE', 'XNYS'), ('CME', 'CMES')):
        try:
            cal = xcals.get_calendar(code)
            rows = {}
            for d in cal.sessions_in_range('2015-01-01', '2026-12-31'):
                rows[str(d.date())] = {
                    'open': cal.session_open(d).isoformat(),
                    'close': cal.session_close(d).isoformat(),
                }
            out[name] = rows
            logger.info(f'  {name}: {len(rows)} sessions')
        except Exception as e:
            logger.warning(f'  {name}: {str(e)[:70]}')
    path = os.path.join(AUX_DIR, 'sessions.json')
    with open(path, 'w') as f:
        json.dump(out, f)
    logger.info(f'sessions done -> {path}')


# FOMC announcement dates are published years ahead and are stable. NFP is
# algorithmic (first Friday). CPI dates follow a BLS schedule that is NOT
# algorithmic - see the report for what is missing.
FOMC_DATES = [
    '2023-02-01', '2023-03-22', '2023-05-03', '2023-06-14', '2023-07-26',
    '2023-09-20', '2023-11-01', '2023-12-13',
    '2024-01-31', '2024-03-20', '2024-05-01', '2024-06-12', '2024-07-31',
    '2024-09-18', '2024-11-07', '2024-12-18',
    '2025-01-29', '2025-03-19', '2025-05-07', '2025-06-18', '2025-07-30',
    '2025-09-17', '2025-10-29', '2025-12-10',
    '2026-01-28', '2026-03-18', '2026-04-29', '2026-06-17', '2026-07-29',
]


def calendar():
    """Macro release calendar for 3.2. NFP derived, FOMC static, CPI via FRED
    if a key exists."""
    import calendar as pycal
    _ensure_aux()
    events = []

    # NFP: first Friday of each month, 08:30 ET
    for year in range(2015, 2027):
        for month in range(1, 13):
            for day in range(1, 8):
                d = datetime(year, month, day)
                if d.weekday() == 4:      # Friday
                    events.append({'date': d.strftime('%Y-%m-%d'),
                                   'time_et': '08:30', 'event': 'NFP'})
                    break
    for d in FOMC_DATES:
        events.append({'date': d, 'time_et': '14:00', 'event': 'FOMC'})

    # CPI via FRED release dates when a key is available (free, instant signup)
    fred_key = os.environ.get('FRED_API_KEY')
    cpi_count = 0
    if fred_key:
        try:
            import urllib.request
            url = ('https://api.stlouisfed.org/fred/release/dates?release_id=10'
                   f'&api_key={fred_key}&file_type=json&limit=1000')
            with urllib.request.urlopen(url, timeout=20) as r:
                data = json.loads(r.read())
            for d in data.get('release_dates', []):
                events.append({'date': d['date'], 'time_et': '08:30', 'event': 'CPI'})
                cpi_count += 1
        except Exception as e:
            logger.warning(f'  FRED CPI fetch failed: {str(e)[:70]}')
    else:
        logger.warning('  FRED_API_KEY not set: CPI release dates NOT included. '
                       'Free key at https://fred.stlouisfed.org/docs/api/api_key.html')

    events.sort(key=lambda e: (e['date'], e['time_et']))
    path = os.path.join(AUX_DIR, 'macro_calendar.json')
    with open(path, 'w') as f:
        json.dump({'events': events,
                   'coverage': {'NFP': 'derived (first Friday)',
                                'FOMC': 'static published schedule',
                                'CPI': f'{cpi_count} dates via FRED' if cpi_count
                                       else 'MISSING - needs FRED_API_KEY'}}, f, indent=1)
    logger.info(f'calendar done: {len(events)} events '
                f'(NFP+FOMC always, CPI={cpi_count}) -> {path}')


def funding():
    """Perp funding rates for 1.1 Funding Shadow. Binance.US lists no perps,
    so the SIGNAL venue is Binance global / Bybit public endpoints (no keys);
    execution stays on Binance.US spot. The venue is recorded for attribution."""
    import ccxt
    _ensure_aux()
    # Binance global and Bybit return HTTP 451 from a US IP (geo-blocked), so
    # the signal venue must be one that answers from here. Verified reachable
    # 2026-08-13: krakenfutures, deribit, bitget. Venue is recorded per row
    # because funding differs between venues and attribution matters.
    for venue_name in ('krakenfutures', 'deribit', 'bitget'):
        try:
            ex = getattr(ccxt, venue_name)({'enableRateLimit': True, 'timeout': 20000})
            ex.load_markets()
        except Exception as e:
            logger.warning(f'  {venue_name} unavailable: {str(e)[:70]}')
            continue
        pairs = {}
        for short in ('BTC', 'ETH', 'SOL'):
            cands = [s for s in ex.symbols if s.startswith(f'{short}/') and ':' in s]
            if cands:
                pairs[sorted(cands)[0]] = short
        if not pairs:
            logger.warning(f'  {venue_name}: no perp symbols matched')
            continue
        got = 0
        for symbol, short in pairs.items():
            try:
                # Paginate: a single call returns ~1000 hourly points (6 weeks),
                # but strategy 1.1 needs a 90-day trailing percentile. Walk back
                # a year so the distribution is actually estimable.
                since = int((datetime.now() - timedelta(days=400)).timestamp() * 1000)
                hist, cursor = [], since
                for _ in range(15):
                    chunk = ex.fetch_funding_rate_history(symbol, since=cursor, limit=1000)
                    if not chunk:
                        break
                    hist += chunk
                    cursor = chunk[-1]['timestamp'] + 1
                    if len(chunk) < 1000:
                        break
                    time.sleep(0.3)
                if not hist:
                    continue
                path = os.path.join(AUX_DIR, f'funding_{venue_name}_{short}.csv')
                with open(path, 'w', newline='') as f:
                    w = csv.writer(f)
                    w.writerow(['ts', 'symbol', 'funding_rate', 'venue'])
                    for h in hist:
                        w.writerow([h['timestamp'], symbol, h['fundingRate'], venue_name])
                logger.info(f'  {venue_name} {short}: {len(hist)} funding points')
                got += 1
                time.sleep(0.3)
            except Exception as e:
                logger.warning(f'  {venue_name} {symbol}: {str(e)[:70]}')
        if got:
            logger.info(f'funding done via {venue_name}')
            return
    logger.error('no funding venue reachable')


if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    n = int(sys.argv[2]) if len(sys.argv) > 2 else None
    if what in ('premarket', 'all'):
        premarket(n)
    if what in ('splits', 'all'):
        splits()
    if what in ('sessions', 'all'):
        sessions()
    if what in ('calendar', 'all'):
        calendar()
    if what in ('funding', 'all'):
        funding()
