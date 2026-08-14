"""Download historical data for multi-asset backtesting.

Uses yfinance (free, no API key) for equities, ETFs, futures, and crypto.

Asset classes:
- Equities: large cap, mid cap, small cap, penny stocks
- Sector ETFs: XLK, XLF, XLE, XLV, XLY
- Futures: ES, NQ, CL, GC (via yfinance futures tickers)
- Crypto: already downloaded from Binance, but yfinance also has BTC-USD etc.

Timeframes:
- Daily: 2+ years (yfinance unlimited)
- Weekly: 5+ years
- 1h: 730 days (2 years)
- 5m: 60 days (limited on free tier)
- 15m: 60 days (limited on free tier)

All data saved as CSV to backtest/data/<symbol>_<tf>.csv
"""
import os
import sys
import time
import logging
import yfinance as yf

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backtest', 'data')

# All tickers to download
TICKERS = {
    # Large cap tech
    'AAPL': 'Apple',
    'MSFT': 'Microsoft',
    'NVDA': 'Nvidia',
    'AMZN': 'Amazon',
    'GOOGL': 'Google',
    'META': 'Meta',
    'TSLA': 'Tesla',

    # Large cap other sectors
    'JPM': 'JPMorgan (finance)',
    'XOM': 'Exxon (energy)',
    'JNJ': 'Johnson & Johnson (health)',
    'HD': 'Home Depot (consumer)',

    # Mid cap
    'RBLX': 'Roblox (mid cap tech)',
    'DUOL': 'Duolingo (mid cap tech)',

    # Small cap
    'SPRT': 'Support.com (small cap)',
    'SOFI': 'SoFi (small cap fintech)',

    # Sector ETFs
    'XLK': 'Tech sector ETF',
    'XLF': 'Financial sector ETF',
    'XLE': 'Energy sector ETF',
    'XLV': 'Health sector ETF',
    'XLY': 'Consumer discretionary ETF',
    'SPY': 'S&P 500 ETF',
    'QQQ': 'Nasdaq 100 ETF',

    # Futures (yfinance format: ticker + =F)
    'ES=F': 'S&P 500 futures',
    'NQ=F': 'Nasdaq futures',
    'CL=F': 'Crude oil futures',
    'GC=F': 'Gold futures',

    # Crypto (also on yfinance for cross-check)
    'BTC-USD': 'Bitcoin (Yahoo)',
    'ETH-USD': 'Ethereum (Yahoo)',
}

# Timeframes to download
TIMEFRAMES = {
    '1d': {'period': '2y', 'interval': '1d'},
    '1wk': {'period': '5y', 'interval': '1wk'},
    '1h': {'period': '730d', 'interval': '1h'},
    '5m': {'period': '60d', 'interval': '5m'},
    '15m': {'period': '60d', 'interval': '15m'},
}


def download_all():
    """Download all tickers across all timeframes."""
    os.makedirs(DATA_DIR, exist_ok=True)
    total = 0
    success = 0
    fail = 0

    for ticker, name in TICKERS.items():
        for tf_label, params in TIMEFRAMES.items():
            total += 1
            # File naming: use yfinance ticker, replace special chars
            safe_ticker = ticker.replace('=', '_').replace('-', '_')
            filename = f"{safe_ticker}_{tf_label}.csv"
            filepath = os.path.join(DATA_DIR, filename)

            if os.path.exists(filepath):
                logger.info(f"SKIP (exists): {filename}")
                success += 1
                continue

            try:
                t = yf.Ticker(ticker)
                df = t.history(period=params['period'], interval=params['interval'])

                if df is None or len(df) == 0:
                    logger.warning(f"EMPTY: {ticker} {tf_label}")
                    fail += 1
                    continue

                # Save as CSV in our format: ts,open,high,low,close,volume
                df = df[['Open', 'High', 'Low', 'Close', 'Volume']].copy()
                df.index.name = 'ts'
                df.columns = ['open', 'high', 'low', 'close', 'volume']

                # Convert index to unix ms timestamp
                df['ts'] = df.index.astype('int64') // 10**6
                df = df[['ts', 'open', 'high', 'low', 'close', 'volume']]
                df.to_csv(filepath, index=False)

                logger.info(f"OK: {ticker} ({name}) {tf_label}: {len(df)} candles -> {filename}")
                success += 1
                time.sleep(0.5)  # rate limit

            except Exception as e:
                logger.error(f"FAIL: {ticker} {tf_label}: {e}")
                fail += 1

    logger.info(f"\nDownload complete: {success}/{total} succeeded, {fail} failed")
    logger.info(f"Files in {DATA_DIR}: {len(os.listdir(DATA_DIR))}")


if __name__ == '__main__':
    download_all()
