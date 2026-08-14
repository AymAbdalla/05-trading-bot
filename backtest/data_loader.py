"""Data loader for backtest harness.

Loads historical OHLCV data from:
1. CSV files (Binance.US historical downloads)
2. Live API fetch (for shorter periods)
3. SQLite (if already collected by the engine)

CSV format expected (Binance.US standard):
timestamp,open,high,low,close,volume,close_time,quote_volume,count,...
"""
import csv
import logging
from typing import List, Dict, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Close-to-next-open gap beyond this fraction flags a probable unadjusted
# split (NVDA's 10:1 showed as -90%, TSLA's 3:1 as -66%). Legit overnight
# moves this size exist but are rare enough that every hit deserves review.
SPLIT_GAP_THRESHOLD = 0.40


def check_integrity(candles: List[Dict], label: str = '') -> List[str]:
    """Scan a candle series for signs of corrupt data. Returns a list of
    human-readable anomaly strings (empty = clean).

    Checks: unsorted/duplicate timestamps, non-positive prices, and
    close-to-open gaps large enough to be unadjusted splits.
    """
    problems = []
    prev_ts = None
    prev_close = None
    for idx, c in enumerate(candles):
        if c['open'] <= 0 or c['close'] <= 0 or c['high'] <= 0 or c['low'] <= 0:
            problems.append(f"{label} row {idx}: non-positive price")
            continue
        if prev_ts is not None:
            if c['ts'] < prev_ts:
                problems.append(f"{label} row {idx}: timestamps out of order")
            elif c['ts'] == prev_ts:
                problems.append(f"{label} row {idx}: duplicate timestamp {c['ts']}")
        if prev_close is not None and prev_close > 0:
            gap = (c['open'] - prev_close) / prev_close
            if gap <= -SPLIT_GAP_THRESHOLD or gap >= 1.0 / (1.0 - SPLIT_GAP_THRESHOLD) - 1.0:
                problems.append(
                    f"{label} row {idx}: {gap * +100:.0f}% close-to-open gap "
                    f"({prev_close} -> {c['open']}) - probable unadjusted split or bad row"
                )
        prev_ts = c['ts']
        prev_close = c['close']
    return problems


def load_csv(filepath: str, pair: str = '', tf: str = '15m') -> List[Dict]:
    """Load historical candles from a Binance.US CSV file.

    Binance.US CSV columns: unix, open, high, low, close, volume,
    close_time, quote_asset_volume, number_of_trades, taker_buy_base,
    taker_buy_quote, ignore

    Returns list of candle dicts with ts, open, high, low, close, volume.
    """
    path = Path(filepath)

    if not path.exists():
        logger.error(f"CSV file not found: {filepath}")
        return []

    # pandas does the parsing (library policy 2026-08-12: the hand-rolled CSV
    # parser silently produced 0 candles for 706 yfinance-format files).
    # Both supported layouts put ts/Date, open, high, low, close, volume in
    # columns 0-5; extra columns (Dividends, Stock Splits, quote_volume...)
    # are ignored. Headerless Binance raw files are detected by the first
    # cell parsing as a number.
    import pandas as pd

    try:
        df = pd.read_csv(path, header=None, skip_blank_lines=True)
    except Exception as e:
        logger.error(f"Failed to read CSV {filepath}: {e}")
        return []
    if df.shape[1] < 6 or len(df) == 0:
        logger.warning(f"{path.name}: not OHLCV-shaped ({df.shape[1]} cols)")
        return []

    first_cell = str(df.iloc[0, 0])
    try:
        float(first_cell)
    except ValueError:
        df = df.iloc[1:]  # header row
    if len(df) == 0:
        return []

    df = df.iloc[:, :6]
    df.columns = ['ts', 'open', 'high', 'low', 'close', 'volume']

    ts_numeric = pd.to_numeric(df['ts'], errors='coerce')
    if ts_numeric.notna().all():
        ts_ms = ts_numeric.astype('int64')
        ts_ms = ts_ms.where(ts_ms >= 1e12, ts_ms * 1000)   # seconds -> ms
        ts_ms = ts_ms.where(ts_ms < 1e14, ts_ms // 1000)   # microseconds (Binance 2025+) -> ms
    else:
        # yfinance format: '2024-08-13 00:00:00-04:00' (ISO, tz-aware)
        parsed = pd.to_datetime(df['ts'], errors='coerce', utc=True)
        ts_ms = (parsed.astype('int64') // 10**6)
        df = df[parsed.notna()]
        ts_ms = ts_ms[parsed.notna()]

    for col in ('open', 'high', 'low', 'close', 'volume'):
        df[col] = pd.to_numeric(df[col], errors='coerce')
    valid = df[['open', 'high', 'low', 'close', 'volume']].notna().all(axis=1)
    df = df[valid]
    ts_ms = ts_ms[valid]

    candles = [
        {'ts': int(t), 'open': float(o), 'high': float(h), 'low': float(l),
         'close': float(c), 'volume': float(v)}
        for t, o, h, l, c, v in zip(ts_ms, df['open'], df['high'], df['low'],
                                    df['close'], df['volume'])
    ]

    # Trust nothing: sort, dedup, and flag probable split gaps loudly.
    candles.sort(key=lambda c: c['ts'])
    deduped = []
    last_ts = None
    for c in candles:
        if c['ts'] != last_ts:
            deduped.append(c)
            last_ts = c['ts']
    if len(deduped) != len(candles):
        logger.warning(f"{path.name}: dropped {len(candles) - len(deduped)} duplicate-timestamp rows")
    candles = deduped

    problems = [p for p in check_integrity(candles, path.name) if 'gap' in p]
    for p in problems[:5]:
        logger.warning(f"DATA INTEGRITY: {p}")
    if len(problems) > 5:
        logger.warning(f"DATA INTEGRITY: {path.name}: {len(problems) - 5} more gap anomalies suppressed")

    logger.info(f"Loaded {len(candles)} candles from {filepath}")
    return candles


def load_from_db(pair: str, tf: str, limit: int = 2000,
                 db_path: str = 'db/trading.db') -> List[Dict]:
    """Load candles from the engine's SQLite database."""
    import sqlite3
    from engine.db import get_db_path

    path = db_path or get_db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM candles WHERE pair = ? AND tf = ? ORDER BY ts ASC LIMIT ?",
        (pair, tf, limit)
    ).fetchall()
    conn.close()

    candles = [dict(r) for r in rows]
    logger.info(f"Loaded {len(candles)} {pair} {tf} candles from DB")
    return candles


def fetch_historical(pair: str, tf: str = '15m', days: int = 270) -> List[Dict]:
    """Fetch historical candles from Binance.US via ccxt.

    270 days = ~9 months of 15m data.
    """
    import ccxt
    import time

    exchange = ccxt.binanceus({'enableRateLimit': True})

    # Calculate timeframe in milliseconds
    tf_ms = {
        '15m': 15 * 60 * 1000,
        '1h': 60 * 60 * 1000,
    }.get(tf, 15 * 60 * 1000)

    now = int(time.time() * 1000)
    start_ts = now - (days * 24 * 60 * 60 * 1000)

    all_candles = []
    current = start_ts

    while current < now:
        try:
            ohlcv = exchange.fetch_ohlcv(pair, tf, since=current, limit=1000)
            if not ohlcv:
                break

            for entry in ohlcv:
                all_candles.append({
                    'ts': entry[0],
                    'open': entry[1],
                    'high': entry[2],
                    'low': entry[3],
                    'close': entry[4],
                    'volume': entry[5],
                })

            current = ohlcv[-1][0] + tf_ms
            time.sleep(0.2)  # rate limit

        except Exception as e:
            logger.error(f"Error fetching {pair} {tf}: {e}")
            break

    logger.info(f"Fetched {len(all_candles)} {pair} {tf} candles from Binance.US")
    return all_candles


def save_csv(candles: List[Dict], filepath: str):
    """Save candles to CSV for caching."""
    with open(filepath, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['unix', 'open', 'high', 'low', 'close', 'volume'])
        for c in candles:
            writer.writerow([c['ts'], c['open'], c['high'], c['low'], c['close'], c['volume']])
    logger.info(f"Saved {len(candles)} candles to {filepath}")
