"""Candlestick pattern detection.

All patterns operate on the most recent candle(s) in a series.
Each function returns a dict with:
  - 'found': bool
  - 'direction': 'bullish' | 'bearish' | 'neutral'
  - 'entry': float or None (entry price if bullish)
  - 'stop': float or None (stop loss if bullish)
  - 'target': float or None (target price if bullish, computed by caller)
  - 'atr': float (current ATR for stop/target calculation by caller)
"""
from typing import List, Dict, Optional

from indicators.atr import latest_atr


def is_downswing(highs: List[float], atr_val: float, lookback: int = 20) -> bool:
    """Check if the last `lookback` candles contain a decline >= 2 * ATR from the high."""
    if len(highs) < lookback:
        return False
    recent_high = max(highs[-lookback:])
    current = highs[-1]
    return (recent_high - current) >= 2 * atr_val


def is_upswing(lows: List[float], atr_val: float, lookback: int = 20) -> bool:
    """Check if the last `lookback` candles contain a rise >= 2 * ATR from the low."""
    if len(lows) < lookback:
        return False
    recent_low = min(lows[-lookback:])
    current = lows[-1]
    return (current - recent_low) >= 2 * atr_val


def bullish_engulfing(opens: List[float], highs: List[float], lows: List[float], closes: List[float]) -> Dict:
    """Bullish Engulfing pattern: green body fully engulfs prior red body."""
    result = {'found': False, 'direction': 'neutral', 'entry': None, 'stop': None, 'target': None}

    if len(closes) < 3:
        return result

    atr_val = latest_atr(highs, lows, closes, 14)
    if atr_val == 0:
        return result

    prev_open, prev_close = opens[-2], closes[-2]
    curr_open, curr_close = opens[-1], closes[-1]

    # Prior candle is red (bearish)
    prev_red = prev_close < prev_open
    # Current candle is green (bullish)
    curr_green = curr_close > curr_open
    # Current body engulfs prior body
    engulfs = curr_open <= prev_close and curr_close >= prev_open

    # After a downswing or near support
    downswing = is_downswing(highs, atr_val)

    if prev_red and curr_green and engulfs and downswing:
        result = {
            'found': True,
            'direction': 'bullish',
            'entry': curr_close,
            'stop': min(lows[-2], lows[-1]) - 0.25 * atr_val,
            'target': None,  # computed by caller using 2R
            'atr': atr_val,
        }

    return result


def hammer(opens: List[float], highs: List[float], lows: List[float], closes: List[float]) -> Dict:
    """Hammer pattern: lower wick >= 2x body, close in top third of range."""
    result = {'found': False, 'direction': 'neutral', 'entry': None, 'stop': None, 'target': None}

    if len(closes) < 3:
        return result

    atr_val = latest_atr(highs, lows, closes, 14)
    if atr_val == 0:
        return result

    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o)
    range_val = h - l
    lower_wick = min(o, c) - l
    upper_wick = h - max(o, c)

    if range_val == 0:
        return result

    # Lower wick >= 2x body
    wick_ok = lower_wick >= 2 * body and body > 0
    # Close in top third
    top_third = c >= l + (range_val * 2 / 3)
    # Small upper wick
    small_upper = upper_wick < body
    # After a downswing
    downswing = is_downswing(highs, atr_val)

    if wick_ok and top_third and small_upper and downswing:
        result = {
            'found': True,
            'direction': 'bullish',
            'entry': h,  # buy stop at hammer high
            'stop': l - 0.25 * atr_val,
            'target': None,
            'atr': atr_val,
            'valid_for': 2,  # valid for 2 candles
        }

    return result


def morning_star(opens: List[float], highs: List[float], lows: List[float], closes: List[float]) -> Dict:
    """Morning Star: red candle -> small body -> green candle closing above midpoint of candle 1."""
    result = {'found': False, 'direction': 'neutral', 'entry': None, 'stop': None, 'target': None}

    if len(closes) < 4:
        return result

    atr_val = latest_atr(highs, lows, closes, 14)
    if atr_val == 0:
        return result

    c1_open, c1_close = opens[-3], closes[-3]
    c2_open, c2_close = opens[-2], closes[-2]
    c3_open, c3_close = opens[-1], closes[-1]

    # Candle 1: red (bearish), sizable body
    c1_red = c1_close < c1_open
    c1_body = abs(c1_close - c1_open)

    # Candle 2: small body
    c2_body = abs(c2_close - c2_open)
    c2_small = c2_body < c1_body * 0.5

    # Candle 2 closes lower than candle 1 close
    c2_lower = c2_close < c1_close

    # Candle 3: green (bullish), closes above midpoint of candle 1
    c3_green = c3_close > c3_open
    c1_midpoint = (c1_open + c1_close) / 2
    c3_above_mid = c3_close > c1_midpoint

    downswing = is_downswing(highs, atr_val)

    if c1_red and c2_small and c2_lower and c3_green and c3_above_mid and downswing:
        result = {
            'found': True,
            'direction': 'bullish',
            'entry': c3_close,
            'stop': min(lows[-2], lows[-1]) - 0.25 * atr_val,
            'target': None,
            'atr': atr_val,
        }

    return result


def piercing_line(opens: List[float], highs: List[float], lows: List[float], closes: List[float]) -> Dict:
    """Piercing Line: green opens below prior red low, closes above its midpoint."""
    result = {'found': False, 'direction': 'neutral', 'entry': None, 'stop': None, 'target': None}

    if len(closes) < 3:
        return result

    atr_val = latest_atr(highs, lows, closes, 14)
    if atr_val == 0:
        return result

    prev_open, prev_close = opens[-2], closes[-2]
    curr_open, curr_close = opens[-1], closes[-1]

    prev_red = prev_close < prev_open
    curr_green = curr_close > curr_open
    opens_below = curr_open < lows[-2]  # SPEC 5.1 #4: opens below prior red LOW
    midpoint = (prev_open + prev_close) / 2
    closes_above_mid = curr_close > midpoint

    downswing = is_downswing(highs, atr_val)

    if prev_red and curr_green and opens_below and closes_above_mid and downswing:
        result = {
            'found': True,
            'direction': 'bullish',
            'entry': curr_close,
            'stop': min(lows[-2], lows[-1]) - 0.25 * atr_val,
            'target': None,
            'atr': atr_val,
        }

    return result


def three_white_soldiers(opens: List[float], highs: List[float], lows: List[float], closes: List[float]) -> Dict:
    """Three White Soldiers: 3 consecutive green candles, each closing near its high.
    This is a TRAIL signal, not an entry. Tightens stop on open positions.
    """
    result = {'found': False, 'direction': 'neutral', 'entry': None, 'stop': None, 'target': None}

    if len(closes) < 4:
        return result

    atr_val = latest_atr(highs, lows, closes, 14)
    if atr_val == 0:
        return result

    c1_o, c1_c = opens[-3], closes[-3]
    c2_o, c2_c = opens[-2], closes[-2]
    c3_o, c3_c = opens[-1], closes[-1]

    all_green = c1_c > c1_o and c2_c > c2_o and c3_c > c3_o
    rising_closes = c1_c < c2_c < c3_c

    # Each closes near its high (top 25% of range)
    def near_high(o, c, h, l):
        r = h - l
        if r == 0:
            return False
        return c >= l + r * 0.75

    near_highs = (
        near_high(c1_o, c1_c, highs[-3], lows[-3]) and
        near_high(c2_o, c2_c, highs[-2], lows[-2]) and
        near_high(c3_o, c3_c, highs[-1], lows[-1])
    )

    upswing = is_upswing(lows, atr_val)

    if all_green and rising_closes and near_highs and upswing:
        result = {
            'found': True,
            'direction': 'trail',  # trail signal, not entry
            'entry': None,
            'stop': lows[-2] - 0.25 * atr_val,  # tighten stop below soldier #2
            'target': None,
            'atr': atr_val,
        }

    return result


def bearish_exit(opens: List[float], highs: List[float], lows: List[float], closes: List[float]) -> Dict:
    """Shooting Star or Bearish Engulfing: exit signal for open longs."""
    result = {'found': False, 'direction': 'neutral', 'entry': None, 'stop': None, 'target': None}

    if len(closes) < 3:
        return result

    atr_val = latest_atr(highs, lows, closes, 14)
    if atr_val == 0:
        return result

    upswing = is_upswing(lows, atr_val)
    if not upswing:
        return result

    # Shooting Star: upper wick >= 2x body, close in bottom third
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o)
    range_val = h - l
    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    shooting_star = False
    bearish_engulf = False

    if range_val > 0 and body > 0:
        shooting_star = (
            upper_wick >= 2 * body and
            c <= l + (range_val * 1 / 3) and
            lower_wick < body
        )

    # Bearish Engulfing: red body engulfs prior green body
    prev_open, prev_close = opens[-2], closes[-2]
    prev_green = prev_close > prev_open
    curr_red = c < o
    engulfs = o >= prev_close and c <= prev_open

    if prev_green and curr_red and engulfs:
        bearish_engulf = True

    if shooting_star or bearish_engulf:
        result = {
            'found': True,
            'direction': 'bearish',
            'entry': None,
            'stop': None,
            'target': None,
            'atr': atr_val,
            'action': 'close_long',
        }

    return result


def doji(opens: List[float], highs: List[float], lows: List[float], closes: List[float]) -> Dict:
    """Doji filter: body <= 10% of range. Blocks new entries."""
    result = {'found': False, 'direction': 'neutral', 'entry': None, 'stop': None, 'target': None}

    if len(closes) < 2:
        return result

    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o)
    range_val = h - l

    if range_val > 0 and body <= range_val * 0.10:
        result = {
            'found': True,
            'direction': 'neutral',
            'entry': None,
            'stop': None,
            'target': None,
            'action': 'block_entries',
        }

    return result
