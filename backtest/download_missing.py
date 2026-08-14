"""Fill the two measured data gaps (2026-08-13).

GAP 1 - WEEKLY NEVER TESTED. 176 weekly files exist but every one is silently
skipped: ~262 bars (5y) means a 20% test slice of 53 bars, below the runner's
100-bar floor. Fix by downloading 15 years of weekly history so the test slice
clears the floor.

GAP 2 - QUARANTINED INTRADAY. Sector-ETF and small-cap intraday files were
quarantined as unadjusted (fake split gaps). yfinance cannot serve intraday
beyond 60 days; Alpaca can, and now requests adjustment='all'.

Both write into backtest/data/ using the standard ts,o,h,l,c,v schema.
Run: python3 backtest/download_missing.py [weekly|intraday|all]
"""
import csv
import logging
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, 'backtest', 'data')
QUARANTINE = os.path.join(DATA_DIR, 'quarantine')

load_dotenv(os.path.join(ROOT, '.env'))
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)


def _write(path, rows):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['ts', 'open', 'high', 'low', 'close', 'volume'])
        w.writerows(rows)


def weekly_long_history(years: int = 15):
    """15y of split+dividend adjusted weekly bars via yfinance."""
    import yfinance as yf
    tickers = sorted({f.rsplit('_', 1)[0] for f in os.listdir(DATA_DIR)
                      if f.endswith('_1wk.csv')})
    logger.info(f'weekly: {len(tickers)} tickers, targeting {years}y')
    ok = fail = 0
    for i, ticker in enumerate(tickers, 1):
        symbol = ticker.replace('_', '-')
        if ticker.endswith('_F'):
            symbol = ticker[:-2] + '=F'
        try:
            hist = yf.Ticker(symbol).history(period=f'{years}y', interval='1wk',
                                             auto_adjust=True)
            if hist is None or len(hist) < 300:
                logger.warning(f'  {ticker}: only {0 if hist is None else len(hist)} bars, skipped')
                fail += 1
                continue
            hist = hist.reset_index()
            dc = 'Date' if 'Date' in hist.columns else hist.columns[0]
            rows = [[int(r[dc].timestamp() * 1000), round(float(r['Open']), 6),
                     round(float(r['High']), 6), round(float(r['Low']), 6),
                     round(float(r['Close']), 6),
                     int(r['Volume']) if r['Volume'] == r['Volume'] else 0]
                    for _, r in hist.iterrows()]
            _write(os.path.join(DATA_DIR, f'{ticker}_1wk.csv'), rows)
            ok += 1
            if i % 25 == 0:
                logger.info(f'  weekly progress {i}/{len(tickers)} (ok={ok})')
            time.sleep(0.25)
        except Exception as e:
            logger.warning(f'  {ticker}: {str(e)[:70]}')
            fail += 1
    logger.info(f'weekly done: {ok} written, {fail} failed')


def intraday_from_alpaca():
    """Re-download quarantined intraday files ADJUSTED via Alpaca."""
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    key, secret = os.environ.get('ALPACA_API_KEY'), os.environ.get('ALPACA_API_SECRET')
    if not key or not secret:
        logger.error('ALPACA keys missing from .env')
        return
    client = StockHistoricalDataClient(key, secret)
    tf_map = {'5m': TimeFrame(5, TimeFrameUnit.Minute),
              '15m': TimeFrame(15, TimeFrameUnit.Minute),
              '1h': TimeFrame(1, TimeFrameUnit.Hour)}

    if not os.path.isdir(QUARANTINE):
        logger.info('no quarantine directory')
        return
    targets = []
    for f in sorted(os.listdir(QUARANTINE)):
        if not f.endswith('.csv'):
            continue
        base, tf = f[:-4].rsplit('_', 1)
        if tf in tf_map:
            targets.append((base, tf))
    logger.info(f'intraday: {len(targets)} quarantined files to refetch')

    ok = fail = 0
    end = datetime.now() - timedelta(minutes=20)   # avoid the SIP delay window
    start = end - timedelta(days=720)
    for base, tf in targets:
        try:
            req = StockBarsRequest(symbol_or_symbols=base, timeframe=tf_map[tf],
                                   start=start, end=end, limit=100000,
                                   adjustment='all')
            df = client.get_stock_bars(req).df
            if df is None or len(df) < 500:
                logger.warning(f'  {base} {tf}: {0 if df is None else len(df)} bars, skipped')
                fail += 1
                continue
            df = df.reset_index()
            tsc = 'timestamp' if 'timestamp' in df.columns else df.columns[1]
            rows = [[int(r[tsc].timestamp() * 1000), round(float(r['open']), 6),
                     round(float(r['high']), 6), round(float(r['low']), 6),
                     round(float(r['close']), 6),
                     int(r['volume']) if r['volume'] == r['volume'] else 0]
                    for _, r in df.iterrows()]
            _write(os.path.join(DATA_DIR, f'{base}_{tf}.csv'), rows)
            logger.info(f'  {base} {tf}: {len(rows)} bars (adjusted)')
            ok += 1
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f'  {base} {tf}: {str(e)[:70]}')
            fail += 1
    logger.info(f'intraday done: {ok} written, {fail} failed')


if __name__ == '__main__':
    what = sys.argv[1] if len(sys.argv) > 1 else 'all'
    if what in ('weekly', 'all'):
        weekly_long_history()
    if what in ('intraday', 'all'):
        intraday_from_alpaca()
