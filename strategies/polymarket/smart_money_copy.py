"""Smart Money Copy: mirror a BUY made by a wallet with a MEASURED record.

Concept borrowed from MrFadiAi's public Polymarket bot ("copy the profitable
wallets"). The concept is all that is borrowed. His thresholds, his wallet list
and his claimed returns are not evidence and none of them is wired into a gate
here (convention 3).

    a tracked wallet BUYS an outcome in the market we are looking at
        -> the wallet's OWN measured record clears the gate
        -> the trade is fresh enough to still be the whale and not the price
        -> we mirror the SIDE, at OUR size, and hold to resolution

## THE HANDLE PROBLEM, which is the reason most of this file exists

The wallet list comes from a Dan1ro0 article and is a list of DISPLAY HANDLES:
bonereaper, boneohio, coinfilippe, doggystyie, Sharky6999, plus two entries
that are address PREFIXES and not addresses (0x50f7, 0xaaaaa).

The Polymarket Data API takes `?user=<proxy wallet address>`. It does not take
a handle and it does not take a prefix. A 4-hex-character prefix is 16 bits of
a 160-bit address, so it identifies roughly one address in four billion of the
remaining space: it is a fingerprint for confirming a match, never a key for
finding one.

So TRACKED_WALLETS maps handle -> address, and today EVERY VALUE IS None,
because we do not have a single one of these addresses. Nothing here guesses,
derives, or substitutes one. A wallet we cannot resolve is skipped with its own
reason `wallet_address_unresolved`, it is COUNTED, and it is categorised into
`unresolved_prefix_only` (we have 4 hex characters and nothing else) versus
`unresolved_no_address` (we have a display name and nothing else). Two different
problems: the first needs an address search, the second needs somebody to ask
the article's author. Pooling them into one number would hide that (convention
20), and the accounting identity

    resolved + unresolved_prefix_only + unresolved_no_address == len(TRACKED_WALLETS)

is stamped on every decision row this strategy emits and asserted in
`tests/test_smart_money_copy.py`.

Practical consequence, stated plainly rather than discovered from an empty log:
**with the list as shipped this strategy cannot enter.** It skips
`wallet_address_unresolved` on every cycle. That is NOT_TESTED, not
tested-and-found-nothing (convention 11). Fill in one real address and the rest
of the path is live.

## THE WIN RATE GATE, and the one thing that would make this file dishonest

The gate is "win rate above 60% over more than 50 trades". Those two numbers
are ours and they are assumptions with an expiry date (convention 17). The
number that would NOT be ours is the article's claimed win rate for these
wallets, and copying that into the gate would be fabricating evidence: it is a
blog post about somebody else's wallet, we have never seen their fills, and a
strategy that reads its own gate off a screenshot has no gate at all.

So `WalletRecord` is computed from SETTLED trades the feed actually supplies. If
the feed cannot supply settlement, the record is UNMEASURED and the wallet is
skipped with `wallet_record_unmeasured`. There is no fallback, no default win
rate, and no "assume the article".

And the honest state of that today: Polymarket's public `/trades` endpoint
returns fills, not outcomes. It carries no `won`, no `realized_pnl` and no
redemption flag, so `WalletTradeFeed.fetch_record` returns None against the live
API and this gate refuses every wallet. Wiring a settled-history source is a
separate task and it is not done. `record_from_rows` is written against explicit
settlement keys so that when such a source exists it plugs in without the gate
being loosened to meet it.

## WHAT THIS STRATEGY CANNOT SEE (convention 22)

  - **Whether the whale is still in.** We see BUY fills. We do not see their
    position, their hedge on another venue, or a SELL they placed one second
    after the fill we copied. Mirroring a leg of a spread as a naked directional
    binary is a real failure mode and nothing here can detect it.
  - **Whether the fill we are reading is the whale's own idea.** Copy trading is
    a crowded strategy. If ten bots mirror the same wallet, the price we lift is
    partly their impact, which is a cost that does not appear anywhere in this
    file.
  - **Whether our Decision became a fill.** The halt check, the risk gate and
    the paper adapter all sit downstream and any of them can refuse. Counters
    here are ATTEMPTS.

## Deduplication, and why a poll loop needs it

The shadow loop polls every few seconds and the Data API returns the same
recent trade on every poll. Without dedupe one whale BUY becomes twenty copies
of itself, which would look in a graveyard like twenty independent signals
agreeing. Copied trade ids are remembered (bounded) and a repeat is refused with
`already_copied_this_trade`.

## Exits

Holds to resolution. `manages_exits = False`. On a binary the stop is exactly
0.00, which is strictly below any entry premium and satisfies convention 8.
Positions from this strategy belong in the RESOLUTION population, never pooled
with the fair-value family's SELL population.

KILL CONDITION: trailing-30 copied-trade win rate below 50%, once 30 copied
trades exist, scored by `backtest/polymarket_harness.py` over the
`PM_smart_money_copy` population alone. 50% is not an arbitrary line: we buy at
the ask, so a copied binary bought at premium p needs a win rate above p to
break even, and the entry band below caps p at 0.95. A sub-50% result over 30
trades says the wallets are not predictive at OUR latency even if they are
predictive at theirs, which is the specific thing this strategy is betting on
and the specific thing it cannot verify in advance.
"""
import json
import logging
import math
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from strategies.polymarket.base import (Decision, Leg, MarketContext,
                                        PolymarketStrategy, effective_ask_for)

logger = logging.getLogger(__name__)

# Never False in this repo. Nothing here has live-trading authority, and this
# module imports no wallet, no signer and no order path.
PAPER_MODE = True

# ---------------------------------------------------------------------------
# The wallet list. Read the HANDLE PROBLEM section above before editing it.
# ---------------------------------------------------------------------------

#: handle -> Polymarket PROXY WALLET ADDRESS, or None when we do not have one.
#: Every value is None today. A None here is not a placeholder to be filled with
#: a guess; it is the reason a wallet gets skipped and counted.
TRACKED_WALLETS: Dict[str, Optional[str]] = {
    'bonereaper': None,
    '0x50f7': None,
    'boneohio': None,
    'coinfilippe': None,
    '0xaaaaa': None,
    'doggystyie': None,
    'Sharky6999': None,
}

#: handle -> the address PREFIX the source gave us, for the two entries that
#: are prefixes rather than names. Kept separate from TRACKED_WALLETS on
#: purpose: a prefix is a fingerprint for confirming a candidate address, and
#: putting it in the address slot is how it would end up in a query string.
TRACKED_WALLET_PREFIXES: Dict[str, str] = {
    '0x50f7': '0x50f7',
    '0xaaaaa': '0xaaaaa',
}

#: An EVM address is '0x' plus 40 hex characters. Anything shorter is a prefix.
ADDRESS_LENGTH = 42

RESOLVED = 'resolved'
UNRESOLVED_PREFIX_ONLY = 'unresolved_prefix_only'
UNRESOLVED_NO_ADDRESS = 'unresolved_no_address'

#: Every resolution status. A new one must be added here or the identity check
#: in `resolve_tracked_wallets` will refuse to balance.
RESOLUTION_STATUSES = (RESOLVED, UNRESOLVED_PREFIX_ONLY, UNRESOLVED_NO_ADDRESS)

# ---------------------------------------------------------------------------
# Feed constants
# ---------------------------------------------------------------------------

DATA_API_HOST = 'https://data-api.polymarket.com'
TRADES_PATH = '/trades'

#: Short on purpose. This runs inside a 5-minute window loop that polls every
#: few seconds; a feed that blocks for 10 seconds has already made the trade
#: stale by the time it answers.
DEFAULT_FEED_TIMEOUT_SEC = 2.0

#: Bounded. Two attempts, one backoff. Retrying harder inside a latency-gated
#: strategy buys a stale answer at a higher price.
DEFAULT_FEED_RETRIES = 2
FEED_BACKOFF_SEC = 0.25

#: Trades pulled per wallet per poll.
DEFAULT_TRADE_LIMIT = 25

#: Trades pulled when building a record. Larger because MIN_TRADE_COUNT is 50
#: and a record built from fewer rows than the gate needs cannot pass it.
DEFAULT_RECORD_LIMIT = 500

# ---------------------------------------------------------------------------
# Strategy constants. OURS, not the article's. Convention 17 applies to all.
# ---------------------------------------------------------------------------

#: Measured win rate a wallet must EXCEED. Strictly greater.
MIN_WIN_RATE = 0.60

#: Settled trades a wallet's record must EXCEED. Strictly greater. Convention 7
#: cuts both ways: a 70% win rate on 12 trades is a shrug, not a green light.
MIN_TRADE_COUNT = 50

#: A copied trade older than this is refused. Following a whale 10 minutes late
#: is following the price, not the whale, and the price has already moved to
#: where the whale put it.
MAX_TRADE_AGE_SEC = 120.0

#: Our own size cap, in shares. Explicitly NOT the whale's size: they are
#: sizing against their bankroll and their conviction, neither of which we can
#: see, and copying a size is how a copy-trader inherits somebody else's risk
#: limits.
MAX_SHARES = 20

#: Per-trade notional. Matches PolymarketPaperAdapter.notional_cap_usdc and
#: PolymarketRiskGate.DEFAULT_NOTIONAL_CAP_USDC; restated so a size computed
#: here cannot silently exceed a cap enforced somewhere else.
MAX_NOTIONAL_USDC = 10.0

#: Exchange minimum order size, in shares.
MIN_SHARES = 5

#: Highest premium we will pay. Above this the trade is Dan1ro0 concept 4E
#: (near-resolution capture), which needs its own position limits and a
#: data-quality kill switch, neither of which is built. Also the break-even
#: arithmetic: at 0.96 the copied wallet has to be right 96% of the time and
#: the kill condition's 50% line stops meaning anything.
MAX_ENTRY_PRICE = 0.95

#: Shares that must rest within DEPTH_BAND of the best ask. A whale's fill
#: against a 6-share top level tells us nothing about what WE can get.
MIN_BOOK_DEPTH_SHARES = 50
DEPTH_BAND = 0.03

#: Copied trade ids remembered. Bounded so a long session does not grow without
#: limit; large enough that a trade cannot age out of the set while it is still
#: inside MAX_TRADE_AGE_SEC at any plausible whale trade rate.
COPIED_IDS_KEPT = 2000

#: Boolean settlement keys `record_from_rows` will accept, in order.
SETTLEMENT_BOOL_KEYS = ('won', 'is_win', 'is_winner')

#: Numeric settlement keys. A row is a win when the value is strictly positive.
SETTLEMENT_NUMERIC_KEYS = ('realized_pnl', 'realizedPnl', 'pnl', 'profit')


def _reject_non_finite(token: str) -> float:
    """`json.loads` accepts bare Infinity and NaN. This refuses them.

    Convention 19. A record computed over a NaN pnl is a record that silently
    counts a corrupt row as a loss, and the value round-trips out of Python as
    a token no other JSON parser accepts.
    """
    raise ValueError('feed payload contained the non-finite JSON constant '
                     '{!r}; this is not portable JSON (convention 19)'.format(
                         token))


def is_full_address(value: Optional[str]) -> bool:
    """True only for '0x' plus 40 hex characters. A prefix is not an address."""
    if not isinstance(value, str):
        return False
    v = value.strip()
    if len(v) != ADDRESS_LENGTH or not v.lower().startswith('0x'):
        return False
    try:
        int(v[2:], 16)
    except ValueError:
        return False
    return True


def resolve_tracked_wallets(wallets: Optional[Dict[str, Optional[str]]] = None
                            ) -> Tuple[Dict[str, str], Dict[str, str],
                                       Dict[str, int]]:
    """Split the wallet list into resolved, unresolved, and a counted census.

    Returns `(resolved, statuses, counts)`:

      resolved  handle -> address, for handles carrying a FULL address
      statuses  handle -> one of RESOLUTION_STATUSES, for EVERY handle
      counts    status -> how many handles landed there, every status present
                even at zero

    Nothing is dropped. The accounting identity `sum(counts.values()) ==
    len(wallets)` holds by construction and is asserted here rather than being
    left for a reader to trust (convention 20).
    """
    wallets = TRACKED_WALLETS if wallets is None else wallets
    resolved: Dict[str, str] = {}
    statuses: Dict[str, str] = {}
    counts: Dict[str, int] = {s: 0 for s in RESOLUTION_STATUSES}

    for handle, address in wallets.items():
        if is_full_address(address):
            resolved[handle] = str(address).strip()
            status = RESOLVED
        elif handle in TRACKED_WALLET_PREFIXES or (
                isinstance(address, str) and address.strip()):
            # We have SOME hex, just not enough of it. A different problem from
            # "we have a display name and nothing else" and it needs a
            # different fix, so it gets its own bucket.
            status = UNRESOLVED_PREFIX_ONLY
        else:
            status = UNRESOLVED_NO_ADDRESS
        statuses[handle] = status
        counts[status] += 1

    total = sum(counts.values())
    if total != len(wallets):
        raise AssertionError(
            'wallet resolution census does not balance: {} counted vs {} '
            'tracked'.format(total, len(wallets)))
    return resolved, statuses, counts


# ---------------------------------------------------------------------------
# Feed types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WalletTrade:
    """One fill by a tracked wallet, as far as the public feed can tell us.

    `side` is the wallet's side, normalised to 'BUY' or 'SELL'. A row whose
    side we could not read is NOT defaulted to BUY - it is dropped and counted,
    because mirroring an unreadable side is a coin flip wearing a signal's name.
    """

    trade_id: str
    handle: str
    address: str
    side: str
    outcome_side: str
    token_id: Optional[str] = None
    market_slug: Optional[str] = None
    condition_id: Optional[str] = None
    price: Optional[float] = None
    size: Optional[float] = None
    ts: Optional[float] = None

    @property
    def is_buy(self) -> bool:
        return self.side == 'BUY'

    def age_sec(self, now: float) -> Optional[float]:
        """Seconds since the fill, or None when the row carried no timestamp.

        None means CANNOT MEASURE. A trade with no timestamp is not a fresh
        trade, and the staleness gate treats it as unusable rather than as
        zero seconds old.
        """
        if self.ts is None:
            return None
        return float(now) - float(self.ts)

    def to_dict(self) -> dict:
        return {
            'trade_id': self.trade_id, 'handle': self.handle,
            'address': self.address, 'side': self.side,
            'outcome_side': self.outcome_side, 'token_id': self.token_id,
            'market_slug': self.market_slug, 'price': self.price,
            'size': self.size, 'ts': self.ts,
        }


@dataclass(frozen=True)
class WalletRecord:
    """A wallet's MEASURED settled record. Never a claimed one.

    `measured` is True only when this was computed from rows that carried an
    explicit settlement field. There is no constructor path that produces a
    measured record from an assumption, which is the point of the class.
    """

    address: str
    trades: int
    wins: int
    source: str
    measured: bool = True

    @property
    def win_rate(self) -> Optional[float]:
        if self.trades <= 0:
            return None
        return self.wins / float(self.trades)

    def passes(self, min_win_rate: float = MIN_WIN_RATE,
               min_trades: int = MIN_TRADE_COUNT) -> bool:
        wr = self.win_rate
        if not self.measured or wr is None:
            return False
        return wr > min_win_rate and self.trades > min_trades

    def to_dict(self) -> dict:
        return {'address': self.address, 'trades': self.trades,
                'wins': self.wins, 'win_rate': self.win_rate,
                'measured': self.measured, 'source': self.source}


def _safe_float(value) -> Optional[float]:
    """`float()` that refuses non-finite values and unparseable ones."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _normalise_side(raw) -> Optional[str]:
    """'buy'/'BUY'/'Buy' -> 'BUY'. Anything unrecognised -> None."""
    if not isinstance(raw, str):
        return None
    v = raw.strip().upper()
    if v in ('BUY', 'B', 'BID'):
        return 'BUY'
    if v in ('SELL', 'S', 'ASK'):
        return 'SELL'
    return None


def _normalise_ts(raw) -> Optional[float]:
    """Epoch seconds, or None.

    Milliseconds are converted ONLY when the field name says so (handled by the
    caller). Magnitude sniffing is deliberately absent: convention 14's
    `min_bars_for()` lesson is that unit detection by magnitude reads a
    synthetic timestamp near epoch 0 as the wrong unit and derives a value
    1000x off, silently.
    """
    return _safe_float(raw)


def trade_from_row(row: dict, handle: str, address: str
                   ) -> Tuple[Optional[WalletTrade], Optional[str]]:
    """Parse one Data API row. Returns `(trade, drop_reason)`.

    Exactly one of the two is not None. Every drop is named so a feed returning
    a changed schema shows up as a categorised count rather than as an empty
    result that reads identically to a quiet wallet (convention 20).
    """
    if not isinstance(row, dict):
        return None, 'row_not_a_dict'

    side = _normalise_side(row.get('side'))
    if side is None:
        return None, 'unreadable_side'

    outcome = row.get('outcome')
    if not isinstance(outcome, str) or not outcome.strip():
        return None, 'unreadable_outcome'

    ts = _normalise_ts(row.get('timestamp'))
    if ts is None:
        ts_ms = _normalise_ts(row.get('timestamp_ms'))
        ts = None if ts_ms is None else ts_ms / 1000.0

    trade_id = row.get('transactionHash') or row.get('id') or row.get('trade_id')
    if not trade_id:
        # No stable id means no dedupe, and no dedupe means one whale BUY
        # becomes twenty copies of itself. Refuse rather than synthesise one.
        return None, 'no_trade_id'

    return WalletTrade(
        trade_id=str(trade_id),
        handle=handle,
        address=address,
        side=side,
        outcome_side=outcome.strip(),
        token_id=(str(row['asset']) if row.get('asset') else
                  (str(row['token_id']) if row.get('token_id') else None)),
        market_slug=(str(row['slug']) if row.get('slug') else None),
        condition_id=(str(row['conditionId']) if row.get('conditionId')
                      else None),
        price=_safe_float(row.get('price')),
        size=_safe_float(row.get('size')),
        ts=ts,
    ), None


def record_from_rows(rows, address: str) -> Optional[WalletRecord]:
    """Build a MEASURED record, or return None.

    None means "this wallet's record could not be measured", never "this wallet
    has a bad record" (convention 11). A row counts only when it carries an
    explicit settlement field; rows without one are not assumed to be losses,
    they are not counted at all, and if NO row carries one the answer is None.

    Polymarket's public `/trades` carries none of these keys today, so against
    the live API this returns None and the strategy skips
    `wallet_record_unmeasured`. That is the honest current state, not a bug.
    """
    if not rows:
        return None
    settled = 0
    wins = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        decided = None
        for key in SETTLEMENT_BOOL_KEYS:
            if key in row and isinstance(row[key], bool):
                decided = bool(row[key])
                break
        if decided is None:
            for key in SETTLEMENT_NUMERIC_KEYS:
                if key in row:
                    val = _safe_float(row[key])
                    if val is not None:
                        decided = val > 0
                    break
        if decided is None:
            continue
        settled += 1
        if decided:
            wins += 1
    if settled == 0:
        return None
    return WalletRecord(address=address, trades=settled, wins=wins,
                        source='settled_trade_rows', measured=True)


class WalletTradeFeed:
    """Read-only reader for `data-api.polymarket.com/trades?user=<address>`.

    Injectable exactly the way `shadow_loop` injects `candle_source` and
    `strike_proxy`, so a unit test hands in a stub and never touches the
    network. The default path prefers the project's own `PolymarketClient`
    (rate limited, GET only, non-finite JSON rejected) and falls back to
    `urllib.request` when no client is supplied, so this module stays importable
    without a network stack.

    NO WALLET, NO SIGNER, NO POST. `_fetch` builds a query string and reads a
    response. There is no code path here that can place an order, which is the
    same structural refusal `engine/polymarket/client.py` makes.

    Failures return None, never `[]`. An unreachable feed and a wallet with no
    recent trades are different facts that demand different responses, and one
    empty list cannot tell them apart (convention 11).
    """

    def __init__(self, client=None, timeout: float = DEFAULT_FEED_TIMEOUT_SEC,
                 retries: int = DEFAULT_FEED_RETRIES,
                 host: str = DATA_API_HOST,
                 trade_limit: int = DEFAULT_TRADE_LIMIT,
                 record_limit: int = DEFAULT_RECORD_LIMIT):
        self.client = client
        self.timeout = float(timeout)
        # retries=0 would mean "never send the request", which is never what a
        # caller means by it.
        self.retries = max(1, int(retries))
        self.host = host.rstrip('/')
        self.trade_limit = int(trade_limit)
        self.record_limit = int(record_limit)
        self.stats: Dict[str, int] = {
            'requests': 0, 'retries': 0, 'failures': 0,
            'fail_network': 0, 'fail_bad_json': 0, 'fail_non_finite_json': 0,
            'fail_not_a_list': 0,
        }

    # -- transport ----------------------------------------------------------

    def _fetch(self, address: str, limit: int) -> Optional[list]:
        params = {'user': address, 'limit': int(limit)}

        if self.client is not None and hasattr(self.client, 'data'):
            # The project client already rate limits, retries and rejects
            # non-finite JSON. Reimplementing any of that here would be a
            # second policy nobody updates.
            self.stats['requests'] += 1
            payload = self.client.data(TRADES_PATH, params)
            if payload is None:
                self.stats['failures'] += 1
                self.stats['fail_network'] += 1
                return None
            if not isinstance(payload, list):
                self.stats['failures'] += 1
                self.stats['fail_not_a_list'] += 1
                return None
            return payload

        session = getattr(self.client, 'session', None)
        url = self.host + TRADES_PATH

        last_err = None
        for attempt in range(self.retries):
            is_last = attempt == self.retries - 1
            self.stats['requests'] += 1
            try:
                body = (self._get_via_session(session, url, params)
                        if session is not None
                        else self._get_via_urllib(url, params))
            except Exception as exc:                    # noqa: BLE001
                last_err = '{}: {}'.format(type(exc).__name__, exc)
                if is_last:
                    self.stats['failures'] += 1
                    self.stats['fail_network'] += 1
                    break
                self.stats['retries'] += 1
                # Only sleep when another attempt actually follows. Sleeping
                # after the last one burns latency on a strategy whose whole
                # gate is a 120-second freshness window.
                time.sleep(FEED_BACKOFF_SEC * (2 ** attempt))
                continue

            try:
                payload = json.loads(body, parse_constant=_reject_non_finite)
            except ValueError as exc:
                self.stats['failures'] += 1
                key = ('fail_non_finite_json'
                       if 'non-finite' in str(exc) else 'fail_bad_json')
                self.stats[key] += 1
                logger.error('smart_money_copy feed %s: %s', url, exc)
                return None

            if not isinstance(payload, list):
                self.stats['failures'] += 1
                self.stats['fail_not_a_list'] += 1
                return None
            return payload

        logger.warning('smart_money_copy feed %s failed after %d attempts: %s',
                       url, self.retries, last_err)
        return None

    def _get_via_session(self, session, url: str, params: dict) -> str:
        resp = session.get(url, params=params, timeout=self.timeout)
        if resp.status_code != 200:
            raise IOError('HTTP {}'.format(resp.status_code))
        return resp.text

    def _get_via_urllib(self, url: str, params: dict) -> str:
        import urllib.parse
        import urllib.request
        full = url + '?' + urllib.parse.urlencode(params)
        req = urllib.request.Request(
            full, headers={'User-Agent': '05-trading-bot/paper (read-only)'})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            if getattr(resp, 'status', 200) != 200:
                raise IOError('HTTP {}'.format(resp.status))
            return resp.read().decode('utf-8')

    # -- public API ---------------------------------------------------------

    def fetch_trades(self, handle: str, address: str,
                     limit: Optional[int] = None
                     ) -> Tuple[Optional[List[WalletTrade]], Dict[str, int]]:
        """Recent trades for one wallet. Returns `(trades_or_None, drops)`.

        `None` for the trades means the read FAILED. An empty list means the
        read succeeded and the wallet has no recent trades. `drops` is keyed by
        the reason `trade_from_row` refused a row and is never merged into the
        failure count.
        """
        rows = self._fetch(address, self.trade_limit if limit is None else limit)
        drops: Dict[str, int] = {}
        if rows is None:
            return None, drops
        out: List[WalletTrade] = []
        for row in rows:
            trade, drop = trade_from_row(row, handle, address)
            if trade is None:
                drops[drop or 'unknown'] = drops.get(drop or 'unknown', 0) + 1
                continue
            out.append(trade)
        return out, drops

    def fetch_record(self, address: str) -> Optional[WalletRecord]:
        """MEASURED record for one wallet, or None if it cannot be measured.

        Against the live Polymarket Data API this returns None: `/trades`
        carries fills, not outcomes. See `record_from_rows`.
        """
        rows = self._fetch(address, self.record_limit)
        if rows is None:
            return None
        return record_from_rows(rows, address)


class SmartMoneyCopy(PolymarketStrategy):
    """Mirror a fresh BUY from a wallet whose record we have MEASURED.

    Kill condition: trailing-30 copied-trade win rate below 50% once 30 copied
    trades exist, scored by `backtest/polymarket_harness.py` on the
    `PM_smart_money_copy` population alone. See the module docstring.
    """

    strategy_name = 'PM_smart_money_copy'
    paper_mode = PAPER_MODE

    #: Holds to resolution, like every strategy here except the fair-value
    #: family. The shadow loop reads this to decide whether to poll an exit.
    manages_exits = False

    def __init__(self, trade_feed=None,
                 wallets: Optional[Dict[str, Optional[str]]] = None,
                 min_win_rate: float = MIN_WIN_RATE,
                 min_trade_count: int = MIN_TRADE_COUNT,
                 max_trade_age_sec: float = MAX_TRADE_AGE_SEC,
                 max_shares: int = MAX_SHARES,
                 max_notional_usdc: float = MAX_NOTIONAL_USDC,
                 min_shares: int = MIN_SHARES,
                 max_entry_price: float = MAX_ENTRY_PRICE,
                 min_book_depth_shares: float = MIN_BOOK_DEPTH_SHARES,
                 depth_band: float = DEPTH_BAND,
                 client=None):
        #: Injected the way shadow_loop injects `candle_source`. A default is
        #: built only when nothing was supplied, so a test that passes a stub
        #: can never fall through to the network.
        self.trade_feed = (trade_feed if trade_feed is not None
                           else WalletTradeFeed(client=client))
        self.wallets = dict(TRACKED_WALLETS if wallets is None else wallets)
        self.min_win_rate = min_win_rate
        self.min_trade_count = min_trade_count
        self.max_trade_age_sec = max_trade_age_sec
        self.max_shares = max_shares
        self.max_notional_usdc = max_notional_usdc
        self.min_shares = min_shares
        self.max_entry_price = max_entry_price
        self.min_book_depth_shares = min_book_depth_shares
        self.depth_band = depth_band

        #: address -> WalletRecord or None. Cached per process: a settled
        #: record does not change inside a 5-minute window and re-reading 500
        #: rows per wallet per poll would spend the whole latency budget on it.
        #: None is cached too, so an unmeasurable wallet is not retried every
        #: cycle.
        self._records: Dict[str, Optional[WalletRecord]] = {}
        #: trade ids already copied. See the dedupe section of the docstring.
        self._copied_ids: List[str] = []
        self._copied_set = set()

    # -- wallet resolution --------------------------------------------------

    def resolve(self) -> Tuple[Dict[str, str], Dict[str, str], Dict[str, int]]:
        return resolve_tracked_wallets(self.wallets)

    # -- dedupe -------------------------------------------------------------

    def already_copied(self, trade_id: str) -> bool:
        return trade_id in self._copied_set

    def _note_copied(self, trade_id: str) -> None:
        if trade_id in self._copied_set:
            return
        self._copied_ids.append(trade_id)
        self._copied_set.add(trade_id)
        while len(self._copied_ids) > COPIED_IDS_KEPT:
            self._copied_set.discard(self._copied_ids.pop(0))

    # -- record lookup ------------------------------------------------------

    def record_for(self, address: str) -> Optional[WalletRecord]:
        """Cached MEASURED record, or None when it could not be measured."""
        if address in self._records:
            return self._records[address]
        record = None
        fetch = getattr(self.trade_feed, 'fetch_record', None)
        if callable(fetch):
            try:
                record = fetch(address)
            except Exception as exc:                    # noqa: BLE001
                # A feed that raises is a feed that failed. Never a wallet with
                # a bad record.
                logger.warning('record fetch raised for %s: %s', address, exc)
                record = None
        self._records[address] = record
        return record

    # -- market matching ----------------------------------------------------

    @staticmethod
    def market_tokens(market) -> set:
        outcomes = getattr(market, 'outcomes', ()) or ()
        return {str(o.token_id) for o in outcomes if getattr(o, 'token_id', None)}

    def matches_market(self, trade: WalletTrade, market) -> bool:
        """Is this fill in the market we are looking at?

        Matched on token id OR slug OR condition id. Token id is the strongest
        of the three and slug is the weakest, but the Data API is not
        consistent about which fields it populates, so all three are tried and
        `match_field` records which one answered. A row that matches on nothing
        is not our market.
        """
        return self.match_field(trade, market) is not None

    def match_field(self, trade: WalletTrade, market) -> Optional[str]:
        if market is None:
            return None
        if trade.token_id and trade.token_id in self.market_tokens(market):
            return 'token_id'
        slug = getattr(market, 'slug', None)
        if trade.market_slug and slug and trade.market_slug == slug:
            return 'market_slug'
        cond = getattr(market, 'condition_id', None)
        if trade.condition_id and cond and trade.condition_id == cond:
            return 'condition_id'
        return None

    @staticmethod
    def clock(ctx: MarketContext) -> Optional[float]:
        """Absolute seconds for this observation, or None.

        Derived from the window's own timestamp rather than the wall clock, so
        a decision is reproducible from a logged context and a test does not
        have to mock `time`. Without it the staleness gate cannot run, which is
        a refusal, not a pass.
        """
        if ctx.seconds_into_window is None:
            return None
        return float(ctx.window_ts) + float(ctx.seconds_into_window)

    # -- entry --------------------------------------------------------------

    def evaluate(self, ctx: MarketContext) -> Decision:
        slug = getattr(ctx.market, 'slug', None)
        resolved, statuses, counts = self.resolve()

        def decide(action, reason, legs=None, **feats):
            feats.setdefault('paper_mode', self.paper_mode)
            # Stamped on EVERY row, skips included. Nothing downstream may pick
            # a vendor number up off a log and read it as a measurement, and
            # nothing may read a handle as if it were an address.
            feats.setdefault('claimed_win_rates_are_unverified_vendor_numbers',
                             True)
            feats.setdefault('wallet_handles_are_not_addresses', True)
            feats.setdefault('trade_count_is_attempts_not_fills', True)
            feats.setdefault('exits_before_resolution', False)
            feats.setdefault('tracked_wallets', len(self.wallets))
            feats.setdefault('wallets_resolved', counts[RESOLVED])
            feats.setdefault('wallets_unresolved_prefix_only',
                             counts[UNRESOLVED_PREFIX_ONLY])
            feats.setdefault('wallets_unresolved_no_address',
                             counts[UNRESOLVED_NO_ADDRESS])
            feats.setdefault('wallet_resolution_census_balances',
                             sum(counts.values()) == len(self.wallets))
            feats.setdefault('wallet_statuses', dict(statuses))
            return Decision(action=action, reason=reason,
                            strategy=self.strategy_name,
                            window_ts=ctx.window_ts, market_slug=slug,
                            legs=legs or [], features=feats)

        if ctx.market is None:
            return decide('SKIP', 'no_market')

        if not resolved:
            # The shipped state. Every handle counted, every one categorised,
            # nothing silently dropped.
            return decide('SKIP', 'wallet_address_unresolved',
                          unresolved_handles=sorted(
                              h for h, s in statuses.items() if s != RESOLVED))

        now = self.clock(ctx)
        if now is None:
            # The staleness gate is the whole difference between copying a
            # whale and copying the price they already moved. Without a clock
            # it cannot run and this strategy must not guess.
            return decide('SKIP', 'no_trade_clock')

        # -- pull, counting every wallet's outcome separately ---------------
        trades: List[WalletTrade] = []
        feed_failures: List[str] = []
        row_drops: Dict[str, int] = {}
        wallets_read = 0
        for handle, address in sorted(resolved.items()):
            try:
                fetched, drops = self.trade_feed.fetch_trades(handle, address)
            except Exception as exc:                    # noqa: BLE001
                logger.warning('trade feed raised for %s: %s', handle, exc)
                fetched, drops = None, {}
            for key, n in (drops or {}).items():
                row_drops[key] = row_drops.get(key, 0) + n
            if fetched is None:
                feed_failures.append(handle)
                continue
            wallets_read += 1
            trades.extend(fetched)

        feats = {
            'wallets_queried': len(resolved),
            'wallets_read': wallets_read,
            'wallets_feed_failed': len(feed_failures),
            'feed_failed_handles': sorted(feed_failures),
            'row_drops': dict(row_drops),
            'trades_seen': len(trades),
        }

        if wallets_read == 0:
            # Could not run. Never "we looked and the whales were quiet."
            return decide('SKIP', 'wallet_feed_unavailable', **feats)

        if not trades:
            return decide('SKIP', 'no_tracked_wallet_trades', **feats)

        in_market = [t for t in trades if self.matches_market(t, ctx.market)]
        feats['trades_in_this_market'] = len(in_market)
        if not in_market:
            return decide('SKIP', 'no_trade_in_this_market', **feats)

        buys = [t for t in in_market if t.is_buy]
        feats['buys_in_this_market'] = len(buys)
        if not buys:
            # A SELL is not a mirrorable BUY and inverting it into one would be
            # a different strategy with a different thesis.
            return decide('SKIP', 'no_tracked_wallet_buy', **feats)

        # Newest first. A trade with no timestamp sorts last and is refused by
        # the staleness gate rather than treated as brand new.
        buys.sort(key=lambda t: (t.ts is not None, t.ts or 0.0), reverse=True)

        fresh = [t for t in buys if not self.already_copied(t.trade_id)]
        feats['buys_already_copied'] = len(buys) - len(fresh)
        if not fresh:
            return decide('SKIP', 'already_copied_this_trade', **feats)

        ages = [t.age_sec(now) for t in fresh]
        feats['youngest_trade_age_sec'] = next(
            (round(a, 1) for a in ages if a is not None), None)
        feats['buys_without_timestamp'] = sum(1 for a in ages if a is None)

        timely = [t for t, a in zip(fresh, ages)
                  if a is not None and a <= self.max_trade_age_sec]
        feats['max_trade_age_sec'] = self.max_trade_age_sec
        feats['buys_fresh_enough'] = len(timely)
        if not timely:
            return decide('SKIP', 'copied_trade_stale', **feats)

        # -- the record gate ------------------------------------------------
        candidate = None
        record = None
        unmeasured = 0
        below_threshold = 0
        for trade in timely:
            rec = self.record_for(trade.address)
            if rec is None or not rec.measured:
                unmeasured += 1
                continue
            if not rec.passes(self.min_win_rate, self.min_trade_count):
                below_threshold += 1
                continue
            candidate, record = trade, rec
            break

        feats['wallets_record_unmeasured'] = unmeasured
        feats['wallets_record_below_threshold'] = below_threshold
        feats['min_win_rate'] = self.min_win_rate
        feats['min_trade_count'] = self.min_trade_count

        if candidate is None:
            if below_threshold and not unmeasured:
                # A measured record that fails is a RESULT. It must never share
                # a bucket with a record we could not measure at all.
                return decide('SKIP', 'wallet_record_below_threshold', **feats)
            if unmeasured and not below_threshold:
                return decide('SKIP', 'wallet_record_unmeasured', **feats)
            # Both causes present. Named as its own third reason rather than
            # picked arbitrarily, so the two never get pooled by a tiebreak.
            return decide('SKIP', 'wallet_record_mixed_unmeasured_and_below',
                          **feats)

        side = candidate.outcome_side
        feats.update({
            'copied_handle': candidate.handle,
            'copied_address': candidate.address,
            'copied_trade_id': candidate.trade_id,
            'copied_trade_age_sec': round(candidate.age_sec(now) or 0.0, 1),
            'copied_trade_price': candidate.price,
            'copied_trade_size': candidate.size,
            'copied_size_is_theirs_not_ours': True,
            'match_field': self.match_field(candidate, ctx.market),
            'outcome_side': side,
            'wallet_win_rate_measured': record.win_rate,
            'wallet_trades_measured': record.trades,
            'wallet_record_source': record.source,
            # Confidence IS the wallet's measured win rate. A measurement of
            # THEIR record, not a probability that OUR copy wins: we enter
            # later, at a worse price, with none of their other positions.
            'confidence': round(record.win_rate or 0.0, 6),
            'confidence_is_their_measured_win_rate_not_ours': True,
        })

        book = ctx.book(side)
        if book is None:
            return decide('SKIP', 'no_orderbook', **feats)
        best_ask = book.best_ask
        feats['best_ask'] = best_ask
        if best_ask is None:
            return decide('SKIP', 'no_asks', **feats)

        feats['max_entry_price'] = self.max_entry_price
        if best_ask > self.max_entry_price:
            return decide('SKIP', 'ask_above_max_entry_price', **feats)

        cap = self.max_entry_price
        depth_limit = round(best_ask + self.depth_band, 6)
        depth = book.ask_depth(depth_limit)
        feats['depth_band'] = self.depth_band
        feats['ask_depth_within_band'] = depth
        feats['min_book_depth_shares'] = self.min_book_depth_shares
        if depth < self.min_book_depth_shares:
            return decide('SKIP', 'insufficient_book_depth', **feats)

        # OUR sizing. Size DOWN to the notional cap rather than letting the
        # adapter reject the order: "20 shares does not fit in $10 at 60c" and
        # "the risk gate blocked this" are different facts.
        affordable = int(math.floor(self.max_notional_usdc / best_ask + 1e-9))
        shares = min(self.max_shares, affordable)
        feats['max_shares'] = self.max_shares
        feats['affordable_shares_at_ask'] = affordable
        feats['shares'] = shares
        feats['shares_capped_by_notional'] = shares < self.max_shares
        if shares < self.min_shares:
            # Could not run, did not lose (convention 11).
            return decide('SKIP', 'unsizable_at_notional_cap', **feats)

        effective = effective_ask_for(book, shares, cap)
        feats['effective_ask'] = (None if effective is None
                                  else round(effective, 4))
        if effective is None:
            return decide('SKIP', 'unfillable_at_cap', **feats)
        if effective > cap:
            # walk_book cannot return this under the same limit, but a silent
            # regression on the price cap would be invisible.
            return decide('SKIP', 'effective_ask_above_cap', **feats)

        # On a binary the break-even win rate IS the premium paid. Reported so
        # nobody has to recompute it from the copied wallet's number, which
        # describes a different trade at a different price.
        feats['breakeven_win_rate'] = round(effective, 4)
        feats['notional_usdc'] = round(shares * effective, 4)
        feats['limit_price'] = cap

        self._note_copied(candidate.trade_id)

        return decide('ENTER', '',
                      legs=[Leg(outcome_side=side,
                                limit_price=cap,
                                order_type='taker',
                                shares=shares,
                                expected_price=effective)],
                      **feats)
