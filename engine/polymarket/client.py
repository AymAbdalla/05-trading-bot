"""Read-only HTTP client for Polymarket's three public APIs.

Gamma (discovery), CLOB (books and price history), Data (trades, open
interest). None of them need auth for reads. Order placement needs a wallet and
EIP-712 signing and is deliberately NOT implemented here - see D-267. This
client refuses anything that is not a GET, so a future edit cannot turn the
data layer into an execution path by accident.

Four things this does that a bare `requests.get` does not:
  - Rate limits per host, using each host's documented budget. The limits are
    generous (CLOB allows 9,000 req/10s) but a 5-minute market loop polling
    books for a dozen tokens will find them.
  - Retries transient failures with backoff, and does NOT retry 4xx. A 404 on a
    market slug means the market is not indexed yet, which is information, not
    an error to paper over.
  - Rejects a 200 whose body contains `Infinity`/`NaN`. `json.loads` accepts
    both (convention 19), so without `parse_constant` a corrupt payload decodes
    to a float that no other language's JSON parser can round-trip.
  - Returns None rather than raising on a failed read, and logs why. Every
    caller here has to decide "skip this window" anyway, and a strategy that
    trades on a missing feed is worse than one that skips.
"""
import json
import logging
import threading
import time
from collections import deque
from typing import Any, Deque, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

GAMMA_HOST = 'https://gamma-api.polymarket.com'
CLOB_HOST = 'https://clob.polymarket.com'
DATA_HOST = 'https://data-api.polymarket.com'

# Documented budgets, as (max_requests, per_seconds). We run at 80% of each so
# a burst from another process on the same IP does not put us over.
RATE_LIMITS: Dict[str, tuple] = {
    GAMMA_HOST: (4000, 10.0),
    CLOB_HOST: (9000, 10.0),
    DATA_HOST: (1000, 10.0),
}
RATE_LIMIT_HEADROOM = 0.8

DEFAULT_TIMEOUT = 10.0
DEFAULT_RETRIES = 3
RETRY_BACKOFF_SEC = 0.5
# A server that asks us to wait ten minutes is not worth honouring inside a
# 5-minute market loop; cap it and let the caller skip the window.
MAX_RETRY_AFTER_SEC = 30.0


class NonFiniteJSONError(ValueError):
    """A 200 response contained a bare `Infinity`, `-Infinity` or `NaN`."""


def _reject_non_finite(token: str) -> float:
    raise NonFiniteJSONError(
        f'response contained the non-finite JSON constant {token!r}; '
        'this payload is not portable JSON (convention 19)')


class RateLimiter:
    """Sliding-window limiter, one per host. Thread-safe."""

    def __init__(self, max_requests: int, per_seconds: float):
        self.max_requests = max(1, int(max_requests * RATE_LIMIT_HEADROOM))
        self.per_seconds = per_seconds
        self._hits: Deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> float:
        """Block until a slot is free. Returns seconds waited."""
        waited = 0.0
        while True:
            with self._lock:
                now = time.monotonic()
                while self._hits and now - self._hits[0] > self.per_seconds:
                    self._hits.popleft()
                if len(self._hits) < self.max_requests:
                    self._hits.append(now)
                    return waited
                sleep_for = self.per_seconds - (now - self._hits[0]) + 0.001
            time.sleep(sleep_for)
            waited += sleep_for


class PolymarketClient:
    """Read-only client. All three hosts, one connection pool, one retry policy."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT,
                 retries: int = DEFAULT_RETRIES,
                 session: Optional[requests.Session] = None):
        self.timeout = timeout
        # retries=0 would mean "never send the request", which is never what a
        # caller means by it.
        self.retries = max(1, int(retries))
        self.session = session or requests.Session()
        self.session.headers.update({'User-Agent': '05-trading-bot/paper (read-only)'})
        self._limiters = {host: RateLimiter(*limits)
                          for host, limits in RATE_LIMITS.items()}
        # Counters so a run can report how much it actually asked for. A silent
        # retry storm looks identical to a healthy run without these.
        # `failures` is split by cause: a rate-limited run and a run hitting
        # 404s on unindexed slugs need completely different responses, and one
        # combined number cannot tell them apart (convention 20).
        self.stats: Dict[str, int] = {
            'requests': 0, 'retries': 0, 'failures': 0, 'rate_limit_waits': 0,
            'fail_network': 0, 'fail_http_4xx': 0, 'fail_http_5xx': 0,
            'fail_bad_json': 0, 'fail_non_finite_json': 0,
        }

    # -- core ---------------------------------------------------------------

    @staticmethod
    def _retry_after(resp: requests.Response, fallback: float) -> float:
        """Honour `Retry-After` when the server sends a sane one."""
        raw = resp.headers.get('Retry-After') if resp.headers else None
        if not raw:
            return fallback
        try:
            secs = float(str(raw).strip())
        except (TypeError, ValueError):
            return fallback  # HTTP-date form; not worth parsing for our budgets
        if secs <= 0:
            return fallback
        return min(secs, MAX_RETRY_AFTER_SEC)

    def get(self, host: str, path: str,
            params: Optional[dict] = None) -> Optional[Any]:
        """GET and decode JSON. Returns None on any failure, having logged it.

        Retries only on network errors, 429, and 5xx. A 4xx other than 429 is a
        real answer from the server (bad slug, unknown token) and retrying it
        just burns budget.
        """
        limiter = self._limiters.get(host)
        url = f"{host.rstrip('/')}/{path.lstrip('/')}"

        last_err = None
        for attempt in range(self.retries):
            is_last = attempt == self.retries - 1

            if limiter is not None:
                waited = limiter.acquire()
                if waited:
                    self.stats['rate_limit_waits'] += 1

            self.stats['requests'] += 1
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                last_err = f'{type(exc).__name__}: {exc}'
                if is_last:
                    self.stats['fail_network'] += 1
                    break
                # Only count a retry we are actually about to perform, and only
                # sleep when another attempt follows. Sleeping after the last
                # attempt burned up to 2s per hard failure for nothing, which a
                # 5-minute window loop cannot spare.
                self.stats['retries'] += 1
                time.sleep(RETRY_BACKOFF_SEC * (2 ** attempt))
                continue

            if resp.status_code == 200:
                try:
                    return resp.json(parse_constant=_reject_non_finite)
                except NonFiniteJSONError as exc:
                    last_err = str(exc)
                    self.stats['failures'] += 1
                    self.stats['fail_non_finite_json'] += 1
                    logger.error('polymarket GET %s: %s', url, last_err)
                    return None
                except ValueError as exc:
                    # A 200 with unparseable content is a failure, not empty
                    # data. Never return {} here (convention 11).
                    # json.JSONDecodeError subclasses ValueError.
                    last_err = f'bad JSON: {exc}'
                    self.stats['failures'] += 1
                    self.stats['fail_bad_json'] += 1
                    logger.error('polymarket GET %s: %s', url, last_err)
                    return None

            if resp.status_code == 429 or resp.status_code >= 500:
                last_err = f'HTTP {resp.status_code}'
                if is_last:
                    key = ('fail_http_4xx' if resp.status_code < 500
                           else 'fail_http_5xx')
                    self.stats[key] += 1
                    break
                backoff = RETRY_BACKOFF_SEC * (2 ** attempt)
                if resp.status_code == 429:
                    backoff = self._retry_after(resp, backoff)
                self.stats['retries'] += 1
                time.sleep(backoff)
                continue

            # 4xx: a definitive answer. Do not retry.
            self.stats['failures'] += 1
            self.stats['fail_http_4xx'] += 1
            logger.debug('polymarket GET %s -> HTTP %s (not retried)',
                         url, resp.status_code)
            return None

        self.stats['failures'] += 1
        logger.warning('polymarket GET %s failed after %d attempts: %s',
                       url, self.retries, last_err)
        return None

    # -- Gamma --------------------------------------------------------------

    def gamma(self, path: str, params: Optional[dict] = None) -> Optional[Any]:
        return self.get(GAMMA_HOST, path, params)

    def clob(self, path: str, params: Optional[dict] = None) -> Optional[Any]:
        return self.get(CLOB_HOST, path, params)

    def data(self, path: str, params: Optional[dict] = None) -> Optional[Any]:
        return self.get(DATA_HOST, path, params)

    # -- convenience --------------------------------------------------------

    def close(self) -> None:
        self.session.close()

    def __enter__(self) -> 'PolymarketClient':
        return self

    def __exit__(self, *exc) -> None:
        self.close()


def parse_embedded_json(value: Any, default: Any = None) -> Any:
    """Gamma double-encodes `outcomes`, `outcomePrices`, `clobTokenIds`.

    They arrive as JSON strings INSIDE a JSON document, so `market['outcomes']`
    is the literal string '["Up", "Down"]'. Already-decoded lists pass through
    unchanged, because Gamma is not perfectly consistent about this across
    endpoints and a client that assumes one shape breaks on the other.

    Handles, in order: missing (None), already-decoded list/dict, empty or
    whitespace-only string, and undecodable string. Everything that is not a
    successful decode returns `default`.
    """
    decoded, _ok = parse_embedded_json_checked(value, default)
    return decoded


def parse_embedded_json_checked(value: Any, default: Any = None
                                ) -> Tuple[Any, str]:
    """`parse_embedded_json` that also says WHY it returned the default.

    Returns `(value, status)` where status is one of `ok`, `passthrough`,
    `missing`, `empty`, `undecodable`. Callers that filter on the result must
    report which of these they hit rather than dropping the record silently
    (convention 20) - "the field was absent" and "the field was corrupt" are
    different problems with different fixes.
    """
    if value is None:
        return default, 'missing'
    if isinstance(value, (list, dict)):
        return value, 'passthrough'
    if isinstance(value, str):
        if not value.strip():
            return default, 'empty'
        try:
            return json.loads(value), 'ok'
        except ValueError:
            logger.debug('could not decode embedded JSON: %r', value[:120])
            return default, 'undecodable'
    return default, 'undecodable'


def parse_embedded_list(value: Any) -> Tuple[List[Any], str]:
    """Decode an embedded field that MUST be a list, e.g. `clobTokenIds`.

    `parse_embedded_json` alone is not enough here. A Gamma field carrying the
    string `'"Yes"'` decodes cleanly to the str `"Yes"`, which is truthy and
    has a `len()` of 3 - so a caller zipping it against `clobTokenIds` builds
    three bogus one-character outcomes instead of refusing the market. Same for
    a bare number. Anything that does not decode to a list is `not_a_list`.
    """
    decoded, status = parse_embedded_json_checked(value, default=None)
    if decoded is None:
        return [], status
    if not isinstance(decoded, list):
        return [], 'not_a_list'
    return decoded, status
