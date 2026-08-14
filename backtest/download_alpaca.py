"""Download 2 years of intraday data from Alpaca for all equities tickers.

Alpaca gives 2 years of free intraday bar data (5m, 15m, 1h) plus
unlimited daily/weekly. Much better than yfinance's 60-day intraday limit.

Saves to backtest/data/<TICKER>_<TF>.csv (overwrites yfinance versions).
"""
import os, sys, time, logging, csv
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'backtest', 'data')

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), '.env'))
API_KEY = os.environ.get('ALPACA_API_KEY')
API_SECRET = os.environ.get('ALPACA_API_SECRET')
if not API_KEY or not API_SECRET:
    sys.exit("ALPACA_API_KEY / ALPACA_API_SECRET not set. Add them to .env (never commit keys to source).")

client = StockHistoricalDataClient(API_KEY, API_SECRET)

# All equities + ETFs + futures (via ETF proxies where direct futures not available)
TICKERS = [
    # Large cap tech
    'AAPL', 'MSFT', 'NVDA', 'AMZN', 'GOOGL', 'META', 'TSLA',
    # Other sectors
    'JPM', 'XOM', 'JNJ', 'HD', 'DIS', 'BA', 'CAT',
    # Mid cap
    'RBLX', 'DUOL', 'SOFI', 'PLTR',
    # Small cap / penny
    'SNDL', 'MULN',  # penny stocks
    # Sector ETFs
    'XLK', 'XLF', 'XLE', 'XLV', 'XLY', 'XLP', 'XLB', 'XLRE', 'XLU', 'SPY', 'QQQ',
    'IWM',  # small cap Russell 2000
    'DIA',  # Dow Jones
]

# Alpaca timeframes
TIMEFRAMES = {
    '5m': TimeFrame(5, TimeFrameUnit.Minute),
    '15m': TimeFrame(15, TimeFrameUnit.Minute),
    '1h': TimeFrame(1, TimeFrameUnit.Hour),
    '1d': TimeFrame(1, TimeFrameUnit.Day),
    '1wk': TimeFrame(1, TimeFrameUnit.Week),
}


def download_ticker(ticker, tf_label, tf_alpaca):
    """Download bars for a single ticker+timeframe from Alpaca."""
    filepath = os.path.join(DATA_DIR, f"{ticker}_{tf_label}.csv")

    end = datetime.now()
    # 2 years for intraday, 5 years for daily/weekly
    if tf_label in ('1d', '1wk'):
        start = end - timedelta(days=1825)  # 5 years
    else:
        start = end - timedelta(days=730)  # 2 years

    try:
        # adjustment='all': split- AND dividend-adjusted bars, matching yfinance's
        # auto_adjust=True convention. Alpaca's default is RAW, which produced fake
        # split-gap crashes (e.g. NVDA 10:1 rendered as a -90% overnight move).
        request = StockBarsRequest(
            symbol_or_symbols=ticker,
            timeframe=tf_alpaca,
            start=start,
            end=end,
            limit=100000,
            adjustment='all',
        )
        bars = client.get_stock_bars(request)
        df = bars.df

        if df is None or len(df) == 0:
            logger.warning(f"EMPTY: {ticker} {tf_label}")
            return 0

        # Flatten multi-index and save
        df = df.reset_index()
        # Columns: symbol, timestamp, open, high, low, close, volume, ...
        # Convert to our format: ts, open, high, low, close, volume
        ts_col = 'timestamp' if 'timestamp' in df.columns else df.columns[1]

        with open(filepath, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(['ts', 'open', 'high', 'low', 'close', 'volume'])
            for _, row in df.iterrows():
                ts = int(row[ts_col].timestamp() * 1000)
                writer.writerow([
                    ts,
                    round(row['open'], 6),
                    round(row['high'], 6),
                    round(row['low'], 6),
                    round(row['close'], 6),
                    int(row['volume']) if row['volume'] == row['volume'] else 0,
                ])

        logger.info(f"OK: {ticker} {tf_label}: {len(df)} bars -> {os.path.basename(filepath)}")
        return len(df)

    except Exception as e:
        logger.error(f"FAIL: {ticker} {tf_label}: {e}")
        return 0


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    total = 0; success = 0; fail = 0

    for ticker in TICKERS:
        for tf_label, tf_alpaca in TIMEFRAMES.items():
            total += 1
            n = download_ticker(ticker, tf_label, tf_alpaca)
            if n > 0:
                success += 1
            else:
                fail += 1
            time.sleep(0.3)  # rate limit

    logger.info(f"\nAlpaca download complete: {success}/{total} succeeded, {fail} failed")
    logger.info(f"Files in {DATA_DIR}: {len(os.listdir(DATA_DIR))}")


if __name__ == '__main__':
    main()
