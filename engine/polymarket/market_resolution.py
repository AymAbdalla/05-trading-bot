"""Cached, read-only lookup of "how did this market actually resolve?".

Built for `smart_money_copy`, which has to score a tracked wallet's past fills
and cannot, because `data-api /trades` returns FILLS and never OUTCOMES. The
missing half is here: given a `conditionId` off a trade row, say which token
paid $1.00 and which paid $0.00, or say that we do not know yet.

## The endpoint, verified against live responses on 2026-08-18

**`GET https://clob.polymarket.com/markets/<conditionId>`** is the source.

```
GET https://clob.polymarket.com/markets/0xf718dcad88302f83da8e6a6b49e2e7247710f8ddad25a763ec39186c2a041b9b
-> HTTP 200
{
 "condition_id": "0xf718dcad...",
 "market_slug": "btc-updown-15m-1787064300",
 "closed": true,
 "tokens": [
   {"token_id": "79913803...427", "outcome": "Up",   "price": 0, "winner": false},
   {"token_id": "27906843...296", "outcome": "Down", "price": 1, "winner": true}
 ]
}
```

`tokens[].token_id` is character-for-character the `asset` field on the Data API
trade row, so a fill can be scored WITHOUT ever comparing outcome strings. That
matters: `outcome` on the trade row is a display string and matching on it is
how a scorer silently inverts itself.

**Gamma is NOT the source, despite looking like it is.** `GET
gamma-api.polymarket.com/markets?condition_ids=<cond>` returns HTTP 200 with an
EMPTY LIST for the short-window crypto Up/Down markets this repo trades. Measured
on a sample of 8 distinct condition ids off one wallet's tape: **CLOB answered
8/8, Gamma answered 1/8**, and the one it answered was the only market still
open. A 200 carrying `[]` is exactly the failure mode convention 11 is about -
it reads like "no resolution" and it means "wrong endpoint".

## `closed` and `winner` are two separate facts and both are required

An OPEN market returns `closed: false` with `winner: false` on EVERY token. That
is observed, not hypothesised - the 8AM-12PM BTC market in the same sample
returned exactly that while trading at 0.9985.

So `winner is False` NEVER means "this outcome lost". It means "this outcome is
not known to have won". A scorer that reads a falsy `winner` as a loss marks
every open position as a loss and manufactures a win rate out of market
duration. `resolved` here is True only when:

  - `closed` is exactly True, AND
  - EXACTLY ONE token carries `winner is True`, AND
  - the winner and loser token-id sets are disjoint and both non-empty.

Anything else is `resolved=False` with a NAMED status, and a named status is the
caller's cue to count it as NOT_TESTED rather than as a loss (convention 11).
Every status gets its own counter; none of them share one (convention 20).

## Caching

A resolved market is IMMUTABLE. Once the chain says Down paid $1.00 that is not
going to change, so a resolved entry is cached with no expiry at all. Everything
else - open markets, unreadable payloads, transport failures - is cached with a
SHORT ttl and its own counter, because those are all states that can flip.

This distinction is the whole point of the cache. The shadow loop polls every
five seconds and a wallet's history is hundreds of rows deep; without an
unexpiring resolved-tier the strategy would re-ask the CLOB for a settled
15-minute market that ended three hours ago, forever.

Keyed by condition id and therefore SHARED across wallets by construction. Seven
tracked whales trading the same BTC Up/Down 5-minute market is seven cache hits
on one fetch.

Bounded: `max_entries` evicts oldest-inserted first, and eviction is counted.
An unbounded cache in a process that runs for days is a slow leak wearing a
performance optimisation's name.

## What this module cannot do

  - **NO WRITES, NO ORDERS.** It builds a path and reads a response, exactly
    like `engine/polymarket/client.py`. There is no signer here.
  - It cannot tell you the wallet's REALIZED pnl. It tells you what the market
    paid. What a wallet actually banked depends on whether they still held at
    resolution, and `/trades` cannot answer that - see the note on SELL rows in
    `strategies/polymarket/smart_money_copy.py`.
"""
import logging
import threading
import time
from collections import Counter, OrderedDict
from dataclasses import dataclass
from typing import Dict, FrozenSet, List, Optional, Tuple

logger = logging.getLogger(__name__)

CLOB_HOST = 'https://clob.polymarket.com'
CLOB_MARKET_PATH = '/markets/'

#: How long an entry that is NOT resolved stays cached. Short: an open market
#: becomes a resolved one, and a 5-minute Up/Down market does it fast.
DEFAULT_PENDING_TTL_SEC = 120.0

#: How long a FAILED lookup stays cached. Shorter still, and separate from the
#: pending ttl on purpose: "the market is open" and "the request did not come
#: back" are different facts and must not share a retry policy (convention 20).
DEFAULT_FAILURE_TTL_SEC = 30.0

#: Cache ceiling. ~5k settled binaries is far more history than any wallet
#: record needs and small enough to be free.
DEFAULT_MAX_ENTRIES = 5000

# -- statuses. Exactly one is attached to every lookup. -----------------------

STATUS_RESOLVED = 'resolved'
STATUS_NOT_CLOSED = 'not_closed'
STATUS_NO_WINNER = 'no_winner'
STATUS_MULTIPLE_WINNERS = 'multiple_winners'
STATUS_NO_TOKENS = 'no_tokens'
STATUS_INCONSISTENT_TOKENS = 'inconsistent_tokens'
STATUS_BAD_PAYLOAD = 'bad_payload'
STATUS_FETCH_FAILED = 'fetch_failed'

#: Every status a lookup can carry. A new one must be added here or
#: `MarketResolutionCache.census()` will refuse to balance.
RESOLUTION_STATUSES = (
    STATUS_RESOLVED, STATUS_NOT_CLOSED, STATUS_NO_WINNER,
    STATUS_MULTIPLE_WINNERS, STATUS_NO_TOKENS, STATUS_INCONSISTENT_TOKENS,
    STATUS_BAD_PAYLOAD, STATUS_FETCH_FAILED,
)


@dataclass(frozen=True)
class MarketResolution:
    """What a market paid, or a named reason we do not know.

    `resolved` is the only field a caller may branch on to decide "this trade
    can be scored". Reading `winning_token_ids` without checking `resolved`
    would treat an open market's empty winner set as "everything lost".
    """

    condition_id: str
    closed: bool
    resolved: bool
    status: str
    winning_token_ids: FrozenSet[str] = frozenset()
    losing_token_ids: FrozenSet[str] = frozenset()
    winning_outcomes: FrozenSet[str] = frozenset()
    losing_outcomes: FrozenSet[str] = frozenset()
    market_slug: Optional[str] = None
    source: str = 'clob_markets'

    # -- verdicts -----------------------------------------------------------

    def verdict_for_token(self, token_id: Optional[str]) -> Optional[bool]:
        """True if that token paid $1.00, False if $0.00, None if UNKNOWN.

        None is returned for an unresolved market AND for a token that is not
        in this market at all. Both are "cannot score", and a caller that
        collapses None to False turns them into losses (convention 11).
        """
        if not self.resolved or not token_id:
            return None
        tok = str(token_id).strip()
        if tok in self.winning_token_ids:
            return True
        if tok in self.losing_token_ids:
            return False
        return None

    def verdict_for_outcome(self, outcome: Optional[str]) -> Optional[bool]:
        """Same, matched on the DISPLAY STRING. Weaker; use as a fallback only.

        Case-insensitive and whitespace-stripped. Token id is the strong key
        and is preferred everywhere; this exists because a Data API row is not
        guaranteed to carry `asset`.
        """
        if not self.resolved or not isinstance(outcome, str):
            return None
        name = outcome.strip().lower()
        if not name:
            return None
        if name in self.winning_outcomes:
            return True
        if name in self.losing_outcomes:
            return False
        return None

    def to_dict(self) -> dict:
        return {
            'condition_id': self.condition_id, 'closed': self.closed,
            'resolved': self.resolved, 'status': self.status,
            'market_slug': self.market_slug,
            'winning_token_ids': sorted(self.winning_token_ids),
            'losing_token_ids': sorted(self.losing_token_ids),
            'winning_outcomes': sorted(self.winning_outcomes),
            'losing_outcomes': sorted(self.losing_outcomes),
            'source': self.source,
        }


def _unresolved(condition_id: str, status: str, closed: bool = False,
                slug: Optional[str] = None) -> MarketResolution:
    return MarketResolution(condition_id=str(condition_id), closed=bool(closed),
                            resolved=False, status=status, market_slug=slug)


def resolution_from_clob(payload, condition_id: str) -> MarketResolution:
    """Parse one `clob /markets/<cond>` body into a MarketResolution.

    Never raises on a shape it does not recognise: an unexpected payload is a
    NAMED unresolved status, because a strategy calling this inside a poll loop
    must not die on a schema change, and must not silently treat one as data.
    """
    cid = str(condition_id)
    if not isinstance(payload, dict):
        return _unresolved(cid, STATUS_BAD_PAYLOAD)

    slug = payload.get('market_slug') or payload.get('slug')
    slug = str(slug) if slug else None

    # `closed` must be EXACTLY True. A missing key, a None, or the string
    # 'false' are all "not known to be closed", never a truthiness question.
    closed = payload.get('closed') is True

    tokens = payload.get('tokens')
    if not isinstance(tokens, list) or not tokens:
        return _unresolved(cid, STATUS_NO_TOKENS, closed=closed, slug=slug)

    win_toks, lose_toks = set(), set()
    win_names, lose_names = set(), set()
    for tok in tokens:
        if not isinstance(tok, dict):
            continue
        tid = tok.get('token_id')
        tid = str(tid).strip() if tid not in (None, '') else None
        name = tok.get('outcome')
        name = name.strip().lower() if isinstance(name, str) and name.strip() \
            else None
        # `winner is True`, not `bool(winner)`. An open market ships
        # `winner: false` on every token and a truthiness read of a missing
        # key would land in the same bucket as a real loss.
        if tok.get('winner') is True:
            if tid:
                win_toks.add(tid)
            if name:
                win_names.add(name)
        else:
            if tid:
                lose_toks.add(tid)
            if name:
                lose_names.add(name)

    if not closed:
        return _unresolved(cid, STATUS_NOT_CLOSED, closed=False, slug=slug)
    if not win_toks and not win_names:
        return _unresolved(cid, STATUS_NO_WINNER, closed=True, slug=slug)
    if len(win_toks) > 1 or len(win_names) > 1:
        # Two winners on a binary means we misread the payload. Refusing is
        # the only safe answer: picking one would be a coin flip that looks
        # like a measurement.
        return _unresolved(cid, STATUS_MULTIPLE_WINNERS, closed=True, slug=slug)
    if (win_toks & lose_toks) or (win_names & lose_names):
        return _unresolved(cid, STATUS_INCONSISTENT_TOKENS, closed=True,
                           slug=slug)
    if not lose_toks and not lose_names:
        # One token, and it won. There is nothing to lose against, so no fill
        # in this market can be scored as a loss and the sample is degenerate.
        return _unresolved(cid, STATUS_INCONSISTENT_TOKENS, closed=True,
                           slug=slug)

    return MarketResolution(
        condition_id=cid, closed=True, resolved=True, status=STATUS_RESOLVED,
        winning_token_ids=frozenset(win_toks),
        losing_token_ids=frozenset(lose_toks),
        winning_outcomes=frozenset(win_names),
        losing_outcomes=frozenset(lose_names),
        market_slug=slug)


class MarketResolutionCache:
    """Condition id -> MarketResolution, with a two-tier cache.

    Resolved entries never expire. Everything else expires fast. See the module
    docstring for why that split is the point rather than an optimisation.

    Thread safe. The lock is held around cache reads and writes ONLY, never
    across the HTTP call, so one slow request cannot block every other reader -
    the same discipline `engine/feeds/noaa_weather.py` uses.

    `client` is expected to be a `PolymarketClient`. Anything with a callable
    `.clob(path)` works, which is what the tests hand it. No client means no
    lookups: `get()` returns a `fetch_failed` resolution rather than reaching
    for the network itself. There is exactly one HTTP client in this repo and
    this is not a second one.
    """

    def __init__(self, client=None, pending_ttl_sec: float = DEFAULT_PENDING_TTL_SEC,
                 failure_ttl_sec: float = DEFAULT_FAILURE_TTL_SEC,
                 max_entries: int = DEFAULT_MAX_ENTRIES,
                 clock=None):
        self.client = client
        self.pending_ttl_sec = float(pending_ttl_sec)
        self.failure_ttl_sec = float(failure_ttl_sec)
        self.max_entries = max(1, int(max_entries))
        self._clock = clock or time.time
        #: One counter per distinct cause. Nothing is incremented twice for one
        #: lookup and nothing is dropped uncounted (convention 20).
        self.health: Counter = Counter()
        self._lock = threading.RLock()
        #: condition_id -> (stored_at, expires_at_or_None, MarketResolution).
        #: OrderedDict so eviction is oldest-inserted-first and deterministic.
        self._cache: 'OrderedDict[str, Tuple[float, Optional[float], MarketResolution]]' = \
            OrderedDict()

    # -- cache --------------------------------------------------------------

    def _cached(self, cid: str) -> Optional[MarketResolution]:
        now = self._clock()
        with self._lock:
            entry = self._cache.get(cid)
            if entry is None:
                self.health['cache_miss'] += 1
                return None
            _stored_at, expires_at, res = entry
            if expires_at is not None and now >= expires_at:
                del self._cache[cid]
                self.health['cache_expired'] += 1
                return None
            self.health['cache_hit_resolved' if res.resolved
                        else 'cache_hit_pending'] += 1
            return res

    def _store(self, cid: str, res: MarketResolution) -> None:
        now = self._clock()
        if res.resolved:
            # Immutable. No expiry, by design.
            expires_at = None
        elif res.status == STATUS_FETCH_FAILED:
            expires_at = now + self.failure_ttl_sec
        else:
            expires_at = now + self.pending_ttl_sec
        with self._lock:
            self._cache.pop(cid, None)
            self._cache[cid] = (now, expires_at, res)
            while len(self._cache) > self.max_entries:
                self._cache.popitem(last=False)
                self.health['cache_evicted'] += 1

    def invalidate(self, condition_id: Optional[str] = None) -> None:
        """Drop one entry, or all of them. For tests and forced refreshes."""
        with self._lock:
            if condition_id is None:
                self._cache.clear()
            else:
                self._cache.pop(str(condition_id), None)

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)

    # -- lookup -------------------------------------------------------------

    def get(self, condition_id: Optional[str]) -> MarketResolution:
        """Resolution for one condition id. NEVER None, NEVER raises.

        A caller always gets a MarketResolution; when we could not determine
        anything it carries `resolved=False` and a named status. Returning None
        here would push a second "is it missing or is it unresolved" branch onto
        every caller for no information gain.
        """
        if not condition_id:
            self.health['asked_without_condition_id'] += 1
            return _unresolved('', STATUS_BAD_PAYLOAD)
        cid = str(condition_id).strip()

        cached = self._cached(cid)
        if cached is not None:
            return cached

        res = self._fetch(cid)
        self.health['status_' + res.status] += 1
        self._store(cid, res)
        return res

    def get_many(self, condition_ids) -> Dict[str, MarketResolution]:
        """One lookup per DISTINCT id, in first-seen order.

        Seven whales in the same BTC Up/Down market is one fetch, not seven.
        """
        out: Dict[str, MarketResolution] = {}
        for cid in condition_ids or ():
            if not cid:
                continue
            key = str(cid).strip()
            if key in out:
                continue
            out[key] = self.get(key)
        return out

    def _fetch(self, cid: str) -> MarketResolution:
        if self.client is None:
            self.health['no_client'] += 1
            return _unresolved(cid, STATUS_FETCH_FAILED)
        clob = getattr(self.client, 'clob', None)
        if not callable(clob):
            self.health['client_has_no_clob'] += 1
            return _unresolved(cid, STATUS_FETCH_FAILED)
        self.health['fetch_attempts'] += 1
        try:
            payload = clob(CLOB_MARKET_PATH + cid)
        except Exception as exc:                        # noqa: BLE001
            # A client that raises is a transport failure, never an unresolved
            # market. The status says so and the ttl is the short one.
            logger.warning('resolution fetch raised for %s: %s', cid, exc)
            self.health['fetch_raised'] += 1
            return _unresolved(cid, STATUS_FETCH_FAILED)
        if payload is None:
            self.health['fetch_returned_none'] += 1
            return _unresolved(cid, STATUS_FETCH_FAILED)
        return resolution_from_clob(payload, cid)

    # -- reporting ----------------------------------------------------------

    def census(self) -> Dict[str, int]:
        """status -> count over everything currently cached. Every status key
        present even at zero, and the total is asserted against the cache size
        so a status added without being registered cannot hide."""
        counts = {s: 0 for s in RESOLUTION_STATUSES}
        with self._lock:
            entries: List[MarketResolution] = [e[2] for e in self._cache.values()]
        for res in entries:
            if res.status not in counts:
                raise AssertionError(
                    'resolution status {!r} is not in RESOLUTION_STATUSES; add '
                    'it there rather than letting it pool into another '
                    'bucket (convention 20)'.format(res.status))
            counts[res.status] += 1
        if sum(counts.values()) != len(entries):
            raise AssertionError('resolution census does not balance')
        return counts
