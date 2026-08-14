"""Data layer: market data collector for Binance.US.

Fetches OHLCV candles via ccxt REST and stores them in SQLite.
WebSocket support will be added for real-time updates in a future iteration.
For v1, polling REST every 15 seconds is sufficient for 15m candles.
"""
import time
import logging
import threading
from typing import List, Dict

import ccxt
import yaml

from engine.db import get_connection, insert_candle, get_candles

logger = logging.getLogger(__name__)


class DataCollector:
    """Collects market data from Binance.US and stores it in SQLite."""

    def __init__(self, config: dict):
        self.config = config
        self.exchange_name = config['exchange']['name']
        self.pairs = config['exchange']['pairs']
        self.signal_tf = config['timeframes']['signal']
        self.regime_tf = config['timeframes']['regime']
        self.running = False
        self._thread = None

        # Initialize ccxt exchange (public endpoints, no API key needed)
        exchange_class = getattr(ccxt, self.exchange_name)
        self.exchange = exchange_class({
            'enableRateLimit': True,
        })

    def fetch_ohlcv(self, pair: str, tf: str, limit: int = 200) -> List[Dict]:
        """Fetch OHLCV candles from the exchange."""
        try:
            ohlcv = self.exchange.fetch_ohlcv(pair, tf, limit=limit)
            candles = []
            for entry in ohlcv:
                candles.append({
                    'ts': entry[0],
                    'open': entry[1],
                    'high': entry[2],
                    'low': entry[3],
                    'close': entry[4],
                    'volume': entry[5],
                })
            return candles
        except Exception as e:
            logger.error(f"Error fetching {pair} {tf}: {e}")
            return []

    def store_candles(self, pair: str, tf: str, candles: List[Dict]):
        """Store candles in SQLite."""
        conn = get_connection()
        try:
            for c in candles:
                insert_candle(conn, pair, tf, c['ts'], c['open'], c['high'],
                              c['low'], c['close'], c['volume'])
            conn.commit()
        finally:
            conn.close()

    def update_all(self):
        """Fetch and store candles for all pairs and timeframes."""
        for pair in self.pairs:
            for tf in [self.signal_tf, self.regime_tf]:
                candles = self.fetch_ohlcv(pair, tf)
                if candles:
                    self.store_candles(pair, tf, candles)
                    logger.info(f"Updated {pair} {tf}: {len(candles)} candles")
                else:
                    logger.warning(f"No candles received for {pair} {tf}")

    def get_latest_candles(self, pair: str, tf: str, limit: int = 200) -> dict:
        """Get the latest candles from the database, returned as column arrays.

        Returns dict with keys: opens, highs, lows, closes, volumes, timestamps
        """
        conn = get_connection()
        try:
            rows = get_candles(conn, pair, tf, limit)
            if not rows:
                return {'opens': [], 'highs': [], 'lows': [], 'closes': [], 'volumes': [], 'timestamps': []}
            return {
                'opens': [r['open'] for r in rows],
                'highs': [r['high'] for r in rows],
                'lows': [r['low'] for r in rows],
                'closes': [r['close'] for r in rows],
                'volumes': [r['volume'] for r in rows],
                'timestamps': [r['ts'] for r in rows],
            }
        finally:
            conn.close()

    def get_top_of_book_spread(self, pair: str) -> float:
        """Get the current bid-ask spread as a percentage.

        Returns 0.0 if unable to fetch.
        """
        try:
            ticker = self.exchange.fetch_ticker(pair)
            bid = ticker.get('bid')
            ask = ticker.get('ask')
            if bid and ask:
                mid = (bid + ask) / 2
                return (ask - bid) / mid
            return 0.0
        except Exception as e:
            logger.error(f"Error fetching spread for {pair}: {e}")
            return 0.0

    def start(self, poll_interval: int = 15):
        """Start the collector in a background thread."""
        self.running = True
        self._thread = threading.Thread(target=self._run, args=(poll_interval,), daemon=True)
        self._thread.start()
        logger.info(f"DataCollector started, polling every {poll_interval}s")

    def stop(self):
        """Stop the collector."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("DataCollector stopped")

    def _run(self, poll_interval: int):
        """Main collection loop."""
        while self.running:
            try:
                self.update_all()
            except Exception as e:
                logger.error(f"Collection error: {e}")
            time.sleep(poll_interval)
