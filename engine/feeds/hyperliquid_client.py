"""Hyperliquid whale-position poller.

Polls large (>$100k notional) open perp positions on Hyperliquid and writes a
time-series of snapshots to `hyperliquid_positions` in db/trading.db. Intended
to feed a future `near_liq_trigger` strategy: the interesting column is
`liq_price`, because a cluster of large positions with nearby liquidation
prices is a predictable forced-flow event.

READ-ONLY. Public endpoints only. No API key, no wallet, no signer, no order
path. This module cannot trade and has no import of engine.executor.


WALLET DISCOVERY: what is actually possible (verified live 2026-08-18)
----------------------------------------------------------------------
The `/info` endpoint is ADDRESS-SCOPED for position data. This was verified by
probe, not assumed from documentation:

    POST /info {"type":"clearinghouseState"}                -> HTTP 422
    POST /info {"type":"clearinghouseState","user":"0x..."} -> HTTP 200
    POST /info {"type":"userState","user":"0x..."}          -> HTTP 422 (no such type)
    POST /info {"type":"allPositions"}                      -> HTTP 422 (no such type)
    POST /info {"type":"leaderboard"}                       -> HTTP 422 (no such type)

So there is NO single `/info` call that returns "all whale positions"
globally.

Global discovery IS possible, but via a SECOND, DIFFERENT host:

    GET https://stats-data.hyperliquid.xyz/Mainnet/leaderboard  -> HTTP 200

which returns ~41,975 `leaderboardRows`, each with an `ethAddress` and an
`accountValue`. That is a ranked, public, unauthenticated list of real
addresses, which we then query per-address via `clearinghouseState`. This is
the two-stage path this module implements, and it works.

CAVEATS a reader must not gloss over:

  1. The leaderboard is NOT part of the documented `/info` API. It is the
     S3-backed stats bucket the Hyperliquid frontend reads. It is undocumented
     and may change or vanish without notice. If it fails, discovery degrades
     to the on-disk cache and then to `--wallets`, and the module logs a
     FAILURE rather than silently polling nothing.

  2. It is 34.6 MB and uncompressed (the server ignores Accept-Encoding: gzip;
     measured). Fetching it on a 30s cadence would be abusive, so it is fetched
     on a much slower cadence and cached to disk. Default refresh age: 6 hours.

  3. `accountValue` on the leaderboard is a POOR ranking proxy and the top of
     the list is actively misleading. Measured 2026-08-18: the ten highest
     leaderboard `accountValue` addresses returned ZERO open positions between
     them, and for the top address the leaderboard claimed $14,113,335,843
     while `clearinghouseState` reported an account value of $10,141.13 in the
     same minute. These are stale or inflated ghost entries. Real whale
     positions started appearing further down the ranking: a scan of the top 25
     found 13 positions over $100k, all from addresses ranked below 10th.
     Consequently DEFAULT_TOP_N is 25, not 5, and `--top-n` is a COVERAGE knob,
     not a definition of "the whales".

  4. Because of (3), this module makes NO claim to observe every large position
     on Hyperliquid. It observes the positions of the addresses it polled. The
     per-poll log line says "wallets=N", never "all whales". Sampling bias here
     is real and unquantified: ranking by a stale account-value field is not a
     random sample of large positions. Treat the resulting table as a WATCHLIST
     time-series, not a census. See the handoff for the open question.

  5. Every number this module WRITES comes from `clearinghouseState`. The
     leaderboard is used only to choose which addresses to ask about; none of
     its figures reach the database.

Convention 11 applies throughout: a failed poll is recorded as a failure, never
as "found no whales". Convention 22: the claims above are pinned by tests in
tests/test_hyperliquid_client.py, and the live-probe evidence is in the handoff.


SCHEMA NOTE (resolved 2026-08-18)
---------------------------------------
Repo convention is that tables live in `db/schema.sql`, and
`hyperliquid_positions` is now declared there. The copy below STAYS: it is what
lets this feed bootstrap its own storage against a database that predates the
table, and `HyperliquidStore.ensure_schema()` applies it (all
`CREATE ... IF NOT EXISTS`, so it is safe to re-run and safe against a db that
another process holds open).

Two copies can drift, and because both are IF NOT EXISTS whichever runs first
wins and the second is a SILENT no-op - so a divergence would surface only on a
fresh checkout, or as a column that quietly reads NULL. If you change this DDL,
change `db/schema.sql` too: `tests/test_schema_matches_feed_modules.py` asserts
the two agree, column by column and index by index.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sqlite3
import time
from collections import Counter, OrderedDict
from typing import Any, Dict, List, Optional, Sequence, Tuple

try:
    import requests
except ImportError:  # pragma: no cover - requests is present on system python3.9
    requests = None  # type: ignore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoints. Two different hosts, on purpose - see the module docstring.
# ---------------------------------------------------------------------------
INFO_URL = 'https://api.hyperliquid.xyz/info'
LEADERBOARD_URL = 'https://stats-data.hyperliquid.xyz/Mainnet/leaderboard'

USER_AGENT = 'aym-trading-bot/hyperliquid-feed (research; read-only)'

# ---------------------------------------------------------------------------
# Defaults. Module-level constants, never inline literals, so a reviewer can
# see the whole policy in one place.
# ---------------------------------------------------------------------------
DEFAULT_SYMBOLS: Tuple[str, ...] = ('BTC', 'ETH', 'SOL')
DEFAULT_MIN_NOTIONAL = 100_000.0
DEFAULT_INTERVAL_SEC = 30.0
DEFAULT_TIMEOUT = 15.0
DEFAULT_RETRIES = 3

# 25, not 10: the top 10 leaderboard entries measured as empty ghost accounts.
# See caveat 3 in the module docstring. This number is a coverage choice with a
# measurement behind it, not a guess.
DEFAULT_TOP_N = 25

# Backoff. Exponential with jitter, capped. Jitter matters because every wallet
# in a poll batch hits the same host: without it, a 429 would resynchronise all
# of them onto the same retry instant.
RETRY_BACKOFF_SEC = 0.5
MAX_BACKOFF_SEC = 30.0
BACKOFF_JITTER_SEC = 0.25

# The leaderboard is 34.6 MB and uncompressed. Fetch it rarely, cache it, and
# give it its own (long) timeout.
LEADERBOARD_TIMEOUT = 90.0
DEFAULT_LEADERBOARD_MAX_AGE_SEC = 6 * 3600
DEFAULT_LEADERBOARD_CACHE = 'research/hyperliquid/leaderboard_wallets.json'

# Cache MORE addresses than we poll. Otherwise a cache built by a `--top-n 10`
# run silently caps a later `--top-n 200` run at 10 wallets, and the operator
# sees a small number with no error to explain it. Slicing happens at use time.
LEADERBOARD_CACHE_WALLETS = 500

# Politeness spacing between per-wallet calls inside one poll. Measured: a
# clearinghouseState call is ~0.18s and 40 sequential calls drew zero 429s, so
# this is headroom rather than a known requirement. The documented rate limit
# could NOT be verified (web access was denied in the authoring session), so
# this default is deliberately conservative.
INTER_REQUEST_SLEEP_SEC = 0.05

DEFAULT_DB_PATH = 'db/trading.db'
DEFAULT_LOG_DIR = 'logs'

# sqlite. db/trading.db is written concurrently by the Polymarket shadow loop,
# so: a busy timeout, short transactions, and NO journal_mode switching. We READ
# journal_mode and log it; changing it on a database another process holds open
# is how you corrupt somebody else's session.
SQLITE_BUSY_TIMEOUT_MS = 5000
SQLITE_CONNECT_TIMEOUT_SEC = 15.0

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS hyperliquid_positions (
    ts          INTEGER NOT NULL,
    wallet      TEXT NOT NULL,
    symbol      TEXT NOT NULL,
    side        TEXT NOT NULL,
    size_usd    REAL NOT NULL,
    entry_price REAL NOT NULL,
    liq_price   REAL,
    leverage    REAL
);
CREATE INDEX IF NOT EXISTS idx_hyperliquid_positions_ts
    ON hyperliquid_positions(ts);
CREATE INDEX IF NOT EXISTS idx_hyperliquid_positions_symbol_ts
    ON hyperliquid_positions(symbol, ts);
"""

# ---------------------------------------------------------------------------
# Skip accounting (convention 20).
#
# Every position received is either WRITTEN or counted under exactly one of
# these reasons. Two drop causes never share a number, and the identity
#     seen - sum(skipped_*) == written
# is asserted every poll. The `skipped_` prefix is what makes the identity
# self-maintaining when a new reason is added.
# ---------------------------------------------------------------------------
SKIP_REASONS: Tuple[str, ...] = (
    'skipped_symbol_out_of_scope',   # a real position, just not BTC/ETH/SOL
    'skipped_below_min_notional',    # in scope, under the whale threshold
    'skipped_missing_field',         # a required key was absent or null
    'skipped_unparseable',           # present but not coercible to the type
)


class NonFiniteJSONError(ValueError):
    """A JSON payload contained Infinity / -Infinity / NaN.

    Convention 19: json.loads is NOT strict and accepts these by default. A NaN
    reaching a REAL column is a silent poison value, so it is rejected at the
    boundary instead.
    """


def _reject_non_finite(token: str) -> Any:
    raise NonFiniteJSONError('non-finite JSON constant: {}'.format(token))


def _utc_date_stamp() -> str:
    return time.strftime('%Y%m%d', time.gmtime())


# ---------------------------------------------------------------------------
# Parsing helpers. Each returns (value, reason) so a caller can categorise the
# failure rather than discovering None and having to guess why.
# ---------------------------------------------------------------------------
def _as_float(value: Any) -> Tuple[Optional[float], str]:
    """Coerce to a finite float. Returns (value, '') or (None, reason)."""
    if value is None:
        return None, 'missing'
    if isinstance(value, bool):
        return None, 'unparseable'
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None, 'unparseable'
    if out != out or out in (float('inf'), float('-inf')):
        return None, 'unparseable'
    return out, ''


def derive_side(signed_size: float) -> str:
    """LONG for a positive signed size, SHORT for negative.

    Hyperliquid reports `szi` as a SIGNED coin quantity: '-4.16563' is short
    4.17 BTC. There is no separate side field, so the sign IS the side.

    A szi of exactly 0 is a flat position and has no side. It cannot reach a
    written row, because its positionValue is 0 and `min_notional` is required
    to be > 0, so it always drops as skipped_below_min_notional first. This
    function still returns SHORT for 0 rather than raising, because it is a pure
    helper and the gate that makes 0 unreachable lives in the caller.
    """
    return 'LONG' if signed_size > 0 else 'SHORT'


class PositionRow(object):
    """One whale position at one instant. Mirrors the table columns exactly."""

    __slots__ = ('ts', 'wallet', 'symbol', 'side', 'size_usd', 'entry_price',
                 'liq_price', 'leverage')

    def __init__(self, ts, wallet, symbol, side, size_usd, entry_price,
                 liq_price, leverage):
        self.ts = ts
        self.wallet = wallet
        self.symbol = symbol
        self.side = side
        self.size_usd = size_usd
        self.entry_price = entry_price
        self.liq_price = liq_price      # may be None - preserve it
        self.leverage = leverage        # may be None

    def as_tuple(self) -> Tuple:
        return (self.ts, self.wallet, self.symbol, self.side, self.size_usd,
                self.entry_price, self.liq_price, self.leverage)

    def __repr__(self):
        return ('PositionRow({} {} {} ${:,.0f} entry={} liq={} lev={})'
                .format(self.wallet[:10], self.symbol, self.side,
                        self.size_usd, self.entry_price, self.liq_price,
                        self.leverage))

    def __eq__(self, other):
        return isinstance(other, PositionRow) and self.as_tuple() == other.as_tuple()


def parse_clearinghouse_state(payload, wallet, ts,
                              symbols=DEFAULT_SYMBOLS,
                              min_notional=DEFAULT_MIN_NOTIONAL):
    """Turn one clearinghouseState response into (rows, counts).

    `counts` has 'seen', 'written' and every SKIP_REASONS key, and satisfies
    seen - sum(skipped_*) == written. The caller asserts that; this function
    guarantees it by construction, because every branch below either appends a
    row or increments exactly one skip counter. There is no bare `continue`.

    A payload that is not a dict, or has no assetPositions list, yields
    seen == 0 rather than raising: "the wallet returned nothing parseable" is
    the caller's fail_* problem, not a skip category.
    """
    counts = Counter({'seen': 0, 'written': 0})
    for reason in SKIP_REASONS:
        counts[reason] = 0
    rows: List[PositionRow] = []

    if not isinstance(payload, dict):
        return rows, counts
    asset_positions = payload.get('assetPositions')
    if not isinstance(asset_positions, list):
        return rows, counts

    symbol_set = set(symbols)

    for entry in asset_positions:
        counts['seen'] += 1

        # --- structure -----------------------------------------------------
        if not isinstance(entry, dict):
            counts['skipped_unparseable'] += 1
            continue
        pos = entry.get('position')
        if not isinstance(pos, dict):
            counts['skipped_unparseable'] += 1
            continue

        # --- symbol --------------------------------------------------------
        coin = pos.get('coin')
        if coin is None or coin == '':
            counts['skipped_missing_field'] += 1
            continue
        if not isinstance(coin, str):
            counts['skipped_unparseable'] += 1
            continue
        if coin not in symbol_set:
            counts['skipped_symbol_out_of_scope'] += 1
            continue

        # --- required numerics ---------------------------------------------
        szi, szi_err = _as_float(pos.get('szi'))
        notional, ntl_err = _as_float(pos.get('positionValue'))
        entry_px, px_err = _as_float(pos.get('entryPx'))

        errs = (szi_err, ntl_err, px_err)
        if 'missing' in errs:
            counts['skipped_missing_field'] += 1
            continue
        if 'unparseable' in errs:
            counts['skipped_unparseable'] += 1
            continue

        # --- the whale threshold -------------------------------------------
        size_usd = abs(notional)
        if size_usd < min_notional:
            counts['skipped_below_min_notional'] += 1
            continue

        # --- optional numerics: absence is DATA, not a defect ---------------
        # liquidationPx is null for positions that cannot be liquidated.
        # Preserve the None. Coercing it to 0.0 would say "liquidates at zero",
        # which a near_liq_trigger strategy would read as maximally distant.
        liq_price, _ = _as_float(pos.get('liquidationPx'))

        lev_raw = pos.get('leverage')
        if isinstance(lev_raw, dict):
            leverage, _ = _as_float(lev_raw.get('value'))
        else:
            leverage, _ = _as_float(lev_raw)

        rows.append(PositionRow(
            ts=ts,
            wallet=wallet,
            symbol=coin,
            side=derive_side(szi),
            size_usd=size_usd,
            entry_price=entry_px,
            liq_price=liq_price,
            leverage=leverage,
        ))
        counts['written'] += 1

    return rows, counts


def assert_accounting_identity(counts) -> None:
    """seen - sum(skipped_*) == written, or raise (convention 20).

    Never 'repaired' by adjusting a counter to match.
    """
    skipped = sum(v for k, v in counts.items() if k.startswith('skipped_'))
    assert counts['seen'] - skipped == counts['written'], (
        'hyperliquid position accounting identity broken: {}'.format(dict(counts)))


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------
class HyperliquidClient(object):
    """Read-only HTTP client for the Hyperliquid public endpoints.

    `session`, `sleep_fn` and `rng` are injectable so tests run fully offline,
    without real sleeps, and deterministically. Failures return None and are
    counted in `self.stats` by cause; nothing here raises on a network problem,
    and nothing returns an empty list to mean "request failed" (convention 11).
    """

    def __init__(self, session=None, timeout=DEFAULT_TIMEOUT,
                 retries=DEFAULT_RETRIES, sleep_fn=None, rng=None):
        if session is None:
            if requests is None:  # pragma: no cover
                raise RuntimeError('requests is not installed and no session was injected')
            session = requests.Session()
            session.headers.update({'User-Agent': USER_AGENT,
                                    'Content-Type': 'application/json'})
        self.session = session
        self.timeout = float(timeout)
        self.retries = max(1, int(retries))
        self._sleep = sleep_fn or time.sleep
        self._rng = rng or random.Random()
        self.stats: Counter = Counter()

    # -- internals ---------------------------------------------------------
    def _backoff_sec(self, attempt: int) -> float:
        base = min(RETRY_BACKOFF_SEC * (2 ** attempt), MAX_BACKOFF_SEC)
        return base + self._rng.uniform(0.0, BACKOFF_JITTER_SEC)

    @staticmethod
    def _retry_after(resp, fallback: float) -> float:
        """Honour Retry-After only when it is numeric, positive and sane."""
        try:
            raw = resp.headers.get('Retry-After')
        except Exception:
            return fallback
        if raw is None:
            return fallback
        try:
            secs = float(raw)
        except (TypeError, ValueError):
            return fallback
        if secs <= 0:
            return fallback
        return min(secs, MAX_BACKOFF_SEC)

    def post_info(self, body: Dict[str, Any]) -> Optional[Any]:
        """POST one /info request. Returns parsed JSON, or None on failure.

        Retries on 429 and 5xx with exponential backoff + jitter. Does NOT
        retry other 4xx: a 422 means the request type or its arguments are
        wrong, and retrying a malformed request is extra load for the same
        answer.
        """
        req_type = body.get('type', '?')
        for attempt in range(self.retries):
            is_last = attempt == self.retries - 1
            self.stats['requests'] += 1
            try:
                resp = self.session.post(INFO_URL, json=body, timeout=self.timeout)
            except Exception as exc:
                if is_last:
                    self.stats['fail_network'] += 1
                    logger.warning('HL %s: network failure after %d attempts: %s',
                                   req_type, self.retries, exc)
                    return None
                self.stats['retries'] += 1
                self._sleep(self._backoff_sec(attempt))
                continue

            code = getattr(resp, 'status_code', None)
            if code == 200:
                try:
                    return resp.json(parse_constant=_reject_non_finite)
                except NonFiniteJSONError as exc:
                    self.stats['fail_non_finite_json'] += 1
                    logger.warning('HL %s: %s', req_type, exc)
                    return None
                except (ValueError, TypeError) as exc:
                    self.stats['fail_bad_json'] += 1
                    logger.warning('HL %s: unparseable JSON: %s', req_type, exc)
                    return None

            if code == 429 or (isinstance(code, int) and code >= 500):
                self.stats['http_429' if code == 429 else 'http_5xx'] += 1
                if is_last:
                    self.stats['fail_http_429' if code == 429 else 'fail_http_5xx'] += 1
                    logger.warning('HL %s: HTTP %s after %d attempts, giving up',
                                   req_type, code, self.retries)
                    return None
                wait = self._backoff_sec(attempt)
                if code == 429:
                    wait = self._retry_after(resp, wait)
                self.stats['retries'] += 1
                logger.info('HL %s: HTTP %s, retrying in %.2fs', req_type, code, wait)
                self._sleep(wait)
                continue

            # Any other status: definitive.
            self.stats['fail_http_4xx'] += 1
            logger.warning('HL %s: HTTP %s (not retried)', req_type, code)
            return None

        return None  # pragma: no cover - the loop always returns

    # -- public calls ------------------------------------------------------
    def clearinghouse_state(self, wallet: str) -> Optional[Any]:
        """Open positions for ONE address. `user` is mandatory (422 without)."""
        return self.post_info({'type': 'clearinghouseState', 'user': wallet})

    def fetch_leaderboard_wallets(self, top_n: int = DEFAULT_TOP_N) -> Optional[List[str]]:
        """Top-N addresses by leaderboard accountValue, or None on failure.

        None means "could not fetch" and is NEVER an empty list (convention
        11). An empty list would mean the leaderboard genuinely had no rows.

        Ranking caveat: see caveat 3 in the module docstring. The top entries
        are frequently stale ghost accounts holding nothing.
        """
        try:
            resp = self.session.get(LEADERBOARD_URL, timeout=LEADERBOARD_TIMEOUT)
        except Exception as exc:
            self.stats['fail_leaderboard_network'] += 1
            logger.warning('HL leaderboard: network failure: %s', exc)
            return None

        code = getattr(resp, 'status_code', None)
        if code != 200:
            self.stats['fail_leaderboard_http'] += 1
            logger.warning('HL leaderboard: HTTP %s', code)
            return None
        try:
            payload = resp.json(parse_constant=_reject_non_finite)
        except (ValueError, TypeError) as exc:
            self.stats['fail_leaderboard_json'] += 1
            logger.warning('HL leaderboard: unparseable JSON: %s', exc)
            return None

        rows = payload.get('leaderboardRows') if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            self.stats['fail_leaderboard_shape'] += 1
            logger.warning('HL leaderboard: no leaderboardRows list in payload')
            return None

        ranked = []
        dropped = 0
        for r in rows:
            if not isinstance(r, dict):
                dropped += 1
                continue
            addr = r.get('ethAddress')
            av, err = _as_float(r.get('accountValue'))
            if not isinstance(addr, str) or not addr or err:
                dropped += 1
                continue
            ranked.append((av, addr))
        ranked.sort(reverse=True)

        # De-duplicate while preserving rank order: the same address can appear
        # more than once across leaderboard windows.
        seen = OrderedDict()
        for _, addr in ranked:
            seen.setdefault(addr, None)
            if len(seen) >= top_n:
                break

        logger.info('HL leaderboard: %d rows, %d unusable, taking top %d by '
                    'accountValue (a STALE field - see module docstring caveat 3)',
                    len(rows), dropped, len(seen))
        return list(seen.keys())


# ---------------------------------------------------------------------------
# Wallet sourcing
# ---------------------------------------------------------------------------
def load_wallets_file(path: str) -> List[str]:
    """Read a newline-delimited wallet list. '#' comments and blanks ignored."""
    wallets: List[str] = []
    with open(path) as fh:
        for line in fh:
            line = line.split('#', 1)[0].strip()
            if line:
                wallets.append(line)
    return wallets


def _read_wallet_cache(path: str, max_age_sec: float) -> Optional[List[str]]:
    try:
        with open(path) as fh:
            blob = json.load(fh)
    except (IOError, OSError, ValueError):
        return None
    if not isinstance(blob, dict):
        return None
    fetched = blob.get('fetched_ts')
    wallets = blob.get('wallets')
    if not isinstance(wallets, list) or not isinstance(fetched, (int, float)):
        return None
    age = time.time() - fetched
    if age > max_age_sec:
        logger.info('HL leaderboard cache is %.0fs old (max %.0fs), refreshing',
                    age, max_age_sec)
        return None
    return [w for w in wallets if isinstance(w, str)]


def _write_wallet_cache(path: str, wallets: Sequence[str]) -> None:
    directory = os.path.dirname(path)
    if directory:
        try:
            os.makedirs(directory)
        except OSError:
            pass
    tmp = path + '.tmp'
    try:
        with open(tmp, 'w') as fh:
            json.dump({'fetched_ts': time.time(), 'source': LEADERBOARD_URL,
                       'wallets': list(wallets)}, fh, indent=2, allow_nan=False)
        os.rename(tmp, path)
    except (IOError, OSError) as exc:
        logger.warning('HL: could not write wallet cache %s: %s', path, exc)


def resolve_wallets(client, wallets_file=None, top_n=DEFAULT_TOP_N,
                    cache_path=DEFAULT_LEADERBOARD_CACHE,
                    max_age_sec=DEFAULT_LEADERBOARD_MAX_AGE_SEC,
                    discover=True):
    """Decide which addresses to poll. Returns (wallets, source_description).

    Precedence: an explicit --wallets file wins outright. Otherwise use the
    cached leaderboard if it is both FRESH and LARGE ENOUGH, else refetch. If
    discovery fails and there is no cache, returns ([], reason) so the caller
    reports a poll that COULD NOT RUN, rather than an empty result that reads
    as "no whales" (convention 11).
    """
    if wallets_file:
        wallets = load_wallets_file(wallets_file)
        return wallets, 'file:{} ({} wallets)'.format(wallets_file, len(wallets))

    if not discover:
        return [], 'discovery disabled and no --wallets file given'

    cached = _read_wallet_cache(cache_path, max_age_sec)
    if cached is not None and len(cached) >= top_n:
        logger.info('HL leaderboard cache hit: %d wallets cached, using top %d',
                    len(cached), top_n)
        return cached[:top_n], 'leaderboard cache ({})'.format(cache_path)
    if cached is not None:
        # A cache built by an earlier, smaller --top-n must not silently cap a
        # larger request. Refetch instead of quietly returning too few.
        logger.info('HL leaderboard cache holds %d wallets but %d were asked '
                    'for, refetching', len(cached), top_n)

    want = max(top_n, LEADERBOARD_CACHE_WALLETS)
    fetched = client.fetch_leaderboard_wallets(top_n=want)
    if fetched is None:
        stale = _read_wallet_cache(cache_path, float('inf'))
        if stale:
            logger.warning('HL: leaderboard fetch FAILED, falling back to a '
                           'STALE cache of %d wallets', len(stale))
            return stale[:top_n], 'STALE leaderboard cache (fetch failed)'
        return [], 'leaderboard fetch FAILED and no cache available'

    _write_wallet_cache(cache_path, fetched)
    return fetched[:top_n], 'leaderboard live fetch (top {} of {} cached)'.format(
        top_n, len(fetched))


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
class HyperliquidStore(object):
    """sqlite writer for hyperliquid_positions.

    db/trading.db is shared with a live shadow loop, so this class:
      * sets busy_timeout, so a concurrent writer produces a WAIT, not an error
      * keeps every write to a single short executemany transaction
      * READS journal_mode and logs it, and never sets it
    """

    def __init__(self, db_path=DEFAULT_DB_PATH):
        self.db_path = db_path
        directory = os.path.dirname(db_path)
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        self.conn = sqlite3.connect(db_path, timeout=SQLITE_CONNECT_TIMEOUT_SEC)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute('PRAGMA busy_timeout={};'.format(SQLITE_BUSY_TIMEOUT_MS))
        mode = self.conn.execute('PRAGMA journal_mode;').fetchone()[0]
        # Deliberately a READ. Switching journal_mode on a db another process
        # holds open is how you break somebody else's session.
        logger.info('HL store: %s journal_mode=%s (observed, not set)', db_path, mode)
        self.journal_mode = mode
        self.ensure_schema()

    def ensure_schema(self) -> None:
        with self.conn:
            self.conn.executescript(SCHEMA_SQL)

    def write_positions(self, rows: Sequence[PositionRow]) -> int:
        if not rows:
            return 0
        payload = [r.as_tuple() for r in rows]
        with self.conn:
            self.conn.executemany(
                'INSERT INTO hyperliquid_positions '
                '(ts, wallet, symbol, side, size_usd, entry_price, liq_price, leverage) '
                'VALUES (?, ?, ?, ?, ?, ?, ?, ?)', payload)
        return len(payload)

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


# ---------------------------------------------------------------------------
# Poller
# ---------------------------------------------------------------------------
class HyperliquidPoller(object):
    """One poll = clearinghouseState for every resolved wallet, then one write."""

    def __init__(self, client, store=None, symbols=DEFAULT_SYMBOLS,
                 min_notional=DEFAULT_MIN_NOTIONAL, dry_run=False,
                 sleep_fn=None):
        self.client = client
        self.store = store
        self.symbols = tuple(symbols)
        self.min_notional = float(min_notional)
        self.dry_run = dry_run
        self._sleep = sleep_fn or time.sleep
        self.health: Counter = Counter()   # OUTSIDE the identity, on purpose

    def poll_once(self, wallets: Sequence[str], ts=None) -> Dict[str, Any]:
        ts = int(time.time()) if ts is None else int(ts)
        totals = Counter({'seen': 0, 'written': 0})
        for reason in SKIP_REASONS:
            totals[reason] = 0

        all_rows: List[PositionRow] = []
        wallets_ok = 0
        wallets_failed = 0
        wallets_empty = 0

        for i, wallet in enumerate(wallets):
            payload = self.client.clearinghouse_state(wallet)
            if payload is None:
                # Convention 11: this is "could not run", not "no positions".
                wallets_failed += 1
                self.health['wallet_fetch_failed'] += 1
                continue
            wallets_ok += 1
            rows, counts = parse_clearinghouse_state(
                payload, wallet, ts, symbols=self.symbols,
                min_notional=self.min_notional)
            assert_accounting_identity(counts)
            if counts['seen'] == 0:
                # Fetched fine, genuinely holds nothing. Distinct from a failure.
                wallets_empty += 1
            totals.update(counts)
            all_rows.extend(rows)
            if i + 1 < len(wallets) and INTER_REQUEST_SLEEP_SEC:
                self._sleep(INTER_REQUEST_SLEEP_SEC)

        # The identity must hold over the SUM, not just per wallet.
        assert_accounting_identity(totals)

        written = 0
        if self.dry_run:
            self.health['dry_run_polls'] += 1
        elif self.store is not None:
            try:
                written = self.store.write_positions(all_rows)
            except sqlite3.Error as exc:
                self.health['db_write_failed'] += 1
                logger.error('HL: DB write FAILED for %d rows: %s', len(all_rows), exc)

        skipped = sum(totals[r] for r in SKIP_REASONS)
        logger.info(
            'HL POLL ts=%d wallets=%d ok=%d empty=%d failed=%d | positions '
            'seen=%d kept=%d skipped=%d (scope=%d below_min=%d missing=%d '
            'unparseable=%d) | rows_written=%d%s',
            ts, len(wallets), wallets_ok, wallets_empty, wallets_failed,
            totals['seen'], totals['written'], skipped,
            totals['skipped_symbol_out_of_scope'], totals['skipped_below_min_notional'],
            totals['skipped_missing_field'], totals['skipped_unparseable'],
            written, ' (DRY RUN, nothing written)' if self.dry_run else '')

        return {'ts': ts, 'counts': totals, 'rows': all_rows,
                'wallets_ok': wallets_ok, 'wallets_failed': wallets_failed,
                'wallets_empty': wallets_empty, 'written': written}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _setup_logging(log_dir: str, verbose: bool) -> Optional[str]:
    """Console + logs/hyperliquid_client_<UTCdate>.log."""
    level = logging.DEBUG if verbose else logging.INFO
    fmt = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    root = logging.getLogger()
    root.setLevel(level)

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    if not log_dir:
        return None
    try:
        if not os.path.isdir(log_dir):
            os.makedirs(log_dir)
        path = os.path.join(log_dir, 'hyperliquid_client_{}.log'.format(_utc_date_stamp()))
        fh = logging.FileHandler(path)
        fh.setFormatter(fmt)
        root.addHandler(fh)
        return path
    except (IOError, OSError) as exc:
        root.warning('could not open log file in %s: %s', log_dir, exc)
        return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Poll Hyperliquid for large open positions (read-only).')
    p.add_argument('--symbols', default=','.join(DEFAULT_SYMBOLS),
                   help='comma-separated coins to keep (default: %(default)s)')
    p.add_argument('--min-notional', type=float, default=DEFAULT_MIN_NOTIONAL,
                   help='USD notional floor, must be > 0 (default: %(default)s)')
    p.add_argument('--interval', type=float, default=DEFAULT_INTERVAL_SEC,
                   help='seconds between polls (default: %(default)s)')
    p.add_argument('--wallets', default=None,
                   help='file of addresses, one per line, # comments allowed. '
                        'Overrides leaderboard discovery entirely.')
    p.add_argument('--top-n', type=int, default=DEFAULT_TOP_N,
                   help='how many leaderboard addresses to poll. A COVERAGE '
                        'knob, not a definition of "whale" (default: %(default)s)')
    p.add_argument('--no-discover', action='store_true',
                   help='do not use the leaderboard; require --wallets')
    p.add_argument('--leaderboard-cache', default=DEFAULT_LEADERBOARD_CACHE,
                   help='(default: %(default)s)')
    p.add_argument('--leaderboard-max-age', type=float,
                   default=DEFAULT_LEADERBOARD_MAX_AGE_SEC,
                   help='refetch the 34MB leaderboard when the cache is older '
                        'than this many seconds (default: %(default)s)')
    p.add_argument('--db', default=DEFAULT_DB_PATH, help='(default: %(default)s)')
    p.add_argument('--log-dir', default=DEFAULT_LOG_DIR, help='(default: %(default)s)')
    p.add_argument('--once', action='store_true', help='one poll then exit')
    p.add_argument('--dry-run', action='store_true',
                   help='fetch and parse but write nothing to the database')
    p.add_argument('--timeout', type=float, default=DEFAULT_TIMEOUT,
                   help='(default: %(default)s)')
    p.add_argument('--retries', type=int, default=DEFAULT_RETRIES,
                   help='(default: %(default)s)')
    p.add_argument('--verbose', action='store_true')
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    log_path = _setup_logging(args.log_dir, args.verbose)

    if args.min_notional <= 0:
        logger.error('REFUSING TO START: --min-notional must be > 0 '
                     '(a zero floor would admit flat, sideless positions)')
        return 1
    if args.no_discover and not args.wallets:
        logger.error('REFUSING TO START: --no-discover needs --wallets, '
                     'otherwise there is nothing to poll')
        return 1

    symbols = tuple(s.strip().upper() for s in args.symbols.split(',') if s.strip())
    if not symbols:
        logger.error('REFUSING TO START: --symbols resolved to nothing')
        return 1

    logger.info('HL feed starting: symbols=%s min_notional=$%.0f interval=%.0fs '
                'top_n=%d db=%s dry_run=%s log=%s',
                ','.join(symbols), args.min_notional, args.interval,
                args.top_n, args.db, args.dry_run, log_path)

    client = HyperliquidClient(timeout=args.timeout, retries=args.retries)
    store = None
    if not args.dry_run:
        store = HyperliquidStore(args.db)
    poller = HyperliquidPoller(client, store=store, symbols=symbols,
                               min_notional=args.min_notional,
                               dry_run=args.dry_run)

    exit_code = 0
    try:
        while True:
            wallets, source = resolve_wallets(
                client, wallets_file=args.wallets, top_n=args.top_n,
                cache_path=args.leaderboard_cache,
                max_age_sec=args.leaderboard_max_age,
                discover=not args.no_discover)
            logger.info('HL wallet source: %s -> %d addresses', source, len(wallets))

            if not wallets:
                # Convention 11 again: say COULD NOT RUN.
                logger.error('HL POLL NOT RUN: no wallets resolved (%s). This is '
                             'a discovery failure, not "no whales found".', source)
                exit_code = 1
                if args.once:
                    break
            else:
                poller.poll_once(wallets)
                exit_code = 0

            if args.once:
                break
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info('HL feed: interrupted, shutting down')
    finally:
        if store is not None:
            store.close()
        logger.info('HL feed stats: %s | health: %s',
                    dict(client.stats), dict(poller.health))

    return exit_code


if __name__ == '__main__':
    raise SystemExit(main())
