"""Complete candlestick pattern detection library (35+ patterns).

All patterns operate on the most recent candle(s) in a series.
Each function returns a dict with:
  - 'found': bool
  - 'direction': 'bullish' | 'bearish' | 'neutral'
  - 'entry': float or None
  - 'stop': float or None
  - 'target': None (computed by caller using 2R)
  - 'atr': float
  - optional 'valid_for': int

Stops use ATR-based placement. All patterns require ATR(14) > 0.
"""
from typing import List, Dict
from indicators.atr import latest_atr
from indicators.patterns import is_downswing, is_upswing


def _base_result() -> Dict:
    return {'found': False, 'direction': 'neutral', 'entry': None,
            'stop': None, 'target': None, 'atr': 0.0}


# ============ BULLISH PATTERNS ============

def bullish_engulfing(opens, highs, lows, closes) -> Dict:
    """Green body fully engulfs prior red body, after downswing."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    po, pc = opens[-2], closes[-2]
    co, cc = opens[-1], closes[-1]
    if pc < po and cc > co and co <= pc and cc >= po and is_downswing(highs, atr):
        r.update(found=True, direction='bullish', entry=cc,
                 stop=min(lows[-2], lows[-1]) - 0.25 * atr, atr=atr)
    return r


def hammer(opens, highs, lows, closes) -> Dict:
    """Lower wick >= 2x body, close in top third, after downswing."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    lw = min(o, c) - l; uw = h - max(o, c)
    if lw >= 2 * body and body > 0 and c >= l + rng * 2/3 and uw < body and is_downswing(highs, atr):
        r.update(found=True, direction='bullish', entry=h,
                 stop=l - 0.25 * atr, atr=atr, valid_for=2)
    return r


def inverted_hammer(opens, highs, lows, closes) -> Dict:
    """Upper wick >= 2x body, lower wick small, after downswing."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    uw = h - max(o, c); lw = min(o, c) - l
    if uw >= 2 * body and body > 0 and lw < body and is_downswing(highs, atr):
        r.update(found=True, direction='bullish', entry=c,
                 stop=l - 0.25 * atr, atr=atr, valid_for=2)
    return r


def morning_star(opens, highs, lows, closes) -> Dict:
    """Red -> small body -> green closing above midpoint of candle 1."""
    r = _base_result()
    if len(closes) < 4: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1o, c1c = opens[-3], closes[-3]
    c2o, c2c = opens[-2], closes[-2]
    c3o, c3c = opens[-1], closes[-1]
    c1_red = c1c < c1o
    c2_small = abs(c2c - c2o) < abs(c1c - c1o) * 0.5
    c2_lower = c2c < c1c
    c3_green = c3c > c3o
    c3_above = c3c > (c1o + c1c) / 2
    if c1_red and c2_small and c2_lower and c3_green and c3_above and is_downswing(highs, atr):
        r.update(found=True, direction='bullish', entry=c3c,
                 stop=min(lows[-2], lows[-1]) - 0.25 * atr, atr=atr)
    return r


def piercing_line(opens, highs, lows, closes) -> Dict:
    """Green opens below prior red low, closes above its midpoint."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    po, pc = opens[-2], closes[-2]
    co, cc = opens[-1], closes[-1]
    # SPEC 5.1 #4: opens below prior red LOW (not prior close).
    if pc < po and cc > co and co < lows[-2] and cc > (po + pc) / 2 and is_downswing(highs, atr):
        r.update(found=True, direction='bullish', entry=cc,
                 stop=min(lows[-2], lows[-1]) - 0.25 * atr, atr=atr)
    return r


def three_white_soldiers(opens, highs, lows, closes) -> Dict:
    """3 consecutive green candles, each closing near its high. Trail signal."""
    r = _base_result()
    if len(closes) < 4: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1o, c1c = opens[-3], closes[-3]
    c2o, c2c = opens[-2], closes[-2]
    c3o, c3c = opens[-1], closes[-1]
    all_green = c1c > c1o and c2c > c2o and c3c > c3o
    rising = c1c < c2c < c3c
    def near_high(o, c, h, l):
        rng = h - l
        return rng > 0 and c >= l + rng * 0.75
    nh = (near_high(c1o, c1c, highs[-3], lows[-3]) and
          near_high(c2o, c2c, highs[-2], lows[-2]) and
          near_high(c3o, c3c, highs[-1], lows[-1]))
    if all_green and rising and nh and is_upswing(lows, atr):
        r.update(found=True, direction='trail', entry=None,
                 stop=lows[-2] - 0.25 * atr, atr=atr)
    return r


def three_inside_up(opens, highs, lows, closes) -> Dict:
    """Large red -> small green inside prior body -> green closing above candle 1 open."""
    r = _base_result()
    if len(closes) < 4: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1o, c1c = opens[-3], closes[-3]
    c2o, c2c = opens[-2], closes[-2]
    c3o, c3c = opens[-1], closes[-1]
    c1_red = c1c < c1o
    c2_inside = c2o >= c1c and c2c <= c1o and abs(c2c - c2o) < abs(c1o - c1c)
    c3_green = c3c > c3o and c3c > c1o
    if c1_red and c2_inside and c3_green and is_downswing(highs, atr):
        r.update(found=True, direction='bullish', entry=c3c,
                 stop=min(lows[-3], lows[-2], lows[-1]) - 0.25 * atr, atr=atr)
    return r


def tweezer_bottom(opens, highs, lows, closes) -> Dict:
    """Two consecutive candles with matching lows (within 0.1% ATR), after downswing."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    l1, l2 = lows[-2], lows[-1]
    if abs(l1 - l2) <= 0.1 * atr and is_downswing(highs, atr):
        c2_green = closes[-1] > opens[-1]
        if c2_green:
            r.update(found=True, direction='bullish', entry=closes[-1],
                     stop=min(l1, l2) - 0.25 * atr, atr=atr, valid_for=2)
    return r


def bullish_harami(opens, highs, lows, closes) -> Dict:
    """Large red -> small green body inside prior body, after downswing."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    po, pc = opens[-2], closes[-2]
    co, cc = opens[-1], closes[-1]
    if pc < po and cc > co and co > pc and cc < po and abs(cc - co) < abs(po - pc) * 0.6 and is_downswing(highs, atr):
        r.update(found=True, direction='bullish', entry=cc,
                 stop=min(lows[-2], lows[-1]) - 0.25 * atr, atr=atr, valid_for=2)
    return r


def bullish_marubozu(opens, highs, lows, closes) -> Dict:
    """Large green candle with no upper/lower wick (close=high, open=low)."""
    r = _base_result()
    if len(closes) < 2: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o)
    rng = h - l
    if rng == 0: return r
    if c > o and body >= rng * 0.9:
        r.update(found=True, direction='bullish', entry=c,
                 stop=l - 0.25 * atr, atr=atr)
    return r


def bullish_abandoned_baby(opens, highs, lows, closes) -> Dict:
    """Red -> doji gap down -> green gap up. Rare reversal pattern."""
    r = _base_result()
    if len(closes) < 4: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1_red = closes[-3] < opens[-3]
    c2_doji = abs(closes[-2] - opens[-2]) <= (highs[-2] - lows[-2]) * 0.1
    c2_gap_down = highs[-2] < lows[-3]
    c3_green = closes[-1] > opens[-1]
    c3_gap_up = lows[-1] > highs[-2]
    if c1_red and c2_doji and c2_gap_down and c3_green and c3_gap_up:
        r.update(found=True, direction='bullish', entry=closes[-1],
                 stop=lows[-2] - 0.25 * atr, atr=atr)
    return r


def mat_hold(opens, highs, lows, closes) -> Dict:
    """Bullish continuation: large green, 3 small reds, then green breakout."""
    r = _base_result()
    if len(closes) < 6: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1_green = closes[-5] > opens[-5] and abs(closes[-5] - opens[-5]) > atr * 0.5
    pullback = all(closes[-4+i] < opens[-4+i] for i in range(3))
    within = all(lows[-4+i] > opens[-5] for i in range(3))
    c6_green = closes[-1] > opens[-1] and closes[-1] > closes[-5]
    if c1_green and pullback and within and c6_green and is_upswing(lows, atr):
        r.update(found=True, direction='bullish', entry=closes[-1],
                 stop=lows[-5] - 0.25 * atr, atr=atr)
    return r


def rising_three_methods(opens, highs, lows, closes) -> Dict:
    """Strong green, 3 small reds within range, then green continuation.

    RETIRED by D-284. The detector stays because it is test data and a
    cautionary record, but it is out of BULLISH_PATTERNS, so no strategy is
    built from it and no future sweep writes a graveyard row for it. See
    RETIRED_ENTRY_PATTERNS at the registry below for the full reasoning.
    """
    r = _base_result()
    if len(closes) < 6: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    big_green = closes[-5] > opens[-5] and (highs[-5] - lows[-5]) > atr
    # D-278 (Raven ruling, 2026-08-17): 0.7 -> 1.0. "Small" red candles were
    # required to be under 0.7 ATR while the big green leg only had to exceed
    # 1.0 ATR, so the pattern demanded a range CONTRACTION the textbook
    # definition does not ask for and effectively never fired. 1.0 ATR reads
    # the condition as written: the pullback candles are not larger than
    # average range. Loosening a filter can only ADD signals, so convention 17
    # applies to whatever comes back - firing is not edge.
    small_reds = all(closes[-4+i] < opens[-4+i] and
                     (highs[-4+i] - lows[-4+i]) < atr * 1.0 for i in range(3))
    within_range = all(highs[-4+i] < highs[-5] and lows[-4+i] > lows[-5] for i in range(3))
    continuation = closes[-1] > opens[-1] and closes[-1] > closes[-5]
    if big_green and small_reds and within_range and continuation and is_upswing(lows, atr):
        r.update(found=True, direction='bullish', entry=closes[-1],
                 stop=lows[-5] - 0.25 * atr, atr=atr)
    return r


def upside_tasuki_gap(opens, highs, lows, closes) -> Dict:
    """Two greens with gap up, then red filling the gap partially."""
    r = _base_result()
    if len(closes) < 4: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1_green = closes[-3] > opens[-3]
    c2_green = closes[-2] > opens[-2]
    gap = opens[-2] > closes[-3]
    c3_red = closes[-1] < opens[-1]
    # Candle 3 opens inside candle 2's body and closes INTO the gap without
    # fully filling it. (The old comparisons were swapped, which made the
    # condition unsatisfiable - this pattern could never fire.)
    opens_in_c2_body = opens[-2] <= opens[-1] <= closes[-2]
    partial_fill = closes[-3] < closes[-1] < opens[-2]
    if c1_green and c2_green and gap and c3_red and opens_in_c2_body and partial_fill and is_upswing(lows, atr):
        r.update(found=True, direction='bullish', entry=closes[-1],
                 stop=lows[-1] - 0.25 * atr, atr=atr, valid_for=2)
    return r


def on_neck(opens, highs, lows, closes) -> Dict:
    """Red candle, then green closing near prior low (continuation in downtrend, weak bullish)."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1_red = closes[-2] < opens[-2]
    c2_green = closes[-1] > opens[-1]
    near_low = abs(closes[-1] - lows[-2]) <= 0.1 * atr
    if c1_red and c2_green and near_low and is_downswing(highs, atr):
        r.update(found=True, direction='bullish', entry=closes[-1],
                 stop=lows[-2] - 0.5 * atr, atr=atr, valid_for=2)
    return r


def in_neck(opens, highs, lows, closes) -> Dict:
    """Red candle, then green closing slightly into prior body (weak bullish)."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1_red = closes[-2] < opens[-2]
    c2_green = closes[-1] > opens[-1]
    into_body = opens[-2] > closes[-1] > closes[-2]
    if c1_red and c2_green and into_body and is_downswing(highs, atr):
        r.update(found=True, direction='bullish', entry=closes[-1],
                 stop=lows[-2] - 0.5 * atr, atr=atr, valid_for=2)
    return r


# ============ BEARISH PATTERNS ============

def bearish_engulfing(opens, highs, lows, closes) -> Dict:
    """Red body fully engulfs prior green body, after upswing."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    po, pc = opens[-2], closes[-2]
    co, cc = opens[-1], closes[-1]
    if pc > po and cc < co and co >= pc and cc <= po and is_upswing(lows, atr):
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def shooting_star(opens, highs, lows, closes) -> Dict:
    """Upper wick >= 2x body, close in bottom third, after upswing."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    uw = h - max(o, c); lw = min(o, c) - l
    if uw >= 2 * body and body > 0 and c <= l + rng / 3 and lw < body and is_upswing(lows, atr):
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def hanging_man(opens, highs, lows, closes) -> Dict:
    """Lower wick >= 2x body in an uptrend (bearish warning)."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    lw = min(o, c) - l; uw = h - max(o, c)
    if lw >= 2 * body and body > 0 and uw < body and is_upswing(lows, atr):
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def evening_star(opens, highs, lows, closes) -> Dict:
    """Green -> small body -> red closing below midpoint of candle 1."""
    r = _base_result()
    if len(closes) < 4: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1o, c1c = opens[-3], closes[-3]
    c2o, c2c = opens[-2], closes[-2]
    c3o, c3c = opens[-1], closes[-1]
    c1_green = c1c > c1o
    c2_small = abs(c2c - c2o) < abs(c1c - c1o) * 0.5
    c2_higher = c2c > c1c
    c3_red = c3c < c3o
    c3_below = c3c < (c1o + c1c) / 2
    if c1_green and c2_small and c2_higher and c3_red and c3_below and is_upswing(lows, atr):
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def dark_cloud_cover(opens, highs, lows, closes) -> Dict:
    """Red opens above prior green high, closes below its midpoint."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    po, pc = opens[-2], closes[-2]
    co, cc = opens[-1], closes[-1]
    if pc > po and cc < co and co > highs[-2] and cc < (po + pc) / 2 and is_upswing(lows, atr):
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def three_black_crows(opens, highs, lows, closes) -> Dict:
    """3 consecutive red candles, each closing near its low."""
    r = _base_result()
    if len(closes) < 4: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    all_red = closes[-3] < opens[-3] and closes[-2] < opens[-2] and closes[-1] < opens[-1]
    falling = closes[-3] > closes[-2] > closes[-1]
    def near_low(o, c, h, l):
        rng = h - l
        return rng > 0 and c <= l + rng * 0.25
    nl = (near_low(opens[-3], closes[-3], highs[-3], lows[-3]) and
          near_low(opens[-2], closes[-2], highs[-2], lows[-2]) and
          near_low(opens[-1], closes[-1], highs[-1], lows[-1]))
    if all_red and falling and nl and is_upswing(lows, atr):
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def three_inside_down(opens, highs, lows, closes) -> Dict:
    """Large green -> small red inside -> red closing below candle 1 low."""
    r = _base_result()
    if len(closes) < 4: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1_green = closes[-3] > opens[-3]
    c2_inside = opens[-2] <= closes[-3] and closes[-2] >= opens[-3] and abs(closes[-2] - opens[-2]) < abs(opens[-3] - closes[-3]) * 0.6
    c3_red = closes[-1] < opens[-1] and closes[-1] < closes[-3]
    if c1_green and c2_inside and c3_red and is_upswing(lows, atr):
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def tweezer_top(opens, highs, lows, closes) -> Dict:
    """Two consecutive candles with matching highs, after upswing."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    h1, h2 = highs[-2], highs[-1]
    if abs(h1 - h2) <= 0.1 * atr and is_upswing(lows, atr):
        c2_red = closes[-1] < opens[-1]
        if c2_red:
            r.update(found=True, direction='bearish', entry=None,
                     stop=None, atr=atr, action='close_long')
    return r


def bearish_harami(opens, highs, lows, closes) -> Dict:
    """Large green -> small red body inside prior body, after upswing."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    po, pc = opens[-2], closes[-2]
    co, cc = opens[-1], closes[-1]
    if pc > po and cc < co and co < pc and cc > po and abs(cc - co) < abs(po - pc) * 0.6 and is_upswing(lows, atr):
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def bearish_marubozu(opens, highs, lows, closes) -> Dict:
    """Large red candle, close=low, open=high."""
    r = _base_result()
    if len(closes) < 2: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    if c < o and body >= rng * 0.9:
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def bearish_abandoned_baby(opens, highs, lows, closes) -> Dict:
    """Green -> doji gap up -> red gap down. Rare bearish reversal."""
    r = _base_result()
    if len(closes) < 4: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1_green = closes[-3] > opens[-3]
    c2_doji = abs(closes[-2] - opens[-2]) <= (highs[-2] - lows[-2]) * 0.1
    c2_gap_up = lows[-2] > highs[-3]
    c3_red = closes[-1] < opens[-1]
    c3_gap_down = highs[-1] < lows[-2]
    if c1_green and c2_doji and c2_gap_up and c3_red and c3_gap_down:
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def falling_three_methods(opens, highs, lows, closes) -> Dict:
    """Strong red, 3 small greens within range, then red continuation."""
    r = _base_result()
    if len(closes) < 6: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    big_red = closes[-5] < opens[-5] and (highs[-5] - lows[-5]) > atr
    small_greens = all(closes[-4+i] > opens[-4+i] and
                       (highs[-4+i] - lows[-4+i]) < atr * 0.7 for i in range(3))
    within = all(highs[-4+i] < highs[-5] and lows[-4+i] > lows[-5] for i in range(3))
    continuation = closes[-1] < opens[-1] and closes[-1] < closes[-5]
    if big_red and small_greens and within and continuation:
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def downside_tasuki_gap(opens, highs, lows, closes) -> Dict:
    """Two reds with gap down, then green partially filling gap."""
    r = _base_result()
    if len(closes) < 4: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    c1_red = closes[-3] < opens[-3]
    c2_red = closes[-2] < opens[-2]
    gap = opens[-2] < closes[-3]
    c3_green = closes[-1] > opens[-1]
    # Candle 3 opens inside candle 2's body and closes up INTO the gap without
    # fully filling it. (Old comparisons were swapped - never fired.)
    opens_in_c2_body = closes[-2] <= opens[-1] <= opens[-2]
    partial_fill = opens[-2] < closes[-1] < closes[-3]
    if c1_red and c2_red and gap and c3_green and opens_in_c2_body and partial_fill:
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


def gravestone_doji(opens, highs, lows, closes) -> Dict:
    """Doji with long upper wick and no lower wick. Bearish at top."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    uw = h - max(o, c); lw = min(o, c) - l
    if body <= rng * 0.1 and uw >= rng * 0.7 and lw <= rng * 0.1 and is_upswing(lows, atr):
        r.update(found=True, direction='bearish', entry=None,
                 stop=None, atr=atr, action='close_long')
    return r


# ============ NEUTRAL / CONTINUATION PATTERNS ============

def doji(opens, highs, lows, closes) -> Dict:
    """Body <= 10% of range. Blocks new entries."""
    r = _base_result()
    if len(closes) < 2: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng > 0 and body <= rng * 0.10:
        r.update(found=True, direction='neutral', atr=atr, action='block_entries')
    return r


def dragonfly_doji(opens, highs, lows, closes) -> Dict:
    """Doji with long lower wick, no upper wick. Bullish at support."""
    r = _base_result()
    if len(closes) < 3: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    lw = min(o, c) - l; uw = h - max(o, c)
    if body <= rng * 0.1 and lw >= rng * 0.7 and uw <= rng * 0.1:
        if is_downswing(highs, atr):
            r.update(found=True, direction='bullish', entry=h,
                     stop=l - 0.25 * atr, atr=atr, valid_for=2)
        else:
            r.update(found=True, direction='neutral', atr=atr, action='block_entries')
    return r


def long_legged_doji(opens, highs, lows, closes) -> Dict:
    """Doji with long wicks both sides. Indecision."""
    r = _base_result()
    if len(closes) < 2: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    if body <= rng * 0.1 and rng > atr:
        r.update(found=True, direction='neutral', atr=atr, action='block_entries')
    return r


def spinning_top(opens, highs, lows, closes) -> Dict:
    """Small body with wicks on both sides. Indecision."""
    r = _base_result()
    if len(closes) < 2: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    if body > rng * 0.1 and body < rng * 0.5:
        uw = h - max(o, c); lw = min(o, c) - l
        if uw > body and lw > body:
            r.update(found=True, direction='neutral', atr=atr, action='block_entries')
    return r


def high_wave(opens, highs, lows, closes) -> Dict:
    """Very small body with very long wicks both sides. Extreme indecision."""
    r = _base_result()
    if len(closes) < 2: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    if body < rng * 0.15 and rng > atr * 1.5:
        r.update(found=True, direction='neutral', atr=atr, action='block_entries')
    return r


def rickshaw_man(opens, highs, lows, closes) -> Dict:
    """Long-legged doji with body in the middle of the range."""
    r = _base_result()
    if len(closes) < 2: return r
    atr = latest_atr(highs, lows, closes, 14)
    if atr == 0: return r
    o, h, l, c = opens[-1], highs[-1], lows[-1], closes[-1]
    body = abs(c - o); rng = h - l
    if rng == 0: return r
    mid = (h + l) / 2
    if body <= rng * 0.15 and abs((o + c) / 2 - mid) < rng * 0.1:
        r.update(found=True, direction='neutral', atr=atr, action='block_entries')
    return r


# ============ PATTERN REGISTRY ============

# D-284 (Raven ruling, 2026-08-17): entry patterns whose detector is kept but
# which are OUT of the active strategy set. Being in this set is the whole
# mechanism - `strategies/builtin/expanded.py` builds one strategy per entry in
# BULLISH_PATTERNS, so a name absent from that dict produces no strategy, and a
# strategy that does not exist produces no graveyard row.
#
# `rising_three_methods`: D-278 loosened its binding `small_reds` clause from
# 0.7 to 1.0 ATR, which unblocked that clause 8.6x (70 -> 600 hits), and the
# pattern STILL fired zero times - `within_range` became binding at 1.03% of
# bars and the two co-occur on 5 of 13,901 bars, none of which satisfy the
# remaining clauses. That met D-278's stated kill condition. Its prior graveyard
# rows are NOT_TESTED, never FAIL: it could never fire, so it was never tested
# (convention 11).
RETIRED_ENTRY_PATTERNS = frozenset({'rising_three_methods'})

BULLISH_PATTERNS = {
    'bullish_engulfing': bullish_engulfing,
    'hammer': hammer,
    'inverted_hammer': inverted_hammer,
    'morning_star': morning_star,
    'piercing_line': piercing_line,
    'three_inside_up': three_inside_up,
    'tweezer_bottom': tweezer_bottom,
    'bullish_harami': bullish_harami,
    'bullish_marubozu': bullish_marubozu,
    'bullish_abandoned_baby': bullish_abandoned_baby,
    'mat_hold': mat_hold,
    # rising_three_methods RETIRED by D-284 - see RETIRED_ENTRY_PATTERNS above.
    # The detector function is still defined and still importable.
    'upside_tasuki_gap': upside_tasuki_gap,
    # on_neck / in_neck REMOVED from the bullish entry registry: the canonical
    # on-neck/in-neck lines are BEARISH CONTINUATION patterns (a failed rally
    # in a downtrend). Registering them as long entries bought straight into
    # confirmed downtrends. The detector functions remain available for study.
    'dragonfly_doji': dragonfly_doji,  # bullish at support
}

BEARISH_PATTERNS = {
    'bearish_engulfing': bearish_engulfing,
    'shooting_star': shooting_star,
    'hanging_man': hanging_man,
    'evening_star': evening_star,
    'dark_cloud_cover': dark_cloud_cover,
    'three_black_crows': three_black_crows,
    'three_inside_down': three_inside_down,
    'tweezer_top': tweezer_top,
    'bearish_harami': bearish_harami,
    'bearish_marubozu': bearish_marubozu,
    'bearish_abandoned_baby': bearish_abandoned_baby,
    'falling_three_methods': falling_three_methods,
    'downside_tasuki_gap': downside_tasuki_gap,
    'gravestone_doji': gravestone_doji,
}

NEUTRAL_PATTERNS = {
    'doji': doji,
    'long_legged_doji': long_legged_doji,
    'spinning_top': spinning_top,
    'high_wave': high_wave,
    'rickshaw_man': rickshaw_man,
}

ALL_PATTERNS = {**BULLISH_PATTERNS, **BEARISH_PATTERNS, **NEUTRAL_PATTERNS}
