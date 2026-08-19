"""Caller feed: poll a named public trader's Reddit history for declared plays.

Proposal 027 (`strategies/proposals/027-pm-smart-money-callers.md`). Same shape
as `smart_money_copy.py`'s wallet feed, one venue over: a wallet feed reads fills
off `data-api.polymarket.com`, this reads posts off a public Reddit JSON mirror
and turns free text into a DECLARED PLAY - a direction, a ticker, and (when the
post gives one) an expiry and a strike. It never reads an order book and never
places one. NO WALLET, NO SIGNER, NO POST - the same structural refusal
`WalletTradeFeed` makes.

## Why Reddit and not the Data API

`smart_money_copy` mirrors a WALLET's fills, which are anonymous and settle
publicly on Polymarket but carry no name and no stated conviction. This
strategy mirrors a NAMED caller's DECLARED direction, stated in advance, in
public, on a platform outside Polymarket. The caller's edge (if any) is
predictive of a stock's move; Polymarket is one of several venues that let us
express the same call. See the module docstring on
`strategies/polymarket/smart_money_callers.py` for how a declared play becomes
a Polymarket entry.

## Parsing is lossy on purpose

Reddit post bodies are free text written for a human audience, not a schema.
`extract_declared_play` is a conservative regex extractor: it only accepts a
`$TICKER` token, a direction word, and a parseable month/day, all three, or it
refuses the whole post with a NAMED reason (convention 20 - a silent `continue`
is a missing number). Refusing an ambiguous post is correct behaviour, not a
bug: this file's job is never to guess.

`$MRVL puts 9/25` parses. `MRVL puts, 9/25` (no dollar sign) does NOT - the `$`
prefix is the one unambiguous ticker delimiter this file trusts, because
without it a five-letter English word ("PUMP", "SHORT") is indistinguishable
from a real ticker and a false positive here is a real order elsewhere in the
pipeline. That is a real gap against how people actually write on r/wsb, and it
is a DELIBERATE trade-off documented rather than a thing this file pretends not
to have.

## Rate limiting is a TTL gate, not a sleep

This runs inside a poll loop that must never block (the loop also drives
crypto strategies on a 5-second cadence). `CallerFeed.poll()` therefore never
sleeps; it checks a per-handle last-poll timestamp against
`DEFAULT_POLL_INTERVAL_SEC` (one hour) and, when the window has not elapsed,
returns the LAST fetched result rather than re-hitting the mirror. That last
result is cached in memory, not re-derived, so "rate limited" and "fetched,
found nothing" are never the same code path (convention 11).

## Storage: two files, two different jobs

  `data/caller_feed/<handle>.jsonl`   the raw fetched posts, one JSON object
                                      per line, append-only, each row carrying
                                      the POST's own `created_utc` - not the
                                      time we happened to fetch it. This is the
                                      audit trail: what did the mirror actually
                                      say, and when did they say it.

  `data/caller_record.json`          one row per caller: how many DISTINCT
                                      declared plays we have ever recorded for
                                      them (`play_ids`, deduplicated - a poll
                                      refetches the WHOLE listing every hour,
                                      not just what is new, so counting raw
                                      rows would double-count every play on
                                      every poll), and how many of those plays
                                      have a VERIFIED outcome. `verified_plays`
                                      and `measured` stay at 0 / False in this
                                      build: verifying a declared play means
                                      checking the underlying stock's price on
                                      the declared expiry, and building that
                                      check is explicitly out of scope for this
                                      task (see the proposal's
                                      `data_requirements`). That is NOT_TESTED,
                                      never a fabricated "0 for 0" pass
                                      (convention 11) - `strategies/polymarket/
                                      smart_money_callers.py` reads this file to
                                      decide whether it has ever seen a given
                                      caller at all, and does not gate on
                                      verification because nothing here can
                                      measure it yet.

Both files are written through `engine.concurrency` (`safe_write` /
`safe_edit`), never through a raw `open(..., 'a')`, so the pre-commit
`conflict-check` hook can verify the on-disk hash against the last tracked
write.

## What is NOT built here, stated rather than left to be discovered

  - **The `r.jina.ai` fallback.** The task calls for it only "if you can
    confirm its request shape without guessing." This session has no verified
    request/response contract for that host, so only the primary redlib
    mirror is wired. `_fetch_raw` is the single choke point a fallback would
    plug into; see the TODO on `CallerFeed._fetch_raw`.
  - **A stock-price resolution oracle.** `verified_plays` cannot become
    nonzero without one. See `data/caller_record.json` above.
  - **Comment-history plays.** `t1` (comment) rows are parsed exactly like
    `t3` (post) rows - both carry free text this module can regex - but a
    caller's declared plays in this source class live almost entirely in top
    -level posts. Nothing here treats a comment differently; it is exercised
    by whatever the mirror actually returns.
"""
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

from engine import concurrency as C

logger = logging.getLogger(__name__)

# Never False in this repo. This module has no signer, no wallet, and no order
# path - see the module docstring's "NO WALLET, NO SIGNER, NO POST" line.
PAPER_MODE = True

# ---------------------------------------------------------------------------
# Feed transport
# ---------------------------------------------------------------------------

#: The public read-only redlib mirror the proposal names. `{handle}` is
#: substituted; the response is Reddit's own JSON shape (a Listing of
#: children), not a redlib-specific envelope.
REDLIB_HOST = 'https://redlib.catsarch.com'
CALLER_FEED_PATH_TMPL = '/user/{handle}.json'

#: Short on purpose, matching `smart_money_copy.DEFAULT_FEED_TIMEOUT_SEC`'s
#: reasoning: this still runs inside the shared poll loop even though ITS OWN
#: cadence is an hour, because the TTL gate below is what makes the network
#: call rare, not a long per-call budget.
DEFAULT_FEED_TIMEOUT_SEC = 5.0
DEFAULT_FEED_RETRIES = 2
FEED_BACKOFF_SEC = 0.5

#: One hour. Convention 17: an assumption with an expiry date, not a
#: measurement. Chosen because a public Reddit mirror is not our
#: infrastructure and this strategy's edge horizon (days, not seconds) does
#: not need a tighter cadence.
DEFAULT_POLL_INTERVAL_SEC = 3600.0

CALLER_DATA_DIR = 'data/caller_feed'
CALLER_RECORD_PATH = 'data/caller_record.json'

#: The agent id every write through the ledger is stamped with in this
#: module's own default paths. Callers running under a different session
#: should pass their own.
DEFAULT_AGENT_ID = 'cody-027'

#: Statuses `CallerFeed.poll()` can report. Every one is a DIFFERENT fact
#: (convention 11):
#:   fetched_fresh                a network read happened and succeeded.
#:   rate_limited_returned_cache  within the TTL window; NOT a network call.
#:   unreachable_no_cache         the read failed and there is no prior result
#:                                to fall back to - this handle has NEVER been
#:                                successfully fetched.
#:   unreachable_returned_stale_cache
#:                                the read failed but a previous successful
#:                                fetch exists; that stale result is returned
#:                                rather than treating the caller as silent.
POLL_STATUS_FRESH = 'fetched_fresh'
POLL_STATUS_CACHED = 'rate_limited_returned_cache'
POLL_STATUS_UNREACHABLE_NO_CACHE = 'unreachable_no_cache'
POLL_STATUS_UNREACHABLE_STALE_CACHE = 'unreachable_returned_stale_cache'

POLL_STATUSES = (POLL_STATUS_FRESH, POLL_STATUS_CACHED,
                 POLL_STATUS_UNREACHABLE_NO_CACHE,
                 POLL_STATUS_UNREACHABLE_STALE_CACHE)


def _reject_non_finite(token: str) -> float:
    """`json.loads` accepts bare Infinity and NaN. This refuses them.

    Convention 19, copied from `smart_money_copy._reject_non_finite`: a
    caller-record field computed over a non-finite value would round-trip out
    of Python as a token no other JSON parser accepts.
    """
    raise ValueError('caller feed payload contained the non-finite JSON '
                     'constant {!r}; this is not portable JSON '
                     '(convention 19)'.format(token))


# ---------------------------------------------------------------------------
# Declared plays: the parsed output of one post
# ---------------------------------------------------------------------------

#: Every reason `extract_declared_play` can refuse a post. Named so a feed
#: that starts returning a changed post shape shows up as a categorised count
#: rather than a quiet caller (convention 20).
PARSE_DROP_REASONS = (
    'no_ticker_found',
    'no_direction_found',
    'no_expiry_found',
    'unparseable_expiry_date',
    'no_post_timestamp',
)

#: `$` plus 1-5 uppercase letters, bounded by a non-alnum edge on both sides.
#: This is the ONE unambiguous ticker delimiter this file trusts. See the
#: module docstring for why a bare "MRVL" is refused rather than guessed at.
TICKER_RE = re.compile(r'(?<![A-Za-z0-9])\$([A-Z]{1,5})(?![A-Za-z0-9])')

#: A direction WORD, not a direction inferred from sentiment. 'call(s)' and
#: 'long' normalise to 'long'; 'put(s)' and 'short' normalise to 'short'.
#: Word-bounded so "shortage" or "callback" cannot match.
DIRECTION_RE = re.compile(r'\b(calls?|puts?|long|short)\b', re.IGNORECASE)
_LONG_WORDS = frozenset({'call', 'calls', 'long'})
_SHORT_WORDS = frozenset({'put', 'puts', 'short'})

#: `9/25`, `09/25`, `9/25/26`, `9/25/2026`. Two-digit years are read as
#: 2000+YY - every plausible expiry in this source class is inside that
#: century.
DATE_RE = re.compile(r'\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b')

#: A strike, only when clearly labelled ("200 strike", "200c", "200p"). Never
#: inferred from a bare number, which would collide with a ticker's price
#: target, a percentage, or a date fragment. Optional on a DeclaredPlay - a
#: play with no readable strike is still a complete play (direction + ticker +
#: expiry is what a Polymarket stock-event market resolves on; the strike is
#: extra context, not a required field).
STRIKE_RE = re.compile(
    r'\b(\d{1,5}(?:\.\d+)?)\s*(?:strike|c|p)\b', re.IGNORECASE)


@dataclass(frozen=True)
class DeclaredPlay:
    """One caller's declared direction on one ticker, as read from one post.

    `direction` is always exactly 'long' or 'short' - never a raw word like
    'calls', so a reader downstream never has to re-normalise it. `expiry` is
    an ISO date string ('YYYY-MM-DD') or None; a play with no readable expiry
    is refused entirely by `extract_declared_play` (`no_expiry_found`), so a
    constructed `DeclaredPlay` always carries one in practice - the field
    stays Optional because a caller MAY build one directly (e.g. in a test)
    without going through the parser.
    """

    handle: str
    play_id: str            # 'handle:post_id' - stable, used for dedupe
    ticker: str              # bare, no '$' - e.g. 'MRVL'
    direction: str           # 'long' | 'short'
    post_id: Optional[str] = None
    post_ts: Optional[float] = None       # epoch seconds, from created_utc
    expiry: Optional[str] = None          # ISO date 'YYYY-MM-DD'
    strike: Optional[float] = None
    source_kind: Optional[str] = None     # 't3' (post) | 't1' (comment)
    raw_text: str = field(default='', repr=False)

    def to_dict(self) -> dict:
        return {
            'handle': self.handle, 'play_id': self.play_id,
            'ticker': self.ticker, 'direction': self.direction,
            'post_id': self.post_id, 'post_ts': self.post_ts,
            'expiry': self.expiry, 'strike': self.strike,
            'source_kind': self.source_kind,
        }


def _normalise_direction(word: str) -> Optional[str]:
    w = word.strip().lower()
    if w in _LONG_WORDS:
        return 'long'
    if w in _SHORT_WORDS:
        return 'short'
    return None


def _infer_expiry_year(month: int, day: int, post_dt: datetime) -> Optional[int]:
    """Which year a bare month/day belongs to, relative to the post's date.

    A declared expiry is always in the caller's FUTURE relative to when they
    posted it - nobody declares a play on an option that already expired. So a
    month/day that falls BEFORE the post's own month/day this year must mean
    NEXT year; one on or after it stays this year. Returns None only when the
    month/day cannot form a valid date in either candidate year (a genuinely
    malformed date, e.g. 2/30).
    """
    base_year = post_dt.year
    try:
        candidate = date(base_year, month, day)
    except ValueError:
        return None
    if candidate < post_dt.date():
        return base_year + 1
    return base_year


def extract_declared_play(handle: str, post_id: Optional[str],
                          text: str, post_ts: Optional[float],
                          source_kind: Optional[str] = None
                          ) -> Tuple[Optional[DeclaredPlay], Optional[str]]:
    """Parse one post's combined text into a DeclaredPlay. `(play, reason)`.

    Exactly one of the two is not None. Every refusal is one of
    `PARSE_DROP_REASONS` - never a bare `continue` (convention 20).
    """
    ticker_match = TICKER_RE.search(text or '')
    if ticker_match is None:
        return None, 'no_ticker_found'
    ticker = ticker_match.group(1)

    direction_match = DIRECTION_RE.search(text or '')
    direction = (_normalise_direction(direction_match.group(1))
                if direction_match else None)
    if direction is None:
        return None, 'no_direction_found'

    date_match = DATE_RE.search(text or '')
    if date_match is None:
        return None, 'no_expiry_found'
    month_s, day_s, year_s = date_match.groups()
    month, day = int(month_s), int(day_s)
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None, 'unparseable_expiry_date'

    if year_s:
        year = int(year_s)
        if year < 100:
            year += 2000
    else:
        if post_ts is None:
            # Cannot roll a bare month/day forward without knowing "now" at
            # post time. Refusing rather than guessing the current year.
            return None, 'no_post_timestamp'
        post_dt = datetime.fromtimestamp(float(post_ts), tz=timezone.utc)
        year = _infer_expiry_year(month, day, post_dt)
        if year is None:
            return None, 'unparseable_expiry_date'

    try:
        expiry = date(year, month, day).isoformat()
    except ValueError:
        return None, 'unparseable_expiry_date'

    strike_match = STRIKE_RE.search(text or '')
    strike = None
    if strike_match:
        try:
            strike = float(strike_match.group(1))
        except ValueError:
            strike = None

    play_id = '{}:{}'.format(handle, post_id or ticker_match.start())
    return DeclaredPlay(
        handle=handle, play_id=play_id, ticker=ticker, direction=direction,
        post_id=post_id, post_ts=post_ts, expiry=expiry, strike=strike,
        source_kind=source_kind, raw_text=text[:500],
    ), None


# ---------------------------------------------------------------------------
# Reddit/redlib listing shape
# ---------------------------------------------------------------------------

def _iter_children(payload) -> List[dict]:
    """Every `children[i]` row across one or more Listings.

    Redlib and Reddit both answer a user-overview request with a single
    Listing object (`{'kind': 'Listing', 'data': {'children': [...]}}`). Some
    hosts wrap multiple listings (posts and comments served separately) in a
    bare list of Listings. Both shapes are accepted; anything else yields no
    children rather than raising, and the caller counts that.
    """
    listings = payload if isinstance(payload, list) else [payload]
    out: List[dict] = []
    for listing in listings:
        if not isinstance(listing, dict):
            continue
        data = listing.get('data')
        children = data.get('children') if isinstance(data, dict) else None
        if isinstance(children, list):
            out.extend(children)
    return out


def parse_caller_posts(payload, handle: str
                       ) -> Tuple[List[DeclaredPlay], Dict[str, int]]:
    """One fetched listing payload -> `(plays, drops)`.

    `drops` is keyed by `PARSE_DROP_REASONS` plus the two shape-level reasons
    below, and every row the payload contained lands in exactly one bucket -
    either `plays` or `drops` (convention 20).
    """
    drops: Dict[str, int] = {}

    def _bump(reason: str) -> None:
        drops[reason] = drops.get(reason, 0) + 1

    children = _iter_children(payload)
    plays: List[DeclaredPlay] = []
    for child in children:
        if not isinstance(child, dict):
            _bump('child_not_a_dict')
            continue
        data = child.get('data')
        if not isinstance(data, dict):
            _bump('no_data_field')
            continue
        title = data.get('title') or ''
        body = data.get('selftext') or data.get('body') or ''
        text = '{} {}'.format(title, body).strip()
        post_ts = data.get('created_utc')
        try:
            post_ts = float(post_ts) if post_ts is not None else None
        except (TypeError, ValueError):
            post_ts = None
        play, reason = extract_declared_play(
            handle=handle, post_id=data.get('id'), text=text,
            post_ts=post_ts, source_kind=child.get('kind'))
        if play is None:
            _bump(reason or 'unknown')
            continue
        plays.append(play)
    return plays, drops


# ---------------------------------------------------------------------------
# CallerRecord: the census this strategy gates entry on
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CallerRecord:
    """A caller's tracked history. NEVER a claimed one (convention 3).

    `play_ids` is the deduplicated set of every declared play we have ever
    recorded for this handle, stored as a sorted tuple so the JSON on disk is
    stable and diffable. `declared_plays_seen` is derived from it rather than
    kept as a separate counter, so the two can never drift apart.

    `verified_plays` and `measured` are ALWAYS 0 / False in this build - see
    the module docstring's storage section. There is no constructor path here
    that fabricates a verified outcome; the fields exist so the shape is
    ready for the day a resolution oracle exists; and every reader of this
    class stamps `caller_record_verification_not_built` so nobody downstream
    mistakes an all-zero record for a caller who has been checked and found
    wanting.
    """

    handle: str
    play_ids: Tuple[str, ...] = ()
    verified_plays: int = 0
    measured: bool = False
    first_seen_ts: Optional[float] = None
    last_seen_ts: Optional[float] = None
    source: str = 'caller_feed_v1'

    @property
    def declared_plays_seen(self) -> int:
        return len(self.play_ids)

    def to_dict(self) -> dict:
        return {
            'handle': self.handle, 'play_ids': list(self.play_ids),
            'declared_plays_seen': self.declared_plays_seen,
            'verified_plays': self.verified_plays, 'measured': self.measured,
            'first_seen_ts': self.first_seen_ts,
            'last_seen_ts': self.last_seen_ts, 'source': self.source,
            'caller_record_verification_not_built': True,
        }


def caller_record_from_dict(handle: str, d: dict) -> CallerRecord:
    """Tolerant reconstruction. A malformed field falls back to the type's
    own default rather than raising - a corrupt `caller_record.json` must
    degrade the record to unmeasured, never crash the poll loop that would
    otherwise fix it on the next successful write.
    """
    play_ids = d.get('play_ids')
    if not isinstance(play_ids, list):
        play_ids = []
    verified = d.get('verified_plays')
    verified = int(verified) if isinstance(verified, (int, float)) else 0
    return CallerRecord(
        handle=handle,
        play_ids=tuple(sorted(str(p) for p in play_ids)),
        verified_plays=verified,
        measured=bool(d.get('measured', False)),
        first_seen_ts=d.get('first_seen_ts'),
        last_seen_ts=d.get('last_seen_ts'),
        source=str(d.get('source', 'caller_feed_v1')),
    )


def _parse_caller_record_json(text: str) -> Dict[str, CallerRecord]:
    if not text or not text.strip():
        return {}
    try:
        raw = json.loads(text, parse_constant=_reject_non_finite)
    except ValueError as exc:
        logger.error('caller_record.json is corrupt (%s); treating as an '
                     'empty record store rather than crashing the poll loop',
                     exc)
        return {}
    if not isinstance(raw, dict):
        return {}
    out: Dict[str, CallerRecord] = {}
    for handle, d in raw.items():
        if isinstance(d, dict):
            out[handle] = caller_record_from_dict(handle, d)
    return out


def _dump_caller_records(records: Dict[str, CallerRecord]) -> str:
    payload = {h: r.to_dict() for h, r in sorted(records.items())}
    return json.dumps(payload, indent=2, sort_keys=True,
                      allow_nan=False) + '\n'


def load_caller_records(path: str = CALLER_RECORD_PATH
                        ) -> Dict[str, CallerRecord]:
    """Read-only load. Missing file or corrupt JSON both yield `{}`.

    A plain read, not a ledger checkout: this function never writes, and
    `engine.concurrency` guards writes, not reads.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as fh:
            text = fh.read()
    except OSError as exc:
        logger.warning('could not read %s (%s); treating as no records',
                       path, exc)
        return {}
    return _parse_caller_record_json(text)


def merge_declared_plays(existing: Optional[CallerRecord], handle: str,
                         plays: List[DeclaredPlay], now: float
                         ) -> CallerRecord:
    """Fold freshly-parsed plays into a caller's record. Deduplicated.

    A poll refetches the caller's WHOLE listing every hour, not a delta, so
    the same play arrives on every poll. Without dedup `declared_plays_seen`
    would count POLLS, not plays. `play_ids` is a set-union, and the record's
    `verified_plays`/`measured` pass through unchanged - this function only
    ever grows the declared count, never touches verification.
    """
    prior_ids = set(existing.play_ids) if existing else set()
    new_ids = {p.play_id for p in plays if p.play_id}
    all_ids = tuple(sorted(prior_ids | new_ids))
    first_seen = (existing.first_seen_ts if existing and
                 existing.first_seen_ts is not None else
                 (now if plays else None))
    return CallerRecord(
        handle=handle, play_ids=all_ids,
        verified_plays=existing.verified_plays if existing else 0,
        measured=existing.measured if existing else False,
        first_seen_ts=first_seen,
        last_seen_ts=now if plays else (
            existing.last_seen_ts if existing else None),
        source=existing.source if existing else 'caller_feed_v1',
    )


def _write_through_ledger(path: str, transform, agent_id: str,
                          db_path: str = C.DEFAULT_DB_PATH) -> None:
    """Apply `transform(old_content_or_empty) -> new_content` via the ledger.

    Handles both the create case (path does not exist) and the update case
    with the same function. `transform` must tolerate being called on '' (no
    prior content) exactly as `safe_edit`'s own contract requires it tolerate
    being re-applied to someone else's intervening change - see
    `engine.concurrency.safe_edit`'s docstring.

    On the create path, a race between the existence check and the write is
    handled by falling back to `safe_edit` if `safe_write(must_be_new=True)`
    reports another writer got there first; the ledger's hash check is what
    actually adjudicates the race, not this function's `os.path.exists`.

    `db_path` defaults to the shared `db/trading.db` coordination table, and
    is threaded all the way from `CallerFeed.__init__` so a test can point it
    at a `tmp_path` database and never write a coordination row into the real
    one, matching `tests/test_concurrency.py`'s own convention.
    """
    if os.path.exists(path):
        C.safe_edit(path, transform, agent_id=agent_id, db_path=db_path)
        return
    new_content = transform('')
    try:
        C.safe_write(path, new_content, agent_id=agent_id,
                     db_path=db_path, must_be_new=True)
    except C.ConcurrentModificationError:
        C.safe_edit(path, transform, agent_id=agent_id, db_path=db_path)


def record_declared_plays(handle: str, plays: List[DeclaredPlay],
                          path: str = CALLER_RECORD_PATH,
                          agent_id: str = DEFAULT_AGENT_ID,
                          now: Optional[float] = None,
                          db_path: str = C.DEFAULT_DB_PATH) -> CallerRecord:
    """Merge `plays` into `path`'s record for `handle` and persist it.

    No-op on disk when `plays` is empty (a poll that found nothing does not
    need to touch the ledger); the in-memory merge still runs so the return
    value is always a real `CallerRecord`, existing or freshly-initialised.
    """
    now = time.time() if now is None else now
    result: Dict[str, CallerRecord] = {}

    def _edit(old_text: str) -> str:
        records = _parse_caller_record_json(old_text)
        updated = merge_declared_plays(records.get(handle), handle, plays, now)
        records[handle] = updated
        result['record'] = updated
        return _dump_caller_records(records)

    if not plays:
        existing = load_caller_records(path).get(handle)
        return existing or CallerRecord(handle=handle)

    _write_through_ledger(path, _edit, agent_id, db_path=db_path)
    return result['record']


def _append_jsonl(path: str, blob: str, agent_id: str,
                  db_path: str = C.DEFAULT_DB_PATH) -> None:
    _write_through_ledger(path, lambda old: old + blob, agent_id,
                          db_path=db_path)


# ---------------------------------------------------------------------------
# The feed itself
# ---------------------------------------------------------------------------

class CallerFeed:
    """Read-only poller for one or more callers' Reddit histories.

    Injectable exactly the way `WalletTradeFeed` is: a test hands in a stub
    with its own `_fetch_raw` (or constructs the class with `transport=`) and
    never touches the network. `poll(handle)` is the only method a strategy
    calls; it owns the TTL gate, the parse, and the persistence side effects.
    """

    def __init__(self, timeout: float = DEFAULT_FEED_TIMEOUT_SEC,
                 retries: int = DEFAULT_FEED_RETRIES,
                 host: str = REDLIB_HOST,
                 poll_interval_sec: float = DEFAULT_POLL_INTERVAL_SEC,
                 data_dir: str = CALLER_DATA_DIR,
                 record_path: str = CALLER_RECORD_PATH,
                 agent_id: str = DEFAULT_AGENT_ID,
                 db_path: str = C.DEFAULT_DB_PATH,
                 clock=None, transport=None):
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))
        self.host = host.rstrip('/')
        self.poll_interval_sec = float(poll_interval_sec)
        self.data_dir = data_dir
        self.record_path = record_path
        self.agent_id = agent_id
        #: The coordination database `safe_write`/`safe_edit` log to. Defaults
        #: to the shared `db/trading.db`; a test overrides this to a
        #: `tmp_path` database so it never writes a coordination row into the
        #: real one (matching `tests/test_concurrency.py`'s convention).
        self.db_path = db_path
        self._clock = clock or time.time
        #: Injectable transport: `transport(url) -> str` (response body) or
        #: raises. Defaults to `urllib.request`, matching
        #: `WalletTradeFeed._get_via_urllib`. A test supplies a stub here and
        #: never resolves a hostname.
        self._transport = transport
        self._last_poll_ts: Dict[str, float] = {}
        #: handle -> (plays, drops), the last SUCCESSFUL parse. Serves both
        #: the rate-limited-cache and the unreachable-stale-cache paths.
        self._cache: Dict[str, Tuple[List[DeclaredPlay], Dict[str, int]]] = {}
        self.stats: Dict[str, int] = {
            'requests': 0, 'retries': 0, 'fetch_ok': 0, 'fetch_failed': 0,
            'poll_rate_limited': 0,
        }

    # -- transport ------------------------------------------------------

    def _default_transport(self, url: str) -> str:
        import urllib.request
        req = urllib.request.Request(
            url, headers={'User-Agent': '05-trading-bot/paper (read-only)'})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            if getattr(resp, 'status', 200) != 200:
                raise IOError('HTTP {}'.format(resp.status))
            return resp.read().decode('utf-8')

    def _fetch_raw(self, handle: str) -> Optional[list]:
        """One handle's listing payload, or None on any failure.

        TODO (documented, not guessed): `r.jina.ai` as a fallback path when
        the primary mirror is unreachable. The task instructions are explicit
        that inventing that host's request/response contract without
        verifying it live would be worse than not having a fallback, so only
        `REDLIB_HOST` is wired. This method is the single choke point a
        fallback would plug into - see `_get` below for the retry loop it
        would join.
        """
        url = self.host + CALLER_FEED_PATH_TMPL.format(handle=handle)
        transport = self._transport or self._default_transport

        last_err = None
        for attempt in range(self.retries):
            is_last = attempt == self.retries - 1
            self.stats['requests'] += 1
            try:
                body = transport(url)
            except Exception as exc:                     # noqa: BLE001
                last_err = '{}: {}'.format(type(exc).__name__, exc)
                if is_last:
                    break
                self.stats['retries'] += 1
                time.sleep(FEED_BACKOFF_SEC * (2 ** attempt))
                continue

            try:
                payload = json.loads(body, parse_constant=_reject_non_finite)
            except ValueError as exc:
                logger.error('caller feed %s: unparseable body (%s)', url, exc)
                return None
            return payload

        logger.warning('caller_feed_unreachable: %s failed after %d '
                       'attempts: %s', url, self.retries, last_err)
        return None

    # -- persistence ------------------------------------------------------

    def _persist_raw(self, handle: str, payload, fetched_at: float) -> None:
        rows = []
        for child in _iter_children(payload):
            if not isinstance(child, dict):
                continue
            data = child.get('data')
            if not isinstance(data, dict):
                continue
            row = {
                'handle': handle, 'kind': child.get('kind'),
                'id': data.get('id'), 'title': data.get('title'),
                'selftext': data.get('selftext'), 'body': data.get('body'),
                'created_utc': data.get('created_utc'),
                'fetched_at': fetched_at,
            }
            try:
                rows.append(json.dumps(row, allow_nan=False, sort_keys=True))
            except (TypeError, ValueError):
                # A row that cannot even be dumped is not one we can log
                # meaningfully either; skip it rather than corrupt the file.
                continue
        if not rows:
            return
        blob = '\n'.join(rows) + '\n'
        path = os.path.join(self.data_dir, '{}.jsonl'.format(handle))
        try:
            _append_jsonl(path, blob, self.agent_id, db_path=self.db_path)
        except Exception as exc:                          # noqa: BLE001
            # Persistence failing must not take the strategy down with it -
            # the in-memory parse this poll produced is still returned.
            logger.warning('could not persist caller feed rows for %s (%s)',
                           handle, exc)

    # -- public API ---------------------------------------------------------

    def poll(self, handle: str) -> Tuple[Optional[List[DeclaredPlay]],
                                         Dict[str, int], str]:
        """`(plays_or_None, parse_drops, status)`. Never sleeps, never raises.

        `status` is one of `POLL_STATUSES`. `plays` is `None` only when this
        handle has NEVER been fetched successfully AND the read just failed
        again (`POLL_STATUS_UNREACHABLE_NO_CACHE`) - every other status
        returns a real (possibly empty) list. An empty list is a caller who
        genuinely has no parseable declared plays right now, never a stand-in
        for "could not check" (convention 11).
        """
        now = self._clock()
        last = self._last_poll_ts.get(handle)
        if last is not None and (now - last) < self.poll_interval_sec:
            self.stats['poll_rate_limited'] += 1
            cached = self._cache.get(handle)
            if cached is None:
                # Rate-limited before any fetch ever completed - only reachable
                # if the caller manipulates `_last_poll_ts` directly; kept as a
                # named, honest answer rather than an assertion.
                return None, {}, POLL_STATUS_CACHED
            plays, drops = cached
            return list(plays), dict(drops), POLL_STATUS_CACHED

        raw = self._fetch_raw(handle)
        self._last_poll_ts[handle] = now  # consumed whether or not it worked
        if raw is None:
            self.stats['fetch_failed'] += 1
            cached = self._cache.get(handle)
            if cached is None:
                return None, {}, POLL_STATUS_UNREACHABLE_NO_CACHE
            plays, drops = cached
            return list(plays), dict(drops), POLL_STATUS_UNREACHABLE_STALE_CACHE

        plays, drops = parse_caller_posts(raw, handle)
        self._cache[handle] = (plays, drops)
        self.stats['fetch_ok'] += 1
        self._persist_raw(handle, raw, now)
        if plays:
            try:
                record_declared_plays(handle, plays, path=self.record_path,
                                      agent_id=self.agent_id, now=now,
                                      db_path=self.db_path)
            except Exception as exc:                      # noqa: BLE001
                logger.warning('could not update caller_record.json for %s '
                               '(%s)', handle, exc)
        return list(plays), dict(drops), POLL_STATUS_FRESH
