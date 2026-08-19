"""Market mapper: a declared play -> the ONE Polymarket market it maps to, or None.

Proposal 027's own text calls this "the blocker": a caller declaring "MRVL
puts, 9/25" is not a Polymarket order until something confirms Polymarket has
a tradeable market on MRVL that resolves on that date, with real liquidity,
that is not closed. This module is that confirmation, and nothing else. It
does not place an order, does not rank markets by attractiveness, and does not
widen a mapping to force a match - "no market maps" is a valid, common, and
CORRECT answer (`no_mapped_market_for_caller_play`, spelled exactly as the
proposal spells it so the reason is greppable against the proposal text).

## Why this takes a market LIST, not a client

`map_declared_play_checked` is a pure function: `(play, markets) -> result`.
It does not fetch anything itself, for the same reason `MarketContext` in
`base.py` is "a plain data bag rather than a live client handle" - a function
that fetches mid-decision cannot be replayed from a logged input, and a unit
test that needs live Gamma access is not a unit test.

Two different callers use the SAME function two different ways:

  - `strategies/polymarket/smart_money_callers.py` calls it with a
    ONE-ELEMENT list, `[ctx.market]`, once per shadow-loop cycle: "does the
    market I am looking at right now happen to be the one this play maps
    to?" This is the live path, and it needs no network call inside
    `evaluate()` at all - every strategy in this package works this way (see
    `base.py`'s module docstring on why `MarketContext` never carries a live
    client).
  - A future batch job (or a test) can call it with the FULL result of
    `engine.polymarket.markets.search_markets(client, ticker)` to find a
    caller's play among many candidates at once. Nothing here assumes which
    caller is used; both are the same function.

## Which Gamma search actually returns stock-price markets

Read `engine/polymarket/markets.py` before believing this without checking it
yourself, but here is what it says: `search_event_markets` (and its `_checked`
twin) return Gamma's highest-VOLUME markets, unfiltered by category - "high
-volume general event markets discovered by dollar volume." `search_sports_
markets` and `search_political_markets` walk `/events?tag_slug=` over FIXED
tag lists (`SPORTS_TAG_SLUGS`, `POLITICAL_TAG_SLUGS`) - NFL, NBA, elections,
the Fed, and so on. Neither tag list has anything resembling a stock-ticker
tag, and a single-stock price market ("Will MRVL close above $80 by 9/25?") is
not a sports or a political event by any reading of those lists.

So a stock-price market is either found by full-text search
(`engine.polymarket.markets.search_markets(client, ticker)`, which hits
Gamma's `/public-search`) or it shows up in the general high-volume feed if it
is popular enough to clear `DEFAULT_MIN_EVENT_VOLUME_USDC`. Both routes land a
`Market` in the `event` bucket, never `sports` or `political`. This is why
`SmartMoneyCallers.supported_market_types` in `smart_money_callers.py` is
narrowed to `(MARKET_TYPE_EVENT,)` rather than the full
`GENERAL_BINARY_MARKET_TYPES` - see that module's docstring for the ruling.

## Matching, one condition at a time, each with its own drop reason

Four conditions, matching the proposal's own `entry_exit_rules` text
verbatim: (a) same underlying, (b) resolution date within the declared
window, (c) liquidity floor, (d) not closed and active. `map_declared_play_
checked` evaluates them IN ORDER for every ticker-matching candidate and
counts every rejection by cause (convention 20) - a market can be dropped for
exactly one reason per pass, attributed to the first condition it fails, so
a market that is BOTH illiquid and closed is counted once (closed is checked
first: a closed market can never become tradeable regardless of its volume,
so that is the more informative reason to report).

## Ambiguity is refused, never resolved by a tiebreak

If MORE THAN ONE market survives every gate, this is NOT resolved by picking
the highest-volume one. The proposal is explicit: "If Polymarket has no MRVL
contract on that expiry, the play is skipped, not improvised." Two markets
both surviving means we cannot tell which one the caller meant either, and
guessing is the exact failure mode this module exists to prevent. Both routes
- zero survivors and multiple survivors - collapse to the SAME external
reason, `no_mapped_market_for_caller_play`, because from the caller's
perspective "no market maps" and "we cannot tell which market maps" both mean
"do not trade." The distinction IS preserved internally, in `drops`, for
anyone auditing why.
"""
import logging
from datetime import date
from typing import Dict, List, Optional, Sequence, Tuple

from engine.polymarket.markets import DEFAULT_MIN_EVENT_VOLUME_USDC
from engine.polymarket.types import Market

logger = logging.getLogger(__name__)

#: The proposal's own wording, reused verbatim rather than re-spelled, so a
#: grep for the proposal text finds this constant.
NO_MAPPED_MARKET_REASON = 'no_mapped_market_for_caller_play'

#: How close a market's `end_date` must land to the declared expiry to count
#: as "the same date". NOT an economic tolerance - it exists purely to absorb
#: day-boundary/timezone rounding between "the options expiry the caller
#: named" and however Gamma stamps `endDate` (often a UTC midnight that can
#: land on the calendar day before or after the naive local date). Convention
#: 17: an assumption with an expiry date, not a measurement; 1 day was chosen
#: because it is the smallest window that absorbs a single timezone rollover
#: without also absorbing an entirely different expiry cycle (options expire
#: weekly at the nearest, so a wider window risks matching the WRONG week's
#: contract).
EXPIRY_TOLERANCE_DAYS = 1

#: Every reason a ticker-matching candidate does not survive to become the
#: mapped market. Named so no two causes share a counter (convention 20).
MAP_DROP_REASONS = (
    'ticker_mismatch',
    'closed',
    'inactive',
    'market_end_date_unreadable',
    'declared_expiry_unreadable',
    'expiry_out_of_window',
    'volume_unreadable',
    'volume_below_floor',
    'ambiguous_multiple_survivors',
)


def _ticker_matches_text(ticker: str, text: Optional[str]) -> bool:
    """Is `ticker` a clearly-delimited token inside `text`?

    Case-insensitive, but bounded on both sides by a non-alphanumeric
    character (or a string edge) so 'F' cannot match inside 'FOMC' and 'MRVL'
    cannot match inside 'MRVLX'. This is the same discipline
    `strategies/polymarket/caller_feed.py`'s `TICKER_RE` applies to the
    SOURCE text; here it applies to the CANDIDATE market's own slug/question,
    which is a different string with different false-positive risks (a slug
    is hyphen-joined lowercase words, e.g. `will-mrvl-close-above-80-on-9-25`).
    """
    if not text or not ticker:
        return False
    t = ticker.strip().lower()
    if not t:
        return False
    hay = text.lower()
    start = 0
    while True:
        idx = hay.find(t, start)
        if idx == -1:
            return False
        before = hay[idx - 1] if idx > 0 else ''
        after_idx = idx + len(t)
        after = hay[after_idx] if after_idx < len(hay) else ''
        if not before.isalnum() and not after.isalnum():
            return True
        start = idx + 1


def market_matches_ticker(market: Market, ticker: str) -> bool:
    """Does this market's slug OR question name `ticker` as a clean token?"""
    return (_ticker_matches_text(ticker, getattr(market, 'slug', None)) or
           _ticker_matches_text(ticker, getattr(market, 'question', None)))


def _parse_iso_date(value) -> Optional[date]:
    """A Gamma-style date string (with or without a time component) -> date.

    None for anything that does not parse. Gamma's `endDate` has been
    observed as an ISO-8601 datetime with a trailing 'Z'; only the date
    component is used here because option/event resolution is a calendar-day
    concept and a market's exact settlement HOUR is not what a Reddit post's
    "9/25" is claiming to predict.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    v = value.strip()
    if 'T' in v:
        v = v.split('T', 1)[0]
    try:
        return date.fromisoformat(v[:10])
    except ValueError:
        return None


def within_expiry_window(market_end_date, declared_expiry,
                         tolerance_days: int = EXPIRY_TOLERANCE_DAYS
                         ) -> bool:
    """Do the market's resolution date and the declared expiry line up?

    Both arguments are date-ish strings, parsed independently so a caller can
    tell WHICH side was unreadable (see `map_declared_play_checked`, which
    calls `_parse_iso_date` itself for that reason rather than trusting this
    function's boolean alone).
    """
    end = _parse_iso_date(market_end_date)
    declared = _parse_iso_date(declared_expiry)
    if end is None or declared is None:
        return False
    return abs((end - declared).days) <= int(tolerance_days)


def map_declared_play_checked(
        play, markets: Sequence[Market],
        min_volume_usdc: float = DEFAULT_MIN_EVENT_VOLUME_USDC,
        tolerance_days: int = EXPIRY_TOLERANCE_DAYS) -> Dict[str, object]:
    """The full accounting. Returns a dict, never raises on a bad candidate.

    `{'market': Market or None, 'reason': str or None, 'drops': {...},
      'ticker_matches': int}`.

    `reason` is `None` on a match and `NO_MAPPED_MARKET_REASON` on every
    refusal path, INCLUDING the ambiguous-survivors case - see the module
    docstring's "Ambiguity is refused" section for why that collapse is
    deliberate. `drops` still distinguishes every internal cause, so a test
    (or an operator) can tell a zero-candidate ticker miss from an ambiguous
    multi-market hit without needing a second reason string threaded through
    the strategy layer.
    """
    floor = float(min_volume_usdc)
    drops: Dict[str, int] = {}

    def _bump(reason: str) -> None:
        drops[reason] = drops.get(reason, 0) + 1

    ticker_candidates: List[Market] = []
    for m in markets or ():
        if m is None:
            continue
        if not market_matches_ticker(m, play.ticker):
            _bump('ticker_mismatch')
            continue
        ticker_candidates.append(m)

    survivors: List[Market] = []
    for m in ticker_candidates:
        if getattr(m, 'closed', False):
            _bump('closed')
            continue
        if not getattr(m, 'active', True):
            _bump('inactive')
            continue
        end = _parse_iso_date(getattr(m, 'end_date', None))
        if end is None:
            _bump('market_end_date_unreadable')
            continue
        declared = _parse_iso_date(getattr(play, 'expiry', None))
        if declared is None:
            _bump('declared_expiry_unreadable')
            continue
        if abs((end - declared).days) > int(tolerance_days):
            _bump('expiry_out_of_window')
            continue
        volume = getattr(m, 'volume', None)
        if volume is None:
            _bump('volume_unreadable')
            continue
        if volume <= floor:
            _bump('volume_below_floor')
            continue
        survivors.append(m)

    if not survivors:
        return {'market': None, 'reason': NO_MAPPED_MARKET_REASON,
                'drops': dict(drops), 'ticker_matches': len(ticker_candidates)}

    if len(survivors) > 1:
        drops['ambiguous_multiple_survivors'] = len(survivors) - 1
        return {'market': None, 'reason': NO_MAPPED_MARKET_REASON,
                'drops': dict(drops), 'ticker_matches': len(ticker_candidates)}

    return {'market': survivors[0], 'reason': None, 'drops': dict(drops),
            'ticker_matches': len(ticker_candidates)}


def map_declared_play(play, markets: Sequence[Market],
                      min_volume_usdc: float = DEFAULT_MIN_EVENT_VOLUME_USDC,
                      tolerance_days: int = EXPIRY_TOLERANCE_DAYS
                      ) -> Tuple[Optional[Market], Optional[str]]:
    """The plain variant. `(market_or_None, reason_or_None)`.

    Prefer `map_declared_play_checked` when the caller needs the drop
    accounting (tests, audit rows); this exists for a call site that only
    needs the answer.
    """
    result = map_declared_play_checked(play, markets, min_volume_usdc,
                                       tolerance_days)
    return result['market'], result['reason']
