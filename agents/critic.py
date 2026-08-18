"""Critic: reads closed trades, names WHY each loser lost, and refuses to guess.

Runs after a shadow session (or on a timer). It reads `positions` and
`signals`, classifies every LOSING closed trade into a failure mode, writes
those modes back into the `hypothesis_graph` table, asks Opus for a post-mortem
through `agents/vault_writer.py`, and drops kill recommendations into
`docs/handoffs/from-raven/`.

Nothing here writes to `positions`, `fills`, `orders`, `signals` or
`equity_snapshots`. Those are opened `mode=ro` at the SQLite layer so a stray
INSERT raises instead of corrupting a live tape; the Polymarket shadow loop is
usually mid-write on the same file.

## The whole point is the classifier admitting when it cannot tell

A classifier that always returns a plausible label is worse than one that
returns `unclassified`, because the plausible label gets counted, quoted,
carried into a kill recommendation, and eventually into a decision. So each
mode below is a real test against columns that actually exist, and the two
modes that need data this database does not hold are wired to return NOTHING
rather than a guess.

What is decidable, and from what:

  spread_eats_edge      DECIDABLE, per trade, where the entry signal recorded
                        `best_bid` and `best_ask`. Entry crosses the spread and
                        pays the ask; the paper exit sells the bid. So when the
                        trade closed at or above the MID that stood at entry,
                        the mid did not move against us and the loss is the
                        spread crossing by itself. Trades whose entry signal has
                        no bid/ask are NOT eligible and are counted as such.

  stop_too_tight        DECIDABLE ONLY where a post-exit price exists. There is
                        no quote tape table in this database and `candles` holds
                        crypto spot bars that do not overlap the Polymarket
                        trading window at all, so "did it reverse after the
                        stop" cannot be answered from a price series. What DOES
                        exist: later signals on the SAME market slug and the
                        SAME outcome side carry `best_bid` in their
                        `features_json`. Those are genuine post-exit
                        observations of the very token we stopped out of. When
                        one shows a bid back at or above our ENTRY price, the
                        market handed the loss back and the stop was premature.
                        When no such observation exists the trade is
                        `unclassified` with reason
                        `no_post_exit_price_observation`. It is never guessed
                        from the exit reason alone, which would classify all 509
                        losers as stop_too_tight, because every loser in this
                        database exited on a stop.

  model_miscalibrated   DECIDABLE at STRATEGY level only, and only for
                        strategies that publish an explicit model price
                        (`side_fair_value`, `fair_value_up`, `p_corridor`,
                        `binned_fair_pair_value`). The test is the task's own
                        definition: the model said buy, did the market go the
                        other way. Scored as the mean realised price change over
                        ALL closed trades of the strategy, winners included, so
                        it is not the tautology "the losers lost".
                        NOT scored against the model's resolution probability:
                        every closed position in this database exited before
                        resolution (`exits_before_resolution: true`, and no exit
                        reason is a redemption), so the event the model gives a
                        probability for is never observed. `confidence` is NOT
                        used as a probability anywhere here - the strategies
                        themselves stamp
                        `confidence_is_model_output_not_measured_win_rate` and
                        `confidence_is_dip_size_not_win_probability` on their
                        signals precisely to stop that reading.

  entry_signal_wrong    DECIDABLE at STRATEGY level. The direction call did no
                        better than a coin flip: win rate <= 50% over at least
                        MIN_STRATEGY_SAMPLE closed trades. 50% is the benchmark
                        for a DIRECTION claim and nothing more. Clearing 50% is
                        necessary but nowhere near sufficient for profit - the
                        breakeven win rates these strategies log run from 0.19 to
                        0.71 - so this test says the signal does not predict
                        direction, NOT that the strategy is unprofitable.

  never_fires           DECIDABLE, from `signals`, and it is not a property of a
                        trade. A strategy that produced a closed trade fired, by
                        construction. So this is computed separately over the
                        window: strategies with signals in the window and zero
                        `acted=1`. Convention 11 governs what happens next: a
                        strategy that never fired is NOT_TESTED, not failed, so
                        the critic REPORTS it and does NOT write it to the
                        hypothesis graph as TESTED_FAILED and does NOT recommend
                        killing it. (`hypothesis_graph.populate_from_graveyard`
                        does write never_fires as TESTED_FAILED. That divergence
                        is deliberate here and wants a D-number.)

  regime_mismatch       NOT DECIDABLE. There is no regime label on any trade, on
                        any signal, or anywhere in this database, and all 135
                        existing `hypothesis_graph` rows carry
                        `market_regime='any'`, so there is not even a stored
                        regime CLAIM for a trade to mismatch against. This
                        classifier is present, wired into the ladder, and
                        returns None every time with a stated reason. It stays in
                        the code rather than being dropped so that the count of
                        modes we cannot decide is visible instead of absent.

## Convention 20 is asserted, not hoped for

Every closed trade in the window is counted exactly once and lands in exactly
one bucket. `winners + losers == closed`, and the per-mode counts of the losers
sum back to `losers` with `unclassified` as a real bucket rather than a
remainder nobody printed. The reasons a trade ended up unclassified are
themselves counted and categorised, and those counts sum back to the
unclassified total. `_assert_accounting` raises if any identity breaks, which is
the point: a silent `continue` here would be a missing number in a document that
recommends killing a strategy.

## Convention 7 is stamped on every recommendation

Three occurrences of one failure mode is a very low bar. Every recommendation
therefore carries the number of classified occurrences AND the strategy's total
closed trades in the window, and anything under PROVISIONAL_TRADE_FLOOR closed
trades is marked PROVISIONAL in the output text. A kill recommendation is a
reason to look, not a retirement.

## State

`--since last` reads `research/critic_state.json`, a plain JSON file. No new
database table was invented for this; the trading database is live and adding a
table to it for a bookkeeping timestamp is not worth the write.
"""
import argparse
import json
import os
import sqlite3
import statistics
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from agents import hypothesis_graph as hg
from agents import vault_reader, vault_writer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(ROOT, 'db', 'trading.db')
STATE_PATH = os.path.join(ROOT, 'research', 'critic_state.json')
KILL_DIR = os.path.join(ROOT, 'docs', 'handoffs', 'from-raven')

#: Source tag on every hypothesis_graph row this module writes. Distinct from
#: 'shadow' so the critic's verdicts never collide with
#: `hypothesis_graph.populate_from_shadow`'s under the upsert identity.
SOURCE_CRITIC = 'critic'

#: Convention 7. Below this many closed trades a strategy-level verdict is a
#: shrug, so the strategy-level classifiers refuse to fire at all and the trades
#: land in `unclassified` with the reason saying why.
MIN_STRATEGY_SAMPLE = 30

#: Convention 7 again, on the way out: a recommendation resting on fewer closed
#: trades than this is printed as PROVISIONAL.
PROVISIONAL_TRADE_FLOOR = 30

#: Same-mode occurrences for one strategy before a kill is recommended. Low on
#: purpose (Raven's spec) and therefore always printed next to the sample size.
KILL_THRESHOLD = 3

#: A losing direction call is one that did no better than a coin flip. This is
#: a benchmark for DIRECTION, not for profitability. See the module docstring.
COIN_FLIP_WIN_RATE = 0.50

#: How far past the review window to look for post-exit quotes of a token we
#: stopped out of. The markets here are 5m and 15m binaries, so an hour is
#: generous; past resolution the token stops being quoted and the search simply
#: finds nothing.
POST_EXIT_LOOKAHEAD_MS = 60 * 60 * 1000

#: Feature keys that mean "this strategy published an explicit model price for
#: the outcome it bought". Presence is what makes `model_miscalibrated`
#: testable at all.
MODEL_PRICE_KEYS = (
    'side_fair_value',
    'fair_value_up',
    'p_corridor',
    'binned_fair_pair_value',
)

#: Mean realised price change (exit minus entry, in outcome-share cents) that
#: counts as "the market went the other way". Not zero: a mean that is adverse
#: by a hair over 30 trades is noise.
ADVERSE_MOVE_FLOOR = 0.005

#: The bucket for "the ladder ran and could not decide". A real bucket with a
#: real count, never a remainder.
UNCLASSIFIED = 'unclassified'

#: Every reason a losing trade can end up unclassified. Convention 20: two drop
#: causes never share one counter, so each of these is its own key and they sum
#: back to the unclassified total.
UNCLASSIFIED_REASONS = (
    'entry_signal_row_missing',
    'position_direction_not_derivable',
    'entry_side_not_recorded',
    'no_post_exit_price_observation',
    'strategy_sample_below_min',
    'strategy_level_tests_did_not_fire',
)

#: Modes this module can produce. Subset of `hypothesis_graph.FAILURE_MODES`, so
#: every write validates.
CRITIC_MODES = (
    'spread_eats_edge',
    'stop_too_tight',
    'model_miscalibrated',
    'entry_signal_wrong',
    'never_fires',
    'regime_mismatch',
    UNCLASSIFIED,
)

#: Stated in code, not only in prose, so a reader who greps for a mode finds the
#: reason it never appears.
NOT_DECIDABLE: Dict[str, str] = {
    'regime_mismatch':
        'no regime label exists on positions, on signals, or anywhere in '
        'db/trading.db, and every hypothesis_graph row carries '
        "market_regime='any', so there is no regime claim to mismatch "
        'against. NOT_TESTED, not "no mismatch found".',
}


# --------------------------------------------------------------------------
# Sanity: the modes we emit must be writable by the graph
# --------------------------------------------------------------------------

for _mode in CRITIC_MODES:
    if _mode not in hg.FAILURE_MODES:
        raise AssertionError(
            'critic mode %r is not in hypothesis_graph.FAILURE_MODES; a mode '
            'the graph cannot store is a mode that will be silently dropped'
            % _mode)


# --------------------------------------------------------------------------
# Time
# --------------------------------------------------------------------------

def now_ms() -> int:
    return int(time.time() * 1000)


def parse_since(text: str, *, state: Optional[Dict[str, Any]] = None,
                now: Optional[int] = None) -> Tuple[int, str]:
    """Turn a `--since` argument into `(epoch_ms, how_it_was_read)`.

    Accepts `last`, a duration like `4h` / `90m` / `7d`, an ISO date or
    datetime, or a raw epoch value. `hypothesis_graph.to_ms` does the epoch
    seconds-versus-milliseconds disambiguation; its boundary is year 1973 in ms,
    so no real trading timestamp is ambiguous.
    """
    now = now if now is not None else now_ms()
    raw = (text or '').strip()
    if not raw:
        raise ValueError('--since is required')

    if raw.lower() == 'last':
        state = state if state is not None else load_state()
        last = state.get('last_review_until_ms')
        if last is None:
            return 0, 'last (no previous review recorded; scanning from 0)'
        return int(last), 'last review at %s' % iso(int(last))

    unit_ms = {'m': 60 * 1000, 'h': 3600 * 1000, 'd': 86400 * 1000}
    if len(raw) > 1 and raw[-1].lower() in unit_ms:
        head = raw[:-1]
        try:
            amount = float(head)
        except ValueError:
            pass
        else:
            delta = int(amount * unit_ms[raw[-1].lower()])
            return now - delta, '%s before now' % raw

    # A bare number is an epoch value. `to_ms` only takes the string path
    # through `datetime.fromisoformat`, which rejects '0' and '1787022141000',
    # so the integer conversion happens here and `to_ms` then does the
    # seconds-versus-milliseconds disambiguation on the number.
    try:
        return int(hg.to_ms(int(raw))), 'explicit epoch value %r' % raw
    except (TypeError, ValueError):
        pass
    return int(hg.to_ms(raw)), 'explicit timestamp %r' % raw


def iso(ms: Optional[int]) -> str:
    if ms is None:
        return 'None'
    return datetime.fromtimestamp(ms / 1000.0, timezone.utc).strftime(
        '%Y-%m-%dT%H:%M:%SZ')


# --------------------------------------------------------------------------
# State file
# --------------------------------------------------------------------------

def load_state(path: str = STATE_PATH) -> Dict[str, Any]:
    """Read the review bookmark. A missing or corrupt file is an empty state.

    Corrupt is treated as empty rather than fatal: a bookmark is a convenience,
    and refusing to run the critic because a JSON file got truncated would be
    the tail wagging the dog. The fact is reported in the returned dict so it is
    not silent.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            parsed = json.load(handle)
    except (OSError, ValueError) as exc:
        return {'_unreadable': str(exc)}
    return parsed if isinstance(parsed, dict) else {'_unreadable': 'not an object'}


def save_state(until_ms: int, path: str = STATE_PATH,
               extra: Optional[Dict[str, Any]] = None) -> None:
    """Move the bookmark. Written whole, then renamed, so a reader never
    observes half a file."""
    payload: Dict[str, Any] = {
        'last_review_until_ms': int(until_ms),
        'last_review_until_iso': iso(int(until_ms)),
        'written_at_iso': iso(now_ms()),
    }
    if extra:
        payload.update(extra)
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as handle:
        # Convention 19: a non-finite would raise here rather than downstream.
        json.dump(payload, handle, indent=2, sort_keys=True, allow_nan=False)
    os.replace(tmp, path)


# --------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------

def _features(raw: Optional[str]) -> Dict[str, Any]:
    """Parse a `features_json` blob. An unparseable blob is an empty dict with
    a marker, never a crash and never silently `{}`."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return {'_unparseable_features_json': True}
    return parsed if isinstance(parsed, dict) else {'_features_not_an_object': True}


def load_window(conn: sqlite3.Connection, since_ts: int, until_ts: int
                ) -> Dict[str, Any]:
    """Everything the classifiers need for `[since_ts, until_ts)`.

    A position belongs to the window by `closed_ts`, not `opened_ts`: the critic
    reviews outcomes, and a trade that opened before the window but closed
    inside it is an outcome this window produced.
    """
    positions = [dict(row) for row in conn.execute(
        'SELECT * FROM positions WHERE closed_ts IS NOT NULL '
        'AND closed_ts >= ? AND closed_ts < ? ORDER BY closed_ts',
        (int(since_ts), int(until_ts)))]

    # Entry signals are fetched by id rather than by window: a position that
    # closed inside the window may have opened (and signalled) before it.
    signal_ids = sorted({p['signal_id'] for p in positions if p['signal_id']})
    entry_signals: Dict[str, Dict[str, Any]] = {}
    for chunk in _chunks(signal_ids, 400):
        marks = ','.join('?' * len(chunk))
        for row in conn.execute(
                'SELECT id, ts, pair, strategy_id, direction, confidence, '
                'features_json FROM signals WHERE id IN (%s)' % marks, chunk):
            record = dict(row)
            record['features'] = _features(record.pop('features_json'))
            entry_signals[record['id']] = record

    # Post-exit quotes: any signal in the window plus a lookahead tail, indexed
    # by (market slug, outcome side). See POST_EXIT_LOOKAHEAD_MS.
    post_exit: Dict[Tuple[str, str], List[Tuple[int, float]]] = {}
    for row in conn.execute(
            'SELECT pair, ts, features_json FROM signals '
            'WHERE ts >= ? AND ts < ?',
            (int(since_ts), int(until_ts) + POST_EXIT_LOOKAHEAD_MS)):
        feats = _features(row['features_json'])
        side = feats.get('outcome_side')
        bid = feats.get('best_bid')
        if side is None or not isinstance(bid, (int, float)):
            continue
        post_exit.setdefault((row['pair'], side), []).append(
            (int(row['ts']), float(bid)))
    for series in post_exit.values():
        series.sort()

    # never_fires evidence: per strategy, signals in the window and how many
    # were acted on. Convention 20 lives here too - the skip reasons are
    # counted, not just the fact of skipping.
    fired: Dict[str, Dict[str, Any]] = {}
    for row in conn.execute(
            'SELECT strategy_id, acted, skip_reason, COUNT(*) AS n '
            'FROM signals WHERE ts >= ? AND ts < ? '
            'GROUP BY strategy_id, acted, skip_reason',
            (int(since_ts), int(until_ts))):
        bucket = fired.setdefault(row['strategy_id'], {
            'signals': 0, 'acted': 0, 'skipped': 0, 'skip_reasons': {}})
        n = int(row['n'])
        bucket['signals'] += n
        if row['acted']:
            bucket['acted'] += n
        else:
            bucket['skipped'] += n
            reason = row['skip_reason'] or '(no skip_reason recorded)'
            bucket['skip_reasons'][reason] = (
                bucket['skip_reasons'].get(reason, 0) + n)

    return {
        'since_ts': int(since_ts),
        'until_ts': int(until_ts),
        'positions': positions,
        'entry_signals': entry_signals,
        'post_exit_bids': post_exit,
        'signal_activity': fired,
    }


def _chunks(items: Sequence[Any], size: int) -> Iterable[Sequence[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


# --------------------------------------------------------------------------
# Derived facts about one position
# --------------------------------------------------------------------------

def is_long(row: Dict[str, Any]) -> Optional[bool]:
    """True when profit came from the price RISING, False when falling.

    Derived from the recorded P&L rather than assumed. Every position in this
    database is a long outcome-share purchase, but asserting that from the data
    is cheap and a future short would otherwise be misread by every price test
    below. Returns None when the two disagree or either is missing, and the
    caller then declines to run any price-direction test.
    """
    entry, exit_px = row.get('entry_px'), row.get('exit_px')
    pnl = row.get('pnl_gross')
    if pnl is None:
        pnl = row.get('pnl_net')
    if entry is None or exit_px is None or pnl is None:
        return None
    move = exit_px - entry
    if move == 0 or pnl == 0:
        return None
    return (move > 0) == (pnl > 0)


def is_loser(row: Dict[str, Any]) -> bool:
    pnl = row.get('pnl_net')
    if pnl is None:
        pnl = row.get('pnl_gross')
    return pnl is not None and pnl < 0


def is_stop_exit(row: Dict[str, Any]) -> bool:
    """Exit reasons in this database are `sell:price_stop`, `stop`,
    `sell:time_stop`, `sell:model_stop`, plus the target-side ones. Substring
    match on 'stop' covers every stop-like variant seen."""
    return 'stop' in (row.get('exit_reason') or '')


# --------------------------------------------------------------------------
# Strategy-level context
# --------------------------------------------------------------------------

def build_context(window: Dict[str, Any]) -> Dict[str, Any]:
    """Aggregate the window into the per-strategy facts the ladder needs.

    Computed over ALL closed trades of the strategy, winners included. Running
    the direction tests over losers only would make them tautologies: every
    loser moved against the entry, by definition of losing.
    """
    stats: Dict[str, Dict[str, Any]] = {}
    for row in window['positions']:
        name = row.get('strategy_id') or '(no strategy_id)'
        bucket = stats.setdefault(name, {
            'strategy': name,
            'closed': 0,
            'wins': 0,
            'losses': 0,
            'flat': 0,
            'pnl_net': 0.0,
            'pnl_gross': 0.0,
            'fees': 0.0,
            'price_moves': [],
            'model_price_trades': 0,
            'model_price_keys': set(),
            'spread_testable_trades': 0,
            'pairs': set(),
        })
        bucket['closed'] += 1
        pnl = row.get('pnl_net')
        if pnl is None:
            pnl = row.get('pnl_gross') or 0.0
        bucket['pnl_net'] += pnl or 0.0
        bucket['pnl_gross'] += row.get('pnl_gross') or 0.0
        bucket['fees'] += row.get('fees') or 0.0
        if pnl and pnl > 0:
            bucket['wins'] += 1
        elif pnl and pnl < 0:
            bucket['losses'] += 1
        else:
            bucket['flat'] += 1
        if row.get('pair'):
            bucket['pairs'].add(row['pair'])

        signal = window['entry_signals'].get(row.get('signal_id'))
        feats = signal['features'] if signal else {}
        found = [k for k in MODEL_PRICE_KEYS if k in feats]
        if found:
            bucket['model_price_trades'] += 1
            bucket['model_price_keys'].update(found)
        if (isinstance(feats.get('best_ask'), (int, float))
                and isinstance(feats.get('spread'), (int, float))):
            bucket['spread_testable_trades'] += 1

        long_side = is_long(row)
        if long_side is not None and row.get('exit_px') is not None:
            move = row['exit_px'] - row['entry_px']
            # Signed so that positive always means "the market went the way the
            # position wanted", whichever way that was.
            bucket['price_moves'].append(move if long_side else -move)

    for bucket in stats.values():
        n = bucket['closed']
        bucket['win_rate'] = (bucket['wins'] / n) if n else 0.0
        bucket['mean_favourable_move'] = (
            statistics.mean(bucket['price_moves'])
            if bucket['price_moves'] else None)
        bucket['has_model_price'] = bucket['model_price_trades'] > 0
        bucket['sample_is_adequate'] = n >= MIN_STRATEGY_SAMPLE
        bucket['asset_class'] = hg._shadow_asset_class(sorted(bucket['pairs']))
        bucket['pairs'] = sorted(bucket['pairs'])
        bucket['model_price_keys'] = sorted(bucket['model_price_keys'])
        del bucket['price_moves']

    return {
        'strategy_stats': stats,
        'entry_signals': window['entry_signals'],
        'post_exit_bids': window['post_exit_bids'],
        'min_strategy_sample': MIN_STRATEGY_SAMPLE,
    }


# --------------------------------------------------------------------------
# The classifiers
# --------------------------------------------------------------------------

class Classification(object):
    """One losing trade and the mode the ladder decided, with the reasoning."""

    def __init__(self, row: Dict[str, Any], mode: str, confidence: float,
                 why: str, level: str,
                 unclassified_reason: Optional[str] = None,
                 also_matched: Optional[List[str]] = None) -> None:
        self.position_id = row.get('id')
        self.strategy = row.get('strategy_id') or '(no strategy_id)'
        self.pair = row.get('pair')
        self.opened_ts = row.get('opened_ts')
        self.closed_ts = row.get('closed_ts')
        self.entry_px = row.get('entry_px')
        self.exit_px = row.get('exit_px')
        self.qty = row.get('qty')
        self.pnl_net = row.get('pnl_net')
        self.exit_reason = row.get('exit_reason')
        self.mode = mode
        self.confidence = confidence
        self.why = why
        self.level = level
        self.unclassified_reason = unclassified_reason
        self.also_matched = list(also_matched or [])
        if mode == UNCLASSIFIED and unclassified_reason not in UNCLASSIFIED_REASONS:
            raise AssertionError(
                'unclassified trade %r carries reason %r, which is not in '
                'UNCLASSIFIED_REASONS; Convention 20 forbids an uncounted drop '
                'cause' % (self.position_id, unclassified_reason))

    def to_dict(self) -> Dict[str, Any]:
        return {
            'position_id': self.position_id,
            'strategy': self.strategy,
            'pair': self.pair,
            'closed_ts': self.closed_ts,
            'closed_iso': iso(self.closed_ts),
            'entry_px': self.entry_px,
            'exit_px': self.exit_px,
            'qty': self.qty,
            'pnl_net': self.pnl_net,
            'exit_reason': self.exit_reason,
            'failure_mode': self.mode,
            'classifier_confidence': self.confidence,
            'why': self.why,
            'decided_at_level': self.level,
            'unclassified_reason': self.unclassified_reason,
            'also_matched': self.also_matched,
        }


def check_spread_eats_edge(row: Dict[str, Any], context: Dict[str, Any]
                           ) -> Optional[str]:
    """Per trade. Did the spread crossing, on its own, account for the loss?

    Entry pays the ask, the paper exit sells the bid. So take the MID that stood
    at entry: if the position closed at or above it, the mid did not move
    against us and there is nothing left to blame but the round trip across the
    book. Returns the `why` string, or None when the test does not fire or
    cannot run.
    """
    signal = context['entry_signals'].get(row.get('signal_id'))
    if not signal:
        return None
    feats = signal['features']
    ask, spread = feats.get('best_ask'), feats.get('spread')
    bid = feats.get('best_bid')
    if not isinstance(ask, (int, float)) or not isinstance(spread, (int, float)):
        return None
    if spread <= 0 or row.get('exit_px') is None:
        return None
    long_side = is_long(row)
    if long_side is None:
        return None

    # The mid is built from the ask and the strategy's OWN declared spread, not
    # from (best_bid + best_ask) / 2. On PM_fair_value_arb_inverse the recorded
    # `best_bid` is the COMPLEMENT token's bid: a signal there shows best_ask
    # 0.67, best_bid 0.33, spread 0.01, and 0.67 + 0.33 = 1.00. Averaging those
    # two gives 0.50, which is the midpoint between two different tokens and is
    # not a price of anything. It made every inverse exit above 0.50 look like
    # the spread had eaten a real edge. `best_ask - spread / 2` is the mid of
    # the book we actually traded on every strategy that records both.
    mid = ask - (spread / 2.0) if long_side else ask + (spread / 2.0)
    if long_side:
        held = row['exit_px'] >= mid
    else:
        held = row['exit_px'] <= mid
    if not held:
        return None

    # Reported, not required: when best_bid does belong to the same book it
    # corroborates the mid, and when it does not the reader should see that.
    if isinstance(bid, (int, float)) and abs((ask - bid) - spread) < 1e-9:
        book = 'best_bid %.4f corroborates (ask minus bid equals the recorded ' \
               'spread)' % bid
    else:
        book = 'best_bid %s does not belong to this book (ask minus bid does ' \
               'not equal the recorded spread), so it was not used' % bid

    return ('exit %.4f is on the favourable side of the entry mid %.4f '
            '(best_ask %.4f, recorded spread %.4f); the mid never moved '
            'against the position, so the %.4f half-spread paid on entry is '
            'the whole loss. %s'
            % (row['exit_px'], mid, ask, spread, spread / 2.0, book))


def check_stop_too_tight(row: Dict[str, Any], context: Dict[str, Any]
                         ) -> Tuple[Optional[str], Optional[str]]:
    """Per trade. Did the market hand the loss back after the stop?

    Returns `(why, blocking_reason)`. `blocking_reason` is set when the test
    could not be run at all, which is a different fact from the test running and
    saying no.

    The observations are later signals on the same market slug and the same
    outcome side that recorded a `best_bid`. That is a real quote for the very
    token we were holding. Compared against the ENTRY price, not the exit price:
    a bid back at entry means the position could have been closed flat, so the
    realised loss was the stop's doing.
    """
    if not is_stop_exit(row):
        return None, None
    signal = context['entry_signals'].get(row.get('signal_id'))
    if not signal:
        return None, 'entry_signal_row_missing'
    side = signal['features'].get('outcome_side')
    if side is None:
        # Distinct from "no quote exists". This one says the ENTRY signal never
        # recorded which of the two outcome tokens was bought, so there is
        # nothing to match a later quote against. Different cause, different
        # fix, therefore its own counter (Convention 20).
        return None, 'entry_side_not_recorded'
    if row.get('closed_ts') is None:
        return None, 'no_post_exit_price_observation'
    long_side = is_long(row)
    if long_side is None:
        return None, 'position_direction_not_derivable'
    if not long_side:
        # A short outcome-share position would need the post-exit ASK, not the
        # bid, and nothing in this database has ever taken one. Refusing is
        # cheaper than writing an untested branch that looks tested.
        return None, 'position_direction_not_derivable'

    series = context['post_exit_bids'].get((row.get('pair'), side)) or []
    after = [(ts, bid) for ts, bid in series if ts > row['closed_ts']]
    if not after:
        return None, 'no_post_exit_price_observation'

    best_ts, best_bid = max(after, key=lambda pair: pair[1])
    if best_bid < row['entry_px']:
        return None, None
    seconds = (best_ts - row['closed_ts']) / 1000.0
    return ('stopped out at %.4f, then the book quoted a bid of %.4f on the '
            'same market and side %.0fs later, at or above the %.4f entry; '
            '%d post-exit quote(s) observed'
            % (row['exit_px'], best_bid, seconds, row['entry_px'],
               len(after))), None


def check_model_miscalibrated(row: Dict[str, Any], context: Dict[str, Any]
                              ) -> Optional[str]:
    """Strategy level. The model said buy and the market went the other way.

    Requires the strategy to publish an explicit model price on its signals, and
    at least MIN_STRATEGY_SAMPLE closed trades. Scored on the mean favourable
    price move across ALL of the strategy's closed trades.
    """
    stats = context['strategy_stats'].get(
        row.get('strategy_id') or '(no strategy_id)')
    if not stats or not stats['sample_is_adequate']:
        return None
    if not stats['has_model_price']:
        return None
    mean_move = stats['mean_favourable_move']
    if mean_move is None or mean_move > -ADVERSE_MOVE_FLOOR:
        return None
    return ('%s publishes an explicit model price on %d/%d of its entries '
            '(fields: %s), and across all %d closed trades in the window the '
            'mean move was %+.4f against the position it took (floor %.4f); '
            'the model priced the outcome and the price went the other way. '
            'Not scored against the model resolution probability: every '
            'position exits before resolution, so that event is never '
            'observed.'
            % (stats['strategy'], stats['model_price_trades'],
               stats['closed'], ', '.join(stats['model_price_keys']),
               stats['closed'], mean_move, ADVERSE_MOVE_FLOOR))


def check_entry_signal_wrong(row: Dict[str, Any], context: Dict[str, Any]
                             ) -> Optional[str]:
    """Strategy level. The direction call did no better than a coin flip."""
    stats = context['strategy_stats'].get(
        row.get('strategy_id') or '(no strategy_id)')
    if not stats or not stats['sample_is_adequate']:
        return None
    if stats['win_rate'] > COIN_FLIP_WIN_RATE:
        return None
    return ('%s won %d of %d closed trades (%.1f%%), at or below the %.0f%% '
            'coin-flip benchmark for a direction call. This says the signal '
            'does not predict direction; it is not a profitability verdict, '
            'because the breakeven win rate for these payoffs is higher than '
            '50%% anyway.'
            % (stats['strategy'], stats['wins'], stats['closed'],
               100.0 * stats['win_rate'], 100.0 * COIN_FLIP_WIN_RATE))


def check_regime_mismatch(row: Dict[str, Any], context: Dict[str, Any]
                          ) -> Optional[str]:
    """Always None. See NOT_DECIDABLE['regime_mismatch'].

    Kept in the ladder rather than deleted so that "we cannot decide this" is a
    visible, counted fact instead of a mode that quietly never appears.
    """
    return None


def classify_trade_detail(row: Dict[str, Any], context: Dict[str, Any]
                          ) -> Classification:
    """Run the fixed ladder over one LOSING closed trade.

    Order, and why it is this order:

      1. spread_eats_edge   decided from entry-time data on the trade itself,
                            needs no external observation, and is a complete
                            explanation of the loss on its own.
      2. stop_too_tight     also per trade, but depends on a post-exit quote
                            that exists for only some trades.
      3. model_miscalibrated  strategy level, needs an explicit model price.
      4. entry_signal_wrong   strategy level, the fallback direction test.
      5. regime_mismatch      never fires; see NOT_DECIDABLE.

    A test lower in the ladder that ALSO matched is recorded in `also_matched`
    rather than thrown away, so the priority never hides a second finding.
    """
    matches: List[Tuple[str, str, float, str]] = []

    spread_why = check_spread_eats_edge(row, context)
    if spread_why:
        matches.append(('spread_eats_edge', spread_why, 0.85, 'trade'))

    stop_why, stop_block = check_stop_too_tight(row, context)
    if stop_why:
        matches.append(('stop_too_tight', stop_why, 0.75, 'trade'))

    model_why = check_model_miscalibrated(row, context)
    if model_why:
        matches.append(('model_miscalibrated', model_why, 0.55, 'strategy'))

    entry_why = check_entry_signal_wrong(row, context)
    if entry_why:
        matches.append(('entry_signal_wrong', entry_why, 0.55, 'strategy'))

    regime_why = check_regime_mismatch(row, context)
    if regime_why:  # pragma: no cover - documented to be unreachable
        matches.append(('regime_mismatch', regime_why, 0.0, 'strategy'))

    if matches:
        mode, why, confidence, level = matches[0]
        return Classification(row, mode, confidence, why, level,
                              also_matched=[m[0] for m in matches[1:]])

    # Nothing fired. Name WHICH kind of nothing, because the fixes differ.
    #
    # The rule is: report the FIRST test in the ladder that could not RUN. Not
    # the most interesting blocker, not a blend - the first one, in the same
    # order the ladder itself uses. That keeps each trade in exactly one bucket
    # and makes the bucket mean something precise. The per-trade tests come
    # first here because they do not care about sample size at all: a single
    # trade is enough to decide `spread_eats_edge`, so a small strategy sample
    # is not what is blocking them.
    stats = context['strategy_stats'].get(
        row.get('strategy_id') or '(no strategy_id)')
    if row.get('signal_id') not in context['entry_signals']:
        reason = 'entry_signal_row_missing'
    elif is_long(row) is None:
        reason = 'position_direction_not_derivable'
    elif stop_block:
        reason = stop_block
    elif stats is None or not stats['sample_is_adequate']:
        reason = 'strategy_sample_below_min'
    else:
        reason = 'strategy_level_tests_did_not_fire'

    why = {
        'no_post_exit_price_observation':
            'no later quote for this market and outcome side exists after the '
            'exit, so whether the market reversed cannot be answered; '
            'NOT_TESTED, not "it did not reverse"',
        'strategy_sample_below_min':
            '%s has %d closed trades in this window, under the %d needed for a '
            'strategy-level verdict (Convention 7)'
            % (stats['strategy'] if stats else '(unknown)',
               stats['closed'] if stats else 0, MIN_STRATEGY_SAMPLE),
        'strategy_level_tests_did_not_fire':
            'every test in the ladder ran and none matched; the loss is real '
            'and its mechanism is not named by any classifier here',
        'entry_side_not_recorded':
            'the entry signal did not record `outcome_side`, so a later quote '
            'on this market cannot be matched to the token that was held; '
            'NOT_TESTED, and a different problem from having no later quote',
        'entry_signal_row_missing':
            'the position references a signal id that is not in the signals '
            'table for this window, so no entry-time book or model data is '
            'available',
        'position_direction_not_derivable':
            'the recorded price move and P&L do not agree on which way the '
            'position was pointing, so no price-direction test may run',
    }[reason]
    return Classification(row, UNCLASSIFIED, 0.0, why, 'none',
                          unclassified_reason=reason)


def classify_trade(row: Dict[str, Any], context: Dict[str, Any]
                   ) -> Tuple[str, float, str]:
    """`(mode, confidence, why)` for one losing closed trade.

    The thin form. `classify_trade_detail` returns the same decision plus the
    categorised unclassified reason and any lower-priority matches.
    """
    detail = classify_trade_detail(row, context)
    return detail.mode, detail.confidence, detail.why


# --------------------------------------------------------------------------
# never_fires: a strategy-level fact, computed from signals
# --------------------------------------------------------------------------

def find_never_fires(window: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Strategies that emitted signals in the window and acted on none.

    Convention 11 governs the reading: this is NOT_TESTED, not failed. The top
    skip reasons come along so that the report says WHERE the strategy stopped,
    which is the only actionable part.
    """
    out: List[Dict[str, Any]] = []
    for name, activity in sorted(window['signal_activity'].items()):
        if activity['acted'] or not activity['signals']:
            continue
        reasons = sorted(activity['skip_reasons'].items(),
                         key=lambda kv: (-kv[1], kv[0]))
        counted = sum(activity['skip_reasons'].values())
        if counted != activity['skipped']:  # pragma: no cover - guard
            raise AssertionError(
                'skip reasons for %s sum to %d but %d signals were skipped; '
                'Convention 20' % (name, counted, activity['skipped']))
        out.append({
            'strategy': name,
            'signals': activity['signals'],
            'acted': 0,
            'skipped': activity['skipped'],
            'top_skip_reasons': reasons[:5],
            'status': 'NOT_TESTED',
            'note': 'never fired in this window; Convention 11 makes this '
                    'NOT_TESTED, so it is reported but not written to the '
                    'hypothesis graph as a failure and not proposed for a kill',
        })
    return out


# --------------------------------------------------------------------------
# The window review
# --------------------------------------------------------------------------

def _assert_accounting(summary: Dict[str, Any]) -> None:
    """Convention 20, enforced rather than described.

    Three identities. Any break is a programming error that would otherwise
    reach a document recommending a strategy be killed.
    """
    if summary['winners'] + summary['losers'] + summary['flat'] != summary['closed']:
        raise AssertionError(
            'closed %d != winners %d + losers %d + flat %d'
            % (summary['closed'], summary['winners'], summary['losers'],
               summary['flat']))
    by_mode = summary['by_mode']
    if sum(by_mode.values()) != summary['losers']:
        raise AssertionError(
            'per-mode counts sum to %d but %d losing trades were classified'
            % (sum(by_mode.values()), summary['losers']))
    reasons = summary['unclassified_reasons']
    if sum(reasons.values()) != by_mode.get(UNCLASSIFIED, 0):
        raise AssertionError(
            'unclassified reasons sum to %d but %d trades are unclassified'
            % (sum(reasons.values()), by_mode.get(UNCLASSIFIED, 0)))


def classify_window(since_ts: int, until_ts: Optional[int] = None,
                    db_path: str = DB_PATH) -> Dict[str, Any]:
    """Classify every losing closed trade in `[since_ts, until_ts)`.

    Opens the trading database READ-ONLY. Returns per-trade classifications, the
    per-strategy roll-up, the never-fired strategies, and a summary whose counts
    are asserted to sum back to the total.
    """
    until_ts = until_ts if until_ts is not None else now_ms()
    if int(until_ts) < int(since_ts):
        raise ValueError('until_ts %d is before since_ts %d'
                         % (until_ts, since_ts))

    conn = hg.connect(db_path, read_only=True)
    try:
        window = load_window(conn, int(since_ts), int(until_ts))
    finally:
        conn.close()

    context = build_context(window)

    classifications: List[Classification] = []
    by_mode: Dict[str, int] = {mode: 0 for mode in CRITIC_MODES
                               if mode != 'never_fires'}
    reasons: Dict[str, int] = {reason: 0 for reason in UNCLASSIFIED_REASONS}
    winners = losers = flat = 0

    for row in window['positions']:
        pnl = row.get('pnl_net')
        if pnl is None:
            pnl = row.get('pnl_gross')
        if pnl is not None and pnl > 0:
            winners += 1
            continue
        if pnl is None or pnl == 0:
            flat += 1
            continue
        losers += 1
        detail = classify_trade_detail(row, context)
        classifications.append(detail)
        by_mode[detail.mode] = by_mode.get(detail.mode, 0) + 1
        if detail.unclassified_reason:
            reasons[detail.unclassified_reason] += 1

    per_strategy: Dict[str, Dict[str, Any]] = {}
    for detail in classifications:
        bucket = per_strategy.setdefault(detail.strategy, {})
        bucket[detail.mode] = bucket.get(detail.mode, 0) + 1

    summary = {
        'since_ts': int(since_ts),
        'until_ts': int(until_ts),
        'since_iso': iso(int(since_ts)),
        'until_iso': iso(int(until_ts)),
        'closed': len(window['positions']),
        'winners': winners,
        'losers': losers,
        'flat': flat,
        'by_mode': by_mode,
        'unclassified_reasons': reasons,
        'per_strategy_modes': per_strategy,
        'not_decidable': dict(NOT_DECIDABLE),
    }
    _assert_accounting(summary)

    return {
        'summary': summary,
        'classifications': classifications,
        'strategy_stats': context['strategy_stats'],
        'never_fires': find_never_fires(window),
        'db_path': db_path,
    }


# --------------------------------------------------------------------------
# Writing back to the hypothesis graph
# --------------------------------------------------------------------------

def _graph_rows(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The `(strategy, mode)` rows this review would write.

    `unclassified` is written too. It is a real verdict - "this strategy lost
    and we could not name why" - and suppressing it would make the graph read as
    though every loss had an explanation.

    `never_fires` is NOT written. See the module docstring: Convention 11 makes
    a strategy that never fired NOT_TESTED, and `record_failure_mode` can only
    write TESTED_FAILED.
    """
    grouped: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for detail in result['classifications']:
        key = (detail.strategy, detail.mode)
        bucket = grouped.setdefault(key, {
            'strategy_name': detail.strategy,
            'failure_mode': detail.mode,
            'occurrences': 0,
            'examples': [],
        })
        bucket['occurrences'] += 1
        if len(bucket['examples']) < 3:
            bucket['examples'].append({
                'position_id': detail.position_id,
                'pair': detail.pair,
                'entry_px': detail.entry_px,
                'exit_px': detail.exit_px,
                'pnl_net': detail.pnl_net,
                'exit_reason': detail.exit_reason,
                'why': detail.why,
            })

    summary = result['summary']
    rows: List[Dict[str, Any]] = []
    for bucket in grouped.values():
        stats = result['strategy_stats'].get(bucket['strategy_name'], {})
        # Deterministic for a fixed window: no wall-clock value goes in here, so
        # re-running the same window produces 'unchanged' rather than a phantom
        # update.
        bucket['evidence'] = {
            'source': 'agents/critic.py',
            'window_since_ms': summary['since_ts'],
            'window_until_ms': summary['until_ts'],
            'window_since_iso': summary['since_iso'],
            'window_until_iso': summary['until_iso'],
            'occurrences_of_this_mode': bucket['occurrences'],
            'closed_trades_in_window': stats.get('closed', 0),
            'wins': stats.get('wins', 0),
            'losses': stats.get('losses', 0),
            'win_rate': round(stats.get('win_rate', 0.0), 4),
            'pnl_net': round(stats.get('pnl_net', 0.0), 4),
            'mean_favourable_move': (
                round(stats['mean_favourable_move'], 6)
                if stats.get('mean_favourable_move') is not None else None),
            'sample_is_adequate': stats.get('sample_is_adequate', False),
            'min_strategy_sample': MIN_STRATEGY_SAMPLE,
            'examples': bucket['examples'],
        }
        bucket['asset_class'] = stats.get('asset_class')
        bucket['notes'] = (
            '%d of %d closed trades in %s..%s classified %s by agents/critic.py'
            % (bucket['occurrences'], stats.get('closed', 0),
               summary['since_iso'], summary['until_iso'],
               bucket['failure_mode']))
        rows.append(bucket)

    rows.sort(key=lambda r: (-r['occurrences'], r['strategy_name'],
                             r['failure_mode']))
    return rows


def update_hypothesis_graph(result: Dict[str, Any], *, db_path: str = DB_PATH,
                            dry_run: bool = False) -> Dict[str, Any]:
    """Write the classified failure modes into `hypothesis_graph`.

    Idempotent by construction: `hypothesis_graph.record_failure_mode` upserts
    on `(strategy_name, hypothesis, asset_class, market_regime, source)` and the
    synthesised hypothesis text is mode-specific and stable, so running the
    critic twice over the same window reports `unchanged` rather than inserting
    a second row. Source is `critic`, which keeps these verdicts from colliding
    with `populate_from_shadow`'s under that same identity.

    `dry_run=True` opens no write connection at all and returns exactly the rows
    it would have written.
    """
    rows = _graph_rows(result)
    out: Dict[str, Any] = {
        'dry_run': bool(dry_run),
        'source': SOURCE_CRITIC,
        'rows': rows,
        'inserted': 0, 'updated': 0, 'unchanged': 0,
        'never_fires_not_written': [n['strategy'] for n in result['never_fires']],
        'never_fires_not_written_because':
            'Convention 11: a strategy that never fired is NOT_TESTED, and '
            'record_failure_mode can only write TESTED_FAILED',
    }
    if dry_run:
        return out

    conn = hg.connect(db_path)
    try:
        for row in rows:
            rowid, action = hg.upsert_hypothesis(
                conn,
                strategy_name=row['strategy_name'],
                hypothesis='%s has exploitable edge (failed: %s)'
                           % (row['strategy_name'], row['failure_mode']),
                status=hg.FAILED_STATUS,
                source=SOURCE_CRITIC,
                market_regime='any',
                asset_class=row['asset_class'],
                evidence=row['evidence'],
                failure_mode=row['failure_mode'],
                date_tested=result['summary']['until_ts'],
                notes=row['notes'],
            )
            row['hypothesis_id'] = rowid
            row['action'] = action
            out[action] = out.get(action, 0) + 1
    finally:
        conn.close()

    written = out['inserted'] + out['updated'] + out['unchanged']
    if written != len(rows):  # pragma: no cover - guard
        raise AssertionError('%d rows offered, %d accounted for; Convention 20'
                             % (len(rows), written))
    return out


# --------------------------------------------------------------------------
# Kill recommendations
# --------------------------------------------------------------------------

def kill_recommendations(result: Dict[str, Any], *,
                         threshold: int = KILL_THRESHOLD
                         ) -> List[Dict[str, Any]]:
    """`(strategy, mode)` pairs seen at least `threshold` times in the window.

    Three is a very low bar, so every recommendation carries the occurrence
    count AND the strategy's closed-trade count, and anything under
    PROVISIONAL_TRADE_FLOOR closed trades is marked provisional (Convention 7).

    `unclassified` never produces a recommendation: "we could not tell why this
    lost" is not grounds to retire anything, and counting it would let the bar
    be cleared by ignorance. `never_fires` never produces one either
    (Convention 11).

    A strategy that is NET POSITIVE over the window is withheld rather than
    recommended, and says so. This is not cosmetic. `PM_temporal_arbitrage` wins
    19.5% of 41 closed trades, which is genuinely below a coin flip and is a
    correct `entry_signal_wrong` classification, and it is up +0.59 over the
    same window because it is a two-leg pair trade with asymmetric payoffs. A
    bad direction call and an unprofitable strategy are different claims, and
    only the second is grounds for a kill. Withheld entries are RETURNED, with
    `recommended=False` and a reason, so the finding is still visible instead of
    silently filtered (Convention 20).
    """
    counts: Dict[Tuple[str, str], List[Classification]] = {}
    for detail in result['classifications']:
        if detail.mode == UNCLASSIFIED:
            continue
        counts.setdefault((detail.strategy, detail.mode), []).append(detail)

    out: List[Dict[str, Any]] = []
    for (strategy, mode), details in counts.items():
        if len(details) < int(threshold):
            continue
        stats = result['strategy_stats'].get(strategy, {})
        closed = stats.get('closed', 0)
        pnl = stats.get('pnl_net', 0.0)
        provisional = closed < PROVISIONAL_TRADE_FLOOR
        worst = min(details, key=lambda d: d.pnl_net if d.pnl_net is not None else 0.0)
        withheld_reason = None
        if pnl >= 0:
            withheld_reason = (
                'the strategy is net %+.2f over this window. The failure mode '
                'is real and stands, but a profitable strategy is not a kill '
                'candidate; that is a repair question, not a retirement one.'
                % pnl)
        out.append({
            'strategy': strategy,
            'failure_mode': mode,
            'occurrences': len(details),
            'closed_trades_in_window': closed,
            'wins': stats.get('wins', 0),
            'win_rate': round(stats.get('win_rate', 0.0), 4),
            'pnl_net': round(pnl, 4),
            'provisional': provisional,
            'recommended': withheld_reason is None,
            'withheld_reason': withheld_reason,
            'verdict_strength': (
                'PROVISIONAL (%d closed trades, under the %d-trade floor; '
                'Convention 7 calls this a shrug, not a verdict)'
                % (closed, PROVISIONAL_TRADE_FLOOR)) if provisional else (
                'SUPPORTED (%d closed trades in the window)' % closed),
            'example_position_id': worst.position_id,
            'example_why': worst.why,
        })
    out.sort(key=lambda r: (not r['recommended'], r['provisional'],
                            -r['occurrences'], r['strategy']))
    return out


def render_kill_file(result: Dict[str, Any],
                     recommendations: List[Dict[str, Any]]) -> str:
    """The markdown that lands in docs/handoffs/from-raven/."""
    summary = result['summary']
    lines = [
        '# Critic kill recommendations',
        '',
        '**Generated by:** `agents/critic.py`',
        '**Review window:** %s to %s' % (summary['since_iso'], summary['until_iso']),
        '**Closed trades in window:** %d (%d winners, %d losers, %d flat)'
        % (summary['closed'], summary['winners'], summary['losers'],
           summary['flat']),
        '**Kill threshold:** %d occurrences of the same failure mode for the '
        'same strategy' % KILL_THRESHOLD,
        '',
        'Three occurrences is a low bar. Every line below states the occurrence '
        'count and the strategy sample size next to it, and anything resting on '
        'fewer than %d closed trades is marked PROVISIONAL. Convention 7: a FAIL '
        'on 200k trades is a verdict, on 1,700 a shrug.' % PROVISIONAL_TRADE_FLOOR,
        '',
        '## Recommendations',
        '',
    ]
    recommended = [r for r in recommendations if r['recommended']]
    withheld = [r for r in recommendations if not r['recommended']]

    if not recommended:
        lines.append('(none: no strategy hit %d occurrences of one failure '
                     'mode in this window while also losing money)'
                     % KILL_THRESHOLD)
        lines.append('')
    else:
        lines.append('| strategy | failure mode | occurrences | closed trades | '
                     'win rate | net P&L | strength |')
        lines.append('|---|---|---|---|---|---|---|')
        for rec in recommended:
            lines.append('| %s | %s | %d | %d | %.1f%% | %+.2f | %s |'
                         % (rec['strategy'], rec['failure_mode'],
                            rec['occurrences'], rec['closed_trades_in_window'],
                            100.0 * rec['win_rate'], rec['pnl_net'],
                            'PROVISIONAL' if rec['provisional'] else 'SUPPORTED'))
        lines.append('')
        for rec in recommended:
            lines.append('### %s: %s' % (rec['strategy'], rec['failure_mode']))
            lines.append('')
            lines.append('- Occurrences in window: **%d**' % rec['occurrences'])
            lines.append('- Strategy closed trades in window: **%d**'
                         % rec['closed_trades_in_window'])
            lines.append('- Strategy net P&L in window: **%+.2f**'
                         % rec['pnl_net'])
            lines.append('- %s' % rec['verdict_strength'])
            lines.append('- Example position `%s`: %s'
                         % (rec['example_position_id'], rec['example_why']))
            lines.append('')

    lines.append('## Withheld: the mode cleared the bar but the strategy is '
                 'net positive')
    lines.append('')
    if not withheld:
        lines.append('(none)')
        lines.append('')
    else:
        lines.append('The classification stands. The kill does not: a bad '
                     'direction call and an unprofitable strategy are '
                     'different claims, and only the second is grounds for '
                     'retirement.')
        lines.append('')
        for rec in withheld:
            lines.append('- **%s / %s**: %d occurrences over %d closed trades, '
                         'win rate %.1f%%. %s'
                         % (rec['strategy'], rec['failure_mode'],
                            rec['occurrences'], rec['closed_trades_in_window'],
                            100.0 * rec['win_rate'], rec['withheld_reason']))
        lines.append('')

    lines.append('## Not a kill: strategies that never fired')
    lines.append('')
    if not result['never_fires']:
        lines.append('(none)')
        lines.append('')
    else:
        lines.append('Convention 11: never-fired is NOT_TESTED, not a failure. '
                     'These are listed so the blocker is visible, and they are '
                     'deliberately NOT candidates for a kill and are NOT '
                     'written to the hypothesis graph.')
        lines.append('')
        lines.append('| strategy | signals | acted | top skip reason |')
        lines.append('|---|---|---|---|')
        for item in result['never_fires']:
            top = item['top_skip_reasons'][0] if item['top_skip_reasons'] else ('(none)', 0)
            lines.append('| %s | %d | 0 | `%s` x%d |'
                         % (item['strategy'], item['signals'], top[0], top[1]))
        lines.append('')

    lines.append('## What the classifier could not decide')
    lines.append('')
    lines.append('| bucket | count |')
    lines.append('|---|---|')
    for reason, count in sorted(summary['unclassified_reasons'].items()):
        lines.append('| `%s` | %d |' % (reason, count))
    lines.append('')
    for mode, why in sorted(summary['not_decidable'].items()):
        lines.append('- **`%s` is NOT DECIDABLE from this database.** %s'
                     % (mode, why))
    lines.append('')
    return '\n'.join(lines)


def write_kill_recommendations(result: Dict[str, Any], *,
                               out_dir: str = KILL_DIR,
                               dry_run: bool = False,
                               threshold: int = KILL_THRESHOLD
                               ) -> Dict[str, Any]:
    """Render and (unless dry) write `<YYYY-MM-DD>-critic-kill-recommendations.md`."""
    recommendations = kill_recommendations(result, threshold=threshold)
    text = render_kill_file(result, recommendations)
    day = datetime.fromtimestamp(
        result['summary']['until_ts'] / 1000.0, timezone.utc).strftime('%Y-%m-%d')
    path = os.path.join(out_dir, '%s-critic-kill-recommendations.md' % day)
    out = {
        'path': path,
        'written': False,
        'dry_run': bool(dry_run),
        'recommendations': recommendations,
        'chars': len(text),
        'text': text,
    }
    if dry_run:
        return out
    vault_writer.atomic_write(path, text)
    out['written'] = True
    return out


# --------------------------------------------------------------------------
# The post-mortem
# --------------------------------------------------------------------------

def build_evidence(result: Dict[str, Any], *, max_examples: int = 4) -> str:
    """The evidence block handed to Opus. Real numbers, real trades, no prose.

    Everything the post-mortem is allowed to cite has to appear here, so this
    carries the accounting identities, the per-strategy table, worked examples
    per failure mode, the unclassified buckets, and an explicit statement of
    what the classifier cannot decide. The last part matters most: without it a
    model reads the absence of `regime_mismatch` as evidence that regime is not
    the problem.
    """
    summary = result['summary']
    lines = [
        'REVIEW WINDOW: %s to %s' % (summary['since_iso'], summary['until_iso']),
        '',
        'ACCOUNTING (every closed trade counted exactly once):',
        '  closed trades      %d' % summary['closed'],
        '  winners            %d' % summary['winners'],
        '  losers (classified)%d' % summary['losers'],
        '  flat / no P&L      %d' % summary['flat'],
        '',
        'FAILURE MODE COUNTS (sum to the %d losers):' % summary['losers'],
    ]
    for mode, count in sorted(summary['by_mode'].items(),
                              key=lambda kv: (-kv[1], kv[0])):
        share = (100.0 * count / summary['losers']) if summary['losers'] else 0.0
        lines.append('  %-22s %5d  (%.1f%%)' % (mode, count, share))

    lines += ['', 'UNCLASSIFIED BREAKDOWN (sums to the unclassified count):']
    for reason, count in sorted(summary['unclassified_reasons'].items(),
                                key=lambda kv: (-kv[1], kv[0])):
        lines.append('  %-34s %5d' % (reason, count))

    lines += ['', 'PER-STRATEGY (all closed trades in the window):',
              '  %-30s %6s %6s %8s %10s %12s %10s'
              % ('strategy', 'closed', 'wins', 'win_rate', 'pnl_net',
                 'mean_move', 'model_px')]
    for name, stats in sorted(result['strategy_stats'].items(),
                              key=lambda kv: kv[1]['pnl_net']):
        mean_move = stats['mean_favourable_move']
        lines.append('  %-30s %6d %6d %7.1f%% %10.2f %12s %10s'
                     % (name, stats['closed'], stats['wins'],
                        100.0 * stats['win_rate'], stats['pnl_net'],
                        ('%+.4f' % mean_move) if mean_move is not None
                        else 'n/a',
                        'yes' if stats['has_model_price'] else 'no'))

    lines += ['', 'PER-STRATEGY FAILURE MODES:']
    for name, modes in sorted(summary['per_strategy_modes'].items()):
        parts = ', '.join('%s=%d' % (m, c) for m, c in
                          sorted(modes.items(), key=lambda kv: (-kv[1], kv[0])))
        lines.append('  %-30s %s' % (name, parts))

    lines += ['', 'WORKED EXAMPLES (up to %d per mode, worst loss first):'
              % max_examples]
    by_mode: Dict[str, List[Classification]] = {}
    for detail in result['classifications']:
        by_mode.setdefault(detail.mode, []).append(detail)
    for mode in sorted(by_mode):
        examples = sorted(
            by_mode[mode],
            key=lambda d: d.pnl_net if d.pnl_net is not None else 0.0)
        lines.append('')
        lines.append('  == %s (%d trades) ==' % (mode, len(by_mode[mode])))
        for detail in examples[:max_examples]:
            lines.append('    %s | %s | entry %.4f exit %.4f qty %s | '
                         'pnl %+.4f | exit_reason %s'
                         % (detail.strategy, detail.pair,
                            detail.entry_px or 0.0, detail.exit_px or 0.0,
                            detail.qty, detail.pnl_net or 0.0,
                            detail.exit_reason))
            lines.append('      why: %s' % detail.why)
            if detail.also_matched:
                lines.append('      a lower-priority test also matched: %s'
                             % ', '.join(detail.also_matched))

    lines += ['', 'STRATEGIES THAT NEVER FIRED (Convention 11: NOT_TESTED, '
                  'not failed, and NOT evidence against the idea):']
    if not result['never_fires']:
        lines.append('  (none)')
    for item in result['never_fires']:
        reasons = ', '.join('%s x%d' % (r, n) for r, n in item['top_skip_reasons'])
        lines.append('  %-30s %6d signals, 0 acted. top skips: %s'
                     % (item['strategy'], item['signals'], reasons))

    lines += ['', 'WHAT THIS CLASSIFIER CANNOT DECIDE (do not read absence as '
                  'evidence):']
    for mode, why in sorted(summary['not_decidable'].items()):
        lines.append('  %s: %s' % (mode, why))
    lines.append('  model_miscalibrated is scored against the realised PRICE '
                 'path, never against the model resolution probability: every '
                 'position here exits before resolution, so that event is '
                 'never observed.')
    lines.append('  stop_too_tight is only decidable where a later quote for '
                 'the same market and outcome side exists after the exit. '
                 'There is no quote tape table in this database.')

    return '\n'.join(lines)


def default_label(result: Dict[str, Any]) -> str:
    summary = result['summary']
    return '%s-to-%s' % (summary['since_iso'][:16].replace(':', ''),
                         summary['until_iso'][:16].replace(':', ''))


def post_mortem_path(result: Dict[str, Any], *, label: Optional[str] = None,
                     out_dir: Optional[str] = None,
                     filename: Optional[str] = None,
                     now: Optional[datetime] = None) -> str:
    """Where `vault_writer.write_post_mortem` would put this note.

    Duplicating the naming rule is unpleasant, but the alternative is worse: the
    critic promises `--dry-run` writes NOTHING, and the only way to report the
    path without writing is to know it. Convention 22 says a claim in a
    docstring is not a wiring test, so
    `test_the_dry_run_path_matches_where_the_writer_actually_writes` writes a
    real note into a tmp dir and asserts the two agree.
    """
    label = label or default_label(result)
    now = now or datetime.now(timezone.utc)
    name = filename or '%s-critic-%s.md' % (now.strftime('%Y-%m-%d'),
                                            vault_writer._slug(label))
    return os.path.join(out_dir or vault_writer.CYCLES_DIR, name)


def write_post_mortem(result: Dict[str, Any], *, label: Optional[str] = None,
                      out_dir: Optional[str] = None,
                      filename: Optional[str] = None,
                      dry_run: bool = False,
                      skip_model: bool = False,
                      vault_context: Optional[str] = None,
                      timeout_s: Optional[int] = None
                      ) -> vault_writer.VaultWrite:
    """Build the evidence block and ask Opus for the post-mortem.

    The model turn, the validation, the atomic write, the provenance header and
    the deterministic fallback all live in `agents/vault_writer.py`. This
    function's whole job is the evidence and the vault context.

    The two "do less" flags mean DIFFERENT things and the difference matters:

      dry_run=True     nothing is written, anywhere. `vault_writer` is never
                       called. Returns `written=False` and the path the note
                       WOULD have taken.
      skip_model=True  `vault_writer`'s own dry run: no model turn is spawned,
                       and the deterministic fallback note IS written to disk.

    They were one flag until a `--dry-run` of this module deposited a note built
    from a unit test's throwaway database into the real vault. `vault_writer`
    is right about what its flag means; the critic just needed its own.
    """
    label = label or default_label(result)
    evidence = build_evidence(result)

    if dry_run:
        return vault_writer.VaultWrite(
            post_mortem_path(result, label=label, out_dir=out_dir,
                             filename=filename),
            written=False, used_model=False, task='critic_post_mortem',
            error='dry_run: nothing written, no model called, %d chars of '
                  'evidence prepared' % len(evidence))

    if vault_context is None:
        vault_context = vault_reader.render_context()
    kwargs: Dict[str, Any] = {
        'vault_context': vault_context,
        'out_dir': out_dir,
        'filename': filename,
        # `vault_writer` called this `dry_run` until a --dry-run of THIS module
        # wrote a note built from synthetic numbers into the real vault. The
        # flag never meant "write nothing"; it meant "skip the model, still
        # write". It is now named for what it does, on both sides.
        'skip_model': skip_model,
    }
    if timeout_s is not None:
        kwargs['timeout_s'] = timeout_s
    return vault_writer.write_post_mortem(label, evidence, **kwargs)


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def render_report(result: Dict[str, Any]) -> str:
    """The human summary the CLI prints."""
    summary = result['summary']
    lines = [
        'CRITIC REVIEW  %s .. %s' % (summary['since_iso'], summary['until_iso']),
        '  database:        %s' % result['db_path'],
        '  closed trades:   %d  (winners %d, losers %d, flat %d)'
        % (summary['closed'], summary['winners'], summary['losers'],
           summary['flat']),
        '',
        'FAILURE MODES (sum to the %d losers):' % summary['losers'],
    ]
    for mode, count in sorted(summary['by_mode'].items(),
                              key=lambda kv: (-kv[1], kv[0])):
        lines.append('  %-22s %5d' % (mode, count))
    lines += ['', 'UNCLASSIFIED REASONS (sum to %d):'
              % summary['by_mode'].get(UNCLASSIFIED, 0)]
    for reason, count in sorted(summary['unclassified_reasons'].items(),
                                key=lambda kv: (-kv[1], kv[0])):
        lines.append('  %-34s %5d' % (reason, count))
    lines += ['', 'NOT DECIDABLE FROM THIS DATABASE:']
    for mode, why in sorted(summary['not_decidable'].items()):
        lines.append('  %s: %s' % (mode, why.split(',')[0]))
    lines += ['', 'NEVER FIRED (NOT_TESTED, not failed): %d strategies'
              % len(result['never_fires'])]
    for item in result['never_fires']:
        top = item['top_skip_reasons'][0] if item['top_skip_reasons'] else ('(none)', 0)
        lines.append('  %-30s %6d signals, top skip %s x%d'
                     % (item['strategy'], item['signals'], top[0], top[1]))
    return '\n'.join(lines)


def result_to_dict(result: Dict[str, Any]) -> Dict[str, Any]:
    """JSON-safe view of a review."""
    return {
        'summary': result['summary'],
        'strategy_stats': result['strategy_stats'],
        'never_fires': result['never_fires'],
        'classifications': [c.to_dict() for c in result['classifications']],
        'db_path': result['db_path'],
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python3 -m agents.critic',
        description='Classify losing trades into failure modes, update the '
                    'hypothesis graph, write a post-mortem, recommend kills.')
    parser.add_argument('--since', default='last',
                        help="'last', a duration like 4h/90m/7d, an ISO date, "
                             'or an epoch value. Default: last.')
    parser.add_argument('--until', default=None,
                        help='same formats as --since. Default: now.')
    parser.add_argument('--db', default=DB_PATH,
                        help='trading database (opened read-only for reads)')
    parser.add_argument('--dry-run', action='store_true',
                        help='write nothing; report exactly what would be '
                             'written. Implies every action flag.')
    parser.add_argument('--update-graph', action='store_true',
                        help='write classified failure modes to hypothesis_graph')
    parser.add_argument('--post-mortem', action='store_true',
                        help='ask Opus for the vault post-mortem')
    parser.add_argument('--skip-model', action='store_true',
                        help='write the post-mortem note but do not spawn a '
                             'model turn; the note says so in its header')
    parser.add_argument('--kill-file', action='store_true',
                        help='write docs/handoffs/from-raven/'
                             '<date>-critic-kill-recommendations.md')
    parser.add_argument('--all', action='store_true',
                        help='--update-graph --post-mortem --kill-file')
    parser.add_argument('--json', action='store_true',
                        help='print the full review as JSON')
    parser.add_argument('--evidence', action='store_true',
                        help='print the evidence block that would go to Opus')
    parser.add_argument('--threshold', type=int, default=KILL_THRESHOLD,
                        help='occurrences before a kill is recommended '
                             '(default %d)' % KILL_THRESHOLD)
    parser.add_argument('--state', default=STATE_PATH,
                        help='review bookmark file for --since last')
    parser.add_argument('--no-state', action='store_true',
                        help='do not move the bookmark after a real run')
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    state = load_state(args.state)
    since_ts, since_how = parse_since(args.since, state=state)
    until_ts = (parse_since(args.until, state=state)[0] if args.until
                else now_ms())

    result = classify_window(since_ts, until_ts, db_path=args.db)

    do_graph = args.update_graph or args.all or args.dry_run
    do_mortem = args.post_mortem or args.all or args.dry_run
    do_kill = args.kill_file or args.all or args.dry_run

    print(render_report(result))
    print('')
    print('window resolved from --since %r: %s' % (args.since, since_how))
    if state.get('_unreadable'):
        print('NOTE: the state file could not be read (%s); --since last fell '
              'back to scanning from 0' % state['_unreadable'])

    actions: Dict[str, Any] = {}

    if do_graph:
        graph = update_hypothesis_graph(result, db_path=args.db,
                                        dry_run=args.dry_run)
        actions['hypothesis_graph'] = graph
        print('')
        print('HYPOTHESIS GRAPH (%s): %d row(s)'
              % ('DRY RUN, nothing written' if args.dry_run else 'written',
                 len(graph['rows'])))
        for row in graph['rows']:
            print('  %-30s %-22s x%-4d %s'
                  % (row['strategy_name'], row['failure_mode'],
                     row['occurrences'], row.get('action', 'would-upsert')))
        if not args.dry_run:
            print('  inserted %d, updated %d, unchanged %d'
                  % (graph['inserted'], graph['updated'], graph['unchanged']))
        if graph['never_fires_not_written']:
            print('  NOT written (%s): %s'
                  % (graph['never_fires_not_written_because'],
                     ', '.join(graph['never_fires_not_written'])))

    if do_kill:
        kill = write_kill_recommendations(result, dry_run=args.dry_run,
                                          threshold=args.threshold)
        actions['kill_file'] = {k: v for k, v in kill.items() if k != 'text'}
        print('')
        recommended = [r for r in kill['recommendations'] if r['recommended']]
        withheld = [r for r in kill['recommendations'] if not r['recommended']]
        print('KILL RECOMMENDATIONS (%s): %d recommended, %d withheld, file %s'
              % ('DRY RUN, nothing written' if args.dry_run else 'written',
                 len(recommended), len(withheld), kill['path']))
        for rec in recommended:
            print('  KILL     %-30s %-22s x%-4d over %d closed trades, '
                  'pnl %+.2f  %s'
                  % (rec['strategy'], rec['failure_mode'], rec['occurrences'],
                     rec['closed_trades_in_window'], rec['pnl_net'],
                     'PROVISIONAL' if rec['provisional'] else 'SUPPORTED'))
        for rec in withheld:
            print('  WITHHELD %-30s %-22s x%-4d net positive %+.2f, so the '
                  'mode stands but the kill does not'
                  % (rec['strategy'], rec['failure_mode'], rec['occurrences'],
                     rec['pnl_net']))

    if do_mortem:
        write = write_post_mortem(result, dry_run=args.dry_run,
                                  skip_model=args.skip_model)
        actions['post_mortem'] = write.to_dict()
        print('')
        print('POST-MORTEM (%s): %s'
              % ('DRY RUN, nothing written' if args.dry_run
                 else ('written by model' if write.used_model
                       else 'written as FALLBACK, the model did not run'),
                 write.path))
        if write.error:
            print('  note: %s' % write.error)

    if args.evidence:
        print('')
        print(build_evidence(result))

    if args.json:
        payload = result_to_dict(result)
        payload['actions'] = actions
        print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False,
                         default=str))

    if not args.dry_run and not args.no_state and (do_graph or do_kill or do_mortem):
        save_state(until_ts, args.state,
                   extra={'last_closed_trades': result['summary']['closed'],
                          'last_losers': result['summary']['losers']})

    return 0


if __name__ == '__main__':  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
