"""Signal layer: scanner that runs all active strategies on each new candle.

For each pair, on each new 15m candle:
1. Run every active strategy's scan() method.
2. Collect all signals.
3. Apply the confirmation stack (regime, RSI, volume, location, spread).
4. Log every signal (acted or skipped with reason) to the signals table.
5. For entry signals on the same pair, select the highest confidence.
6. Exit signals (close_long, tighten_stop) are processed immediately.

The scanner does NOT place orders. It emits Signal objects to a queue
that the execution layer consumes.
"""
import json
import time
import logging
import threading
from typing import Dict, List, Optional, Tuple
from queue import Queue

from engine.db import get_connection, insert_signal, get_candles
from engine.collector import DataCollector
from strategies.builtin.patterns import ALL_BUILTIN, ENTRY_STRATEGIES, EXIT_STRATEGIES
from indicators.ema import latest_ema, ema_slope
from indicators.rsi import latest_rsi
from indicators.volume import volume_ratio
from indicators.atr import latest_atr
from indicators.support_resistance import find_support_levels

logger = logging.getLogger(__name__)


class ConfirmationResult:
    """Result of running the confirmation stack on a signal."""

    def __init__(self, passed: bool, skip_reason: Optional[str] = None,
                 regime: str = '', features: Optional[dict] = None):
        self.passed = passed
        self.skip_reason = skip_reason
        self.regime = regime
        self.features = features or {}


class Scanner:
    """Scans market data for trading signals using all active strategies."""

    def __init__(self, config: dict, collector: DataCollector,
                 signal_queue: Optional[Queue] = None):
        self.config = config
        self.collector = collector
        self.signal_queue = signal_queue or Queue()
        self.running = False
        self._thread = None

        # Confirmation stack params
        conf = config.get('strategy', {}).get('confirmation', {})
        self.regime_ema_period = conf.get('regime_ema_period', 50)
        self.regime_lookback = conf.get('regime_lookback', 10)
        self.rsi_period = conf.get('rsi_period', 14)
        self.rsi_max_entry = conf.get('rsi_max_entry', 60)
        self.rsi_reversal_boost = conf.get('rsi_reversal_boost', 45)
        self.volume_sma_period = conf.get('volume_sma_period', 20)
        self.volume_min_ratio = conf.get('volume_min_ratio', 1.5)
        self.support_lookback = conf.get('support_lookback', 100)
        self.support_min_touches = conf.get('support_min_touches', 2)
        self.support_cluster_atr_mult = conf.get('support_cluster_atr_mult', 0.5)
        self.location_atr_mult = conf.get('location_atr_mult', 1.5)
        self.spread_max = conf.get('spread_max', 0.001)

        # Active strategies (V1: all builtin. Future: load from registry)
        self.entry_strategies = ENTRY_STRATEGIES
        self.exit_strategies = EXIT_STRATEGIES

        # Track last candle timestamp per pair to avoid re-scanning
        self._last_scanned_ts: Dict[str, int] = {}

    @staticmethod
    def _tf_ms(tf: str) -> int:
        """Timeframe string ('15m', '1h', '4h', '1d') -> milliseconds."""
        units = {'m': 60_000, 'h': 3_600_000, 'd': 86_400_000, 'w': 604_800_000}
        return int(tf[:-1]) * units[tf[-1]]

    @staticmethod
    def _drop_forming(cols: Dict[str, List], interval_ms: int) -> Dict[str, List]:
        """Drop the last candle if it is still forming.

        ccxt's fetch_ohlcv includes the in-progress candle. Scanning it was
        the audited B1 bug: patterns evaluated on seconds-old partial candles
        (which then repaint), and the volume filter compared near-zero partial
        volume against full-candle averages, killing nearly every entry. Only
        CLOSED candles (open_ts + interval <= now) may be scanned - matching
        the backtest, which only ever sees completed candles.
        """
        ts = cols.get('timestamps')
        if not ts:
            return cols
        now_ms = int(time.time() * 1000)
        if ts[-1] + interval_ms > now_ms:
            return {k: v[:-1] if isinstance(v, list) else v for k, v in cols.items()}
        return cols

    def _prepare_candles(self, pair: str) -> Optional[Dict[str, List[float]]]:
        """Load latest CLOSED candles for a pair and format as column arrays.

        Returns None if insufficient data.
        """
        signal_tf = self.config['timeframes']['signal']
        regime_tf = self.config['timeframes']['regime']

        # Get signal timeframe candles (need at least 100 for support, ATR, etc.)
        signal_candles = self.collector.get_latest_candles(pair, signal_tf, limit=200)
        signal_candles = self._drop_forming(signal_candles, self._tf_ms(signal_tf))
        if len(signal_candles['closes']) < 50:
            logger.warning(f"{pair}: insufficient signal candles ({len(signal_candles['closes'])})")
            return None

        # Get regime timeframe candles for EMA slope (closed candles only)
        regime_candles = self.collector.get_latest_candles(pair, regime_tf, limit=100)
        regime_candles = self._drop_forming(regime_candles, self._tf_ms(regime_tf))
        if len(regime_candles['closes']) < self.regime_ema_period + self.regime_lookback:
            logger.warning(f"{pair}: insufficient regime candles")
            return None

        return {
            'opens': signal_candles['opens'],
            'highs': signal_candles['highs'],
            'lows': signal_candles['lows'],
            'closes': signal_candles['closes'],
            'volumes': signal_candles['volumes'],
            'timestamps': signal_candles['timestamps'],
            'regime_closes': regime_candles['closes'],
            'regime_highs': regime_candles['highs'],
            'regime_lows': regime_candles['lows'],
        }

    def _check_regime(self, candles: Dict) -> Tuple[bool, str]:
        """Check 1h EMA(50) slope and price position.

        Returns (passed, regime_label).
        """
        regime_closes = candles['regime_closes']
        regime_highs = candles['regime_highs']
        regime_lows = candles['regime_lows']

        ema_val = latest_ema(regime_closes, self.regime_ema_period)
        # ema_slope returns a FLOAT. SPEC 5.2 #1 requires the slope to be
        # POSITIVE; using the raw float as a boolean (the audited B4 bug)
        # made any nonzero slope - including a falling EMA - pass.
        slope_val = ema_slope(regime_closes, self.regime_ema_period, self.regime_lookback)
        slope_ok = slope_val > 0
        price_above = regime_closes[-1] > ema_val

        if slope_ok and price_above:
            return True, 'uptrend'
        elif not slope_ok and not price_above:
            return False, 'downtrend'
        else:
            return False, 'sideways'

    def _check_location(self, entry: float, lows: List[float],
                        highs: List[float], closes: List[float],
                        atr_val: float) -> bool:
        """Check if entry is within 1.5x ATR of a support level."""
        support_levels = find_support_levels(
            lows, highs, closes,
            lookback=self.support_lookback,
            min_touches=self.support_min_touches,
            cluster_atr_mult=self.support_cluster_atr_mult
        )
        if not support_levels:
            return False

        for support in support_levels:
            if abs(entry - support) <= self.location_atr_mult * atr_val:
                return True
        return False

    def _confirmation_stack(self, signal, candles: Dict,
                            pair: str) -> ConfirmationResult:
        """Run the full confirmation stack on an entry signal.

        Returns ConfirmationResult with passed/skipped and features.
        """
        closes = candles['closes']
        highs = candles['highs']
        lows = candles['lows']
        volumes = candles['volumes']

        # Compute features for logging regardless of pass/fail
        atr_val = latest_atr(highs, lows, closes, 14)
        rsi_val = latest_rsi(closes, self.rsi_period)
        vol_ratio = volume_ratio(volumes, self.volume_sma_period)
        regime_passed, regime_label = self._check_regime(candles)

        features = {
            'rsi': round(rsi_val, 2),
            'volume_ratio': round(vol_ratio, 2),
            'atr': round(atr_val, 6),
            'regime': regime_label,
            'entry': signal.entry,
            'stop': signal.stop,
            'target': signal.target,
            'confidence': signal.confidence,
            'valid_for': signal.valid_for,  # pending-order expiry needs this
        }

        # 1. Regime check
        if not regime_passed:
            return ConfirmationResult(
                False, f"regime_fail: {regime_label}", regime_label, features
            )

        # 2. RSI check
        if rsi_val > self.rsi_max_entry:
            return ConfirmationResult(
                False, f"rsi_high: {rsi_val:.1f} > {self.rsi_max_entry}", regime_label, features
            )

        # 3. Volume check
        if vol_ratio < self.volume_min_ratio:
            return ConfirmationResult(
                False, f"volume_low: {vol_ratio:.2f} < {self.volume_min_ratio}", regime_label, features
            )

        # 4. Location check (near support)
        location_ok = self._check_location(signal.entry, lows, highs, closes, atr_val)
        if not location_ok:
            return ConfirmationResult(
                False, "location_fail: not near support", regime_label, features
            )

        # 5. Spread check (live, requires network call)
        spread = self.collector.get_top_of_book_spread(pair)
        features['spread'] = round(spread, 6)
        if spread > self.spread_max:
            return ConfirmationResult(
                False, f"spread_wide: {spread:.4f} > {self.spread_max}", regime_label, features
            )

        # All checks passed
        # Boost confidence for reversal patterns in oversold conditions
        if signal.direction == 'bullish' and rsi_val < self.rsi_reversal_boost:
            features['confidence_boost'] = True
            features['original_confidence'] = signal.confidence
            signal.confidence = min(0.9, signal.confidence + 0.1)

        return ConfirmationResult(True, None, regime_label, features)

    def _log_signal(self, pair: str, tf: str, signal, result: ConfirmationResult,
                    acted: bool, mode: str = 'paper'):
        """Log a signal to the database (acted or skipped)."""
        ts = int(time.time() * 1000)
        signal_data = {
            'ts': ts,
            'pair': pair,
            'tf': tf,
            'strategy_id': signal.strategy_id,
            'pattern': signal.pattern,
            'direction': signal.direction,
            'confidence': signal.confidence,
            'features_json': json.dumps(result.features),
            'acted': 1 if acted else 0,
            'skip_reason': result.skip_reason,
            'mode': mode,
        }
        conn = get_connection()
        try:
            signal_id = insert_signal(conn, signal_data)
            conn.commit()
            return signal_id
        except Exception as e:
            logger.error(f"Failed to log signal: {e}")
            return None
        finally:
            conn.close()

    def _scan_pair(self, pair: str):
        """Run all strategies on a single pair and process results."""
        candles = self._prepare_candles(pair)
        if candles is None:
            return

        signal_tf = self.config['timeframes']['signal']
        latest_ts = candles['timestamps'][-1]

        # Skip if we already scanned this candle
        if self._last_scanned_ts.get(pair) == latest_ts:
            return
        self._last_scanned_ts[pair] = latest_ts

        # Run exit strategies first (process immediately, don't gate).
        # NOTE on acted flags: the scanner NEVER writes acted=1. A signal is
        # only "acted" once the execution layer's risk gate approves it and an
        # order is actually placed - the executor must then call
        # db.mark_signal_acted(signal_id). Logging acted=1 here (the audited
        # M6 bug) corrupted the skipped-signal dataset at the source.
        entries_blocked_by = None
        for strategy in self.exit_strategies:
            try:
                signal = strategy.scan(candles)
                if signal is not None:
                    signal.pair = pair
                    # Exit signals get a minimal confirmation (just regime for features)
                    _, regime_label = self._check_regime(candles)
                    result = ConfirmationResult(
                        True, None, regime_label,
                        {'action': signal.action, 'atr': signal.features.get('atr', 0)}
                    )
                    if signal.action == 'block_entries':
                        # Enforced HERE (entries skipped below), not by the
                        # executor - log it as such and don't queue it, or it
                        # would land in handle_exit with a wrong label (N4).
                        result.skip_reason = 'block_enforced_in_scanner'
                        self._log_signal(pair, signal_tf, signal, result, acted=False)
                        entries_blocked_by = signal.pattern
                    else:
                        signal_id = self._log_signal(pair, signal_tf, signal, result, acted=False)
                        self.signal_queue.put(('exit', pair, signal, signal_id))
                    logger.info(f"{pair}: EXIT signal from {signal.pattern} (action={signal.action})")
            except Exception as e:
                logger.error(f"Exit strategy {strategy.name} error on {pair}: {e}")

        # SPEC 5.1 #7: a doji (or any block_entries signal) blocks new entries
        # on this candle. Enforced HERE, not left to a nonexistent consumer.
        if entries_blocked_by is not None:
            logger.info(f"{pair}: entries blocked this candle by {entries_blocked_by}")
            return

        # Run entry strategies
        entry_signals: List = []
        for strategy in self.entry_strategies:
            try:
                signal = strategy.scan(candles)
                if signal is not None:
                    signal.pair = pair
                    result = self._confirmation_stack(signal, candles, pair)

                    if result.passed:
                        signal_id = self._log_signal(pair, signal_tf, signal, result, acted=False)
                        entry_signals.append((signal, signal_id))
                        logger.info(
                            f"{pair}: ENTRY signal from {signal.pattern} "
                            f"(confidence={signal.confidence:.2f}, regime={result.regime})"
                        )
                    else:
                        self._log_signal(pair, signal_tf, signal, result, acted=False)
                        logger.debug(
                            f"{pair}: {signal.pattern} skipped: {result.skip_reason}"
                        )
            except Exception as e:
                logger.error(f"Entry strategy {strategy.name} error on {pair}: {e}")

        # Select highest confidence entry signal for this pair (if multiple fired)
        if entry_signals:
            best, best_id = max(entry_signals, key=lambda s: s[0].confidence)
            self.signal_queue.put(('entry', pair, best, best_id))
            # Non-selected signals must not sit unlabeled (N4): record why
            # they didn't proceed so the skipped-signal dataset stays honest.
            for sig, sid in entry_signals:
                if sid is not None and sid != best_id:
                    conn = get_connection()
                    try:
                        from engine.db import update_signal_skip_reason
                        update_signal_skip_reason(
                            conn, sid, f'not_selected: {best.pattern} had higher confidence')
                        conn.commit()
                    finally:
                        conn.close()
            if len(entry_signals) > 1:
                logger.info(
                    f"{pair}: {len(entry_signals)} entry signals, "
                    f"selected {best.pattern} (confidence={best.confidence:.2f})"
                )

    def scan_once(self):
        """Run a single scan cycle across all pairs."""
        for pair in self.config['exchange']['pairs']:
            try:
                self._scan_pair(pair)
            except Exception as e:
                logger.error(f"Scan error for {pair}: {e}")

    def start(self, poll_interval: int = 15):
        """Start the scanner in a background thread."""
        self.running = True
        self._thread = threading.Thread(target=self._run, args=(poll_interval,), daemon=True)
        self._thread.start()
        logger.info(f"Scanner started, polling every {poll_interval}s")

    def stop(self):
        """Stop the scanner."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("Scanner stopped")

    def _run(self, poll_interval: int):
        """Main scanning loop."""
        while self.running:
            try:
                self.scan_once()
            except Exception as e:
                logger.error(f"Scanner loop error: {e}")
            time.sleep(poll_interval)
