"""Shared reading and rendering layer for the shadow-trading summaries.

Both `scripts/daily_shadow_summary.py` and `scripts/weekly_shadow_summary.py`
read the same two artefacts and render them the same way. The logic lives here
once so the two reports can never disagree about what a skip is or how a win
rate is computed.

## The two data sources, and which one is authoritative

`engine/polymarket/shadow_loop.py` writes EVERY evaluation to both:

  db/trading.db  `signals`  one row per (cycle, strategy), `acted` 0 or 1,
                            `skip_reason` verbatim, `ts` in unix MILLISECONDS.
                 `positions`/`orders`/`fills`  written only on a real fill.
                 `equity_snapshots`, `audit_log`, `risk_events`.
  research/polymarket_paper/polymarket_paper_log.csv
                            the paper adapter's own decision log, `ts` in
                            unix SECONDS.

The DB is authoritative for every number in these reports. The CSV is read as a
CROSS CHECK only, and any row-count difference between the two is reported as a
reconciliation line rather than reconciled away. The two legitimately differ:
the CSV is appended across sessions and survives a database that was replaced,
and one loop path deliberately suppresses its CSV row when the adapter has
already written one.

## Convention 11: an outage is never a flat day

`open_db` RAISES `DataSourceError` when the database is missing, is not a
database, or cannot be read. The callers turn that into an explicit ERROR
report and a non-zero exit code. There is no code path in this module that can
turn an unreadable database into a summary saying "0 trades", because "we could
not look" and "we looked and there was nothing" are opposite facts.

The CSV is not fatal: it is a cross check, so a missing CSV is reported as
NOT_TESTED on the reconciliation line and every other number stands.

## Convention 20: every skip is counted AND categorised

`classify_reason` puts each skip reason in exactly one of five buckets, and
`skip_category_counts` asserts that the buckets sum to the skip total. Anything
the rule table does not name lands in UNCLASSIFIED, which is PRINTED with the
offending reason strings attached. An unrecognised reason is a missing rule and
has to look like one; it must never be absorbed into a neighbouring bucket.

## Undefined is not zero

`win_rate` returns None when no trade has closed. `profit_factor` returns None
when there are no losses, including the case where there are no trades at all.
Both render as "n/a" with the reason in parentheses. A win rate of 0% means the
strategy traded and lost; a profit factor of infinity means nothing at all.
Neither may be printed for a session that has not traded.
"""
import csv
import datetime
import json
import math
import os
import sqlite3
from collections import Counter, OrderedDict

try:
    from zoneinfo import ZoneInfo
except ImportError:                                     # pragma: no cover
    ZoneInfo = None

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_DB_PATH = os.path.join(REPO_ROOT, 'db', 'trading.db')
DEFAULT_CSV_PATH = os.path.join(
    REPO_ROOT, 'research', 'polymarket_paper', 'polymarket_paper_log.csv')
PROPOSALS_DIR = os.path.join(REPO_ROOT, 'strategies', 'proposals')
FORGE_RUNS_PATH = os.path.join(PROPOSALS_DIR, 'forge_runs.jsonl')
SUMMARY_DIR = os.path.join(REPO_ROOT, 'logs', 'summaries')

ET_TZ_NAME = 'America/New_York'

MODE = 'paper'

#: Actions the paper adapter writes into the CSV `action` column. Listed so a
#: new one shows up as unknown rather than being folded into an existing count.
CSV_ACTIONS = ('SKIP', 'NO_FILL', 'ENTER', 'CLOSE', 'RESOLVE')


class DataSourceError(RuntimeError):
    """A source could not be READ. Never means the source was empty."""


# ---------------------------------------------------------------------------
# Time. Every boundary in these reports is an Eastern-time calendar day.
# ---------------------------------------------------------------------------

def _et_tz():
    if ZoneInfo is None:
        raise DataSourceError(
            'zoneinfo is unavailable, so an Eastern-time day boundary cannot '
            'be computed. Refusing to guess a fixed UTC offset: it would be '
            'right in winter and wrong in summer.')
    try:
        return ZoneInfo(ET_TZ_NAME)
    except Exception as exc:
        raise DataSourceError(
            'could not load timezone {}: {}. Install tzdata.'
            .format(ET_TZ_NAME, exc))


def today_et():
    """Today's calendar date in Eastern time."""
    return datetime.datetime.now(_et_tz()).date()


def parse_date(text):
    """Parse YYYY-MM-DD, raising a message an operator can act on."""
    try:
        return datetime.datetime.strptime(text, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        raise DataSourceError(
            'could not parse {!r} as a date; expected YYYY-MM-DD'.format(text))


def et_day_bounds_ms(day):
    """Half-open [start, end) in unix milliseconds for one ET calendar day.

    Half-open on purpose: a row stamped exactly at midnight belongs to one day
    and only one day, so consecutive days can be summed without double counting
    the boundary.
    """
    tz = _et_tz()
    start = datetime.datetime.combine(day, datetime.time(0, 0), tzinfo=tz)
    end = datetime.datetime.combine(
        day + datetime.timedelta(days=1), datetime.time(0, 0), tzinfo=tz)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def et_range_bounds_ms(first_day, last_day):
    """Half-open [start, end) in ms spanning first_day through last_day."""
    start_ms, _ = et_day_bounds_ms(first_day)
    _, end_ms = et_day_bounds_ms(last_day)
    return start_ms, end_ms


def ms_to_et_string(ts_ms):
    if ts_ms is None:
        return 'n/a'
    dt = datetime.datetime.fromtimestamp(ts_ms / 1000.0, _et_tz())
    return dt.strftime('%Y-%m-%d %H:%M:%S ET')


def ms_to_et_date(ts_ms):
    return datetime.datetime.fromtimestamp(ts_ms / 1000.0, _et_tz()).date()


# ---------------------------------------------------------------------------
# Undefined-safe arithmetic. Nothing in this module divides without a guard.
# ---------------------------------------------------------------------------

def safe_div(numerator, denominator):
    """None when the denominator is zero. Never raises, never returns inf."""
    if denominator is None or denominator == 0:
        return None
    value = numerator / denominator
    return value if math.isfinite(value) else None


def win_rate(wins, losses):
    """Fraction of decided trades that won, or None when none have decided.

    Flat trades (exactly zero net) are excluded from the denominator and
    reported separately. A binary that resolved is never flat in practice, but
    the crypto path can produce one and it is not a win.
    """
    return safe_div(wins, wins + losses)


def profit_factor(gross_profit, gross_loss):
    """Gross profit over gross loss, or None when there are no losses.

    Undefined, not infinite. A profit factor computed against zero losses says
    nothing about an edge; it says the sample has not yet contained a loss.
    """
    if gross_loss is None or gross_loss <= 0:
        return None
    return safe_div(gross_profit, gross_loss)


def fmt_rate(value, n_decided, unit='trades'):
    if value is None:
        return 'n/a ({} {})'.format(n_decided, unit)
    return '{:.1f}%'.format(value * 100.0)


def fmt_pf(value, n_losses):
    if value is None:
        return 'n/a ({} losing trades)'.format(n_losses)
    return '{:.2f}'.format(value)


def fmt_usd(value):
    if value is None:
        return 'n/a'
    return '${:,.2f}'.format(value)


def fmt_pct_of(part, whole):
    frac = safe_div(part, whole)
    return 'n/a' if frac is None else '{:.1f}%'.format(frac * 100.0)


# ---------------------------------------------------------------------------
# Skip taxonomy (convention 20)
# ---------------------------------------------------------------------------

DATA_BLOCKER = 'DATA_BLOCKER'
NO_TRADE = 'NO_TRADE'
OPERATIONAL = 'OPERATIONAL'
ERROR = 'ERROR'
UNCLASSIFIED = 'UNCLASSIFIED'

CATEGORY_ORDER = (DATA_BLOCKER, NO_TRADE, OPERATIONAL, ERROR, UNCLASSIFIED)

CATEGORY_NOTE = {
    DATA_BLOCKER: 'input missing or path not simulable; the strategy could '
                  'NOT be tested (convention 11)',
    NO_TRADE: 'the strategy looked at real data and declined; this is the '
              'strategy working',
    OPERATIONAL: 'a halt or a risk cap blocked the entry, not the strategy',
    ERROR: 'a bug or an exception; a decision window we failed to evaluate',
    UNCLASSIFIED: 'no rule for this reason yet; add one before citing these '
                  'counts',
}

#: Exact reason strings. Kept explicit rather than pattern matched: guessing a
#: category from the shape of a string is how `no_market` (venue has nothing)
#: and `no_streak` (venue has data, strategy declined) end up in one bucket.
_EXACT = {
    # -- inputs that are missing, so the strategy never got to decide
    'no_market': DATA_BLOCKER,
    'no_liquidity': DATA_BLOCKER,
    'no_orderbook': DATA_BLOCKER,
    'orderbook_read_error': DATA_BLOCKER,
    'no_asks': DATA_BLOCKER,
    'no_bids': DATA_BLOCKER,
    'no_bid_liquidity': DATA_BLOCKER,
    'no_spot_or_strike': DATA_BLOCKER,
    'no_lead_or_atr': DATA_BLOCKER,
    'no_magnitude_data': DATA_BLOCKER,
    'insufficient_window_history': DATA_BLOCKER,
    'no_window_clock': DATA_BLOCKER,
    'zero_atr_undefined_stretch': DATA_BLOCKER,
    'unavailable': DATA_BLOCKER,
    # a maker quote the taker-only adapter cannot model is NOT_TESTED, not a
    # strategy that declined
    'maker_fill_not_simulated': DATA_BLOCKER,
    'maker_quote_not_simulable': DATA_BLOCKER,

    # -- the strategy saw real data and said no
    'no_streak': NO_TRADE,
    'not_through_strike': NO_TRADE,
    'lead_below_zone': NO_TRADE,
    'lead_above_zone': NO_TRADE,
    'book_too_tight_to_arm': NO_TRADE,
    'past_quote_window': NO_TRADE,
    'window_not_open': NO_TRADE,
    'late_in_window': NO_TRADE,
    'not_final_third_of_15m': NO_TRADE,
    'pair_cost_above_cap': NO_TRADE,
    'unfillable_at_cap': NO_TRADE,
    'insufficient_ask_depth': NO_TRADE,
    'book_above_limit': NO_TRADE,
    'bid_below_limit': NO_TRADE,
    'book_price_out_of_range': NO_TRADE,
    'limit_price_out_of_range': NO_TRADE,
    'trades_this_window': NO_TRADE,
    'no_mispricing': NO_TRADE,
    'edge_below_threshold': NO_TRADE,

    # -- risk and control surfaces
    'halted': OPERATIONAL,
    'max_concurrent_positions': OPERATIONAL,
    'over_notional_cap': OPERATIONAL,
    'unsizable_at_cap': OPERATIONAL,
    # D-366. Operational, not NO_TRADE: the strategy wanted the trade and the
    # book had too little money left to buy the exchange minimum. Reading it as
    # "no signal" would hide a book that needs re-funding (D-358).
    'unsizable_at_position_pct': OPERATIONAL,

    # -- ours, and broken
    'enter_without_legs': ERROR,
    'unknown_outcome_token': ERROR,
    'cycle_exception': ERROR,
    'unknown_position': ERROR,
    'position_not_open': ERROR,
    'invalid_sell_size': ERROR,
    'partial_sell_refused': ERROR,
    'unreported': ERROR,
    'unspecified': ERROR,
}

#: Prefix rules, applied only after the exact table misses. `api_error` carries
#: an attempt counter (`api_error:attempt_3`) and `risk_gate:`/`adapter:` carry
#: somebody else's verbatim reason after the colon.
_PREFIX = (
    ('api_error', DATA_BLOCKER),
    ('cycle_exception', ERROR),
    ('strategy:', None),        # strip and re-classify the tail
    ('adapter:', None),
    ('risk_gate:', None),
)

_PASSTHROUGH_PREFIX_DEFAULT = {
    'strategy:': NO_TRADE,
    'adapter:': NO_TRADE,
    'risk_gate:': OPERATIONAL,
}


def classify_reason(reason):
    """Map one skip reason string to exactly one category.

    Unknown reasons return UNCLASSIFIED. They are never guessed into a
    neighbouring bucket, and the callers print them with their counts so the
    missing rule is visible rather than absorbed.
    """
    if reason is None:
        return UNCLASSIFIED
    text = str(reason).strip()
    if not text:
        return UNCLASSIFIED
    if text in _EXACT:
        return _EXACT[text]
    for prefix, category in _PREFIX:
        if text.startswith(prefix):
            if category is not None:
                return category
            tail = text[len(prefix):]
            # The tail is another component's own reason. Classify it on its
            # own merits; fall back to what the wrapper implies.
            if tail in _EXACT:
                return _EXACT[tail]
            for inner_prefix, inner_cat in _PREFIX:
                if inner_cat is not None and tail.startswith(inner_prefix):
                    return inner_cat
            return _PASSTHROUGH_PREFIX_DEFAULT[prefix]
    return UNCLASSIFIED


def skip_category_counts(reason_counts):
    """Bucket a {reason: count} mapping and assert the accounting identity.

    Returns (categories, unclassified_reasons). The assertion is the point of
    the function: a bucketing that does not sum to the input has dropped a
    skip, which is the exact failure convention 20 exists to catch.
    """
    categories = OrderedDict((c, 0) for c in CATEGORY_ORDER)
    unclassified = Counter()
    for reason, count in reason_counts.items():
        category = classify_reason(reason)
        categories[category] += count
        if category == UNCLASSIFIED:
            unclassified[reason] += count
    total_in = sum(reason_counts.values())
    total_out = sum(categories.values())
    assert total_in == total_out, (
        'skip categorisation lost rows: in={} out={} categories={}'
        .format(total_in, total_out, dict(categories)))
    return categories, unclassified


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

def open_db(db_path):
    """Open the trading DB read-only, or raise DataSourceError.

    Read-only because the shadow loop is the single writer and may be running
    right now. Every failure mode below is a REFUSAL to report, never a zero:
    a missing file, a file that is not a database, and a file we lack
    permission to read are all "could not run" (convention 11).
    """
    if not os.path.exists(db_path):
        raise DataSourceError('database not found: {}'.format(db_path))
    if not os.path.isfile(db_path):
        raise DataSourceError('not a file: {}'.format(db_path))
    try:
        conn = sqlite3.connect(
            'file:{}?mode=ro'.format(db_path), uri=True, timeout=15.0)
        conn.row_factory = sqlite3.Row
        # Touch the catalogue. `connect` is lazy, so a corrupt or non-database
        # file does not fail until the first real read.
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as exc:
        raise DataSourceError(
            'could not read database {}: {}: {}'
            .format(db_path, type(exc).__name__, exc))
    return conn


def required_tables(conn):
    names = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    return names


def check_schema(conn, db_path):
    """Refuse to report against a database missing the tables we summarise."""
    have = required_tables(conn)
    need = {'signals', 'positions', 'equity_snapshots', 'audit_log'}
    missing = sorted(need - have)
    if missing:
        raise DataSourceError(
            'database {} is missing table(s) {}; this is a schema problem, '
            'not an empty day'.format(db_path, ', '.join(missing)))


def read_signals(conn, start_ms, end_ms, mode=MODE):
    rows = conn.execute(
        'SELECT strategy_id, acted, skip_reason, ts FROM signals '
        'WHERE ts >= ? AND ts < ? AND mode = ?',
        (start_ms, end_ms, mode)).fetchall()
    return [dict(r) for r in rows]


def read_positions(conn, start_ms, end_ms, mode=MODE):
    """Positions OPENED in the window, plus positions CLOSED in the window.

    Two separate reads because they answer two different questions and sharing
    one number between them would merge "what did we put on today" with "what
    settled today". A position opened yesterday and resolved today belongs in
    today's P&L and in yesterday's entry count.
    """
    opened = [dict(r) for r in conn.execute(
        'SELECT * FROM positions WHERE opened_ts >= ? AND opened_ts < ? '
        'AND mode = ?', (start_ms, end_ms, mode)).fetchall()]
    closed = [dict(r) for r in conn.execute(
        'SELECT * FROM positions WHERE closed_ts IS NOT NULL '
        'AND closed_ts >= ? AND closed_ts < ? AND mode = ?',
        (start_ms, end_ms, mode)).fetchall()]
    return opened, closed


def read_fills(conn, start_ms, end_ms):
    row = conn.execute(
        'SELECT count(*) AS n, COALESCE(sum(fee), 0.0) AS fees FROM fills '
        'WHERE ts >= ? AND ts < ?', (start_ms, end_ms)).fetchone()
    return {'count': int(row['n']), 'fees': float(row['fees'])}


def read_equity(conn, start_ms, end_ms, mode=MODE):
    rows = conn.execute(
        'SELECT ts, equity, cash, open_risk FROM equity_snapshots '
        'WHERE ts >= ? AND ts < ? AND mode = ? ORDER BY ts',
        (start_ms, end_ms, mode)).fetchall()
    prior = conn.execute(
        'SELECT ts, equity FROM equity_snapshots WHERE ts < ? AND mode = ? '
        'ORDER BY ts DESC LIMIT 1', (start_ms, mode)).fetchone()
    if not rows:
        return {
            'status': 'NOT_TESTED',
            'note': 'no equity snapshot inside the window',
            'count': 0, 'start': None, 'end': None, 'min': None, 'max': None,
            'change': None, 'change_pct': None,
            'prior_close': (float(prior['equity']) if prior else None),
            'prior_close_ts': (int(prior['ts']) if prior else None),
        }
    values = [float(r['equity']) for r in rows]
    start, end = values[0], values[-1]
    change = end - start
    return {
        'status': 'OK',
        'note': None,
        'count': len(values),
        'start': start, 'end': end, 'min': min(values), 'max': max(values),
        'change': change,
        'change_pct': safe_div(change, start),
        'prior_close': (float(prior['equity']) if prior else None),
        'prior_close_ts': (int(prior['ts']) if prior else None),
        'first_ts': int(rows[0]['ts']), 'last_ts': int(rows[-1]['ts']),
        'max_open_risk': max(float(r['open_risk']) for r in rows),
    }


def read_events(conn, start_ms, end_ms):
    audit = [dict(r) for r in conn.execute(
        'SELECT ts, actor, event_type, payload_json FROM audit_log '
        'WHERE ts >= ? AND ts < ? ORDER BY ts', (start_ms, end_ms)).fetchall()]
    risk = [dict(r) for r in conn.execute(
        'SELECT ts, type, details_json FROM risk_events '
        'WHERE ts >= ? AND ts < ? ORDER BY ts', (start_ms, end_ms)).fetchall()]
    return audit, risk


def first_trade_ever(conn, mode=MODE):
    row = conn.execute(
        'SELECT min(opened_ts) AS first_ts, count(*) AS n FROM positions '
        'WHERE mode = ?', (mode,)).fetchone()
    if row is None or row['n'] == 0 or row['first_ts'] is None:
        return None
    return int(row['first_ts'])


def read_csv_rows(csv_path, start_s, end_s):
    """Cross-check source. A missing CSV is NOT_TESTED, never zero rows."""
    if not os.path.exists(csv_path):
        return {'status': 'MISSING',
                'note': 'decision CSV not found at {}'.format(csv_path),
                'rows': 0, 'actions': {}, 'reasons': {}, 'unparsable_ts': 0}
    actions = Counter()
    reasons = Counter()
    rows = 0
    unparsable = 0
    try:
        with open(csv_path, newline='') as handle:
            for row in csv.DictReader(handle):
                raw = (row.get('ts') or '').strip()
                try:
                    ts = int(float(raw))
                except (TypeError, ValueError):
                    unparsable += 1
                    continue
                if not (start_s <= ts < end_s):
                    continue
                rows += 1
                actions[(row.get('action') or 'UNKNOWN').strip()] += 1
                reasons[(row.get('reason') or '').strip()] += 1
    except OSError as exc:
        return {'status': 'UNREADABLE',
                'note': 'could not read {}: {}'.format(csv_path, exc),
                'rows': 0, 'actions': {}, 'reasons': {}, 'unparsable_ts': 0}
    return {'status': 'OK', 'note': None, 'rows': rows,
            'actions': dict(actions), 'reasons': dict(reasons),
            'unparsable_ts': unparsable,
            'unknown_actions': sorted(a for a in actions
                                      if a not in CSV_ACTIONS)}


def read_forge(start_ms, end_ms, proposals_dir=PROPOSALS_DIR,
               runs_path=FORGE_RUNS_PATH):
    """Proposals attributable to the window, by FILE MTIME.

    `forge_runs.jsonl` carries no timestamp field (verified against the file:
    the keys are candidates_screened, evidence_errors, gaps_used, refused,
    refused_by_category, written). So a run cannot be dated from its own
    record and mtime is the only evidence available. That is a weaker claim
    than a timestamp and the report says so rather than implying otherwise.
    """
    result = {'status': 'OK', 'note': None, 'proposals_in_window': [],
              'proposals_total': 0, 'runs_total': 0,
              'runs_file_mtime_in_window': False, 'runs_file_mtime': None,
              'attribution': 'file mtime (forge_runs.jsonl has no ts field)',
              'written_names': [], 'refused_by_category': {}}
    if not os.path.isdir(proposals_dir):
        result['status'] = 'MISSING'
        result['note'] = 'no proposals directory at {}'.format(proposals_dir)
        return result
    start_s, end_s = start_ms / 1000.0, end_ms / 1000.0
    for name in sorted(os.listdir(proposals_dir)):
        if not name.endswith('.md') or name == 'README.md':
            continue
        path = os.path.join(proposals_dir, name)
        result['proposals_total'] += 1
        mtime = os.path.getmtime(path)
        if start_s <= mtime < end_s:
            result['proposals_in_window'].append(
                {'file': name, 'mtime_ms': int(mtime * 1000)})
    if os.path.exists(runs_path):
        mtime = os.path.getmtime(runs_path)
        result['runs_file_mtime'] = int(mtime * 1000)
        result['runs_file_mtime_in_window'] = bool(start_s <= mtime < end_s)
        refused = Counter()
        written = []
        try:
            with open(runs_path) as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        record = json.loads(line)
                    except ValueError:
                        result['note'] = 'forge_runs.jsonl has an unparsable line'
                        continue
                    result['runs_total'] += 1
                    for key, count in (record.get('refused_by_category')
                                       or {}).items():
                        refused[key] += count
                    for item in record.get('written') or []:
                        written.append(item.get('name'))
        except OSError as exc:
            result['note'] = 'could not read forge_runs.jsonl: {}'.format(exc)
        result['refused_by_category'] = dict(refused)
        result['written_names'] = [n for n in written if n]
    else:
        result['note'] = 'no forge_runs.jsonl'
    return result


def configured_strategies():
    """The strategy list the CURRENT source would build.

    Convention 13: a long-running loop snapshotted its imports at start, so
    this can legitimately differ from what the log shows, and the difference is
    reported rather than silently reconciled. Import failure is NOT_TESTED.
    """
    try:
        import sys
        import warnings
        if REPO_ROOT not in sys.path:
            sys.path.insert(0, REPO_ROOT)
        with warnings.catch_warnings():
            # The import chain pulls in urllib3, which warns about LibreSSL on
            # this machine. That warning is not a fact about the strategy list
            # and it must not land in a message rendered for delivery.
            warnings.simplefilter('ignore')
            from strategies.polymarket import build_strategies
            names = sorted(getattr(s, 'strategy_name', str(s))
                           for s in build_strategies())
        return {'status': 'OK', 'names': names}
    except Exception as exc:
        return {'status': 'NOT_TESTED',
                'names': [],
                'note': 'could not import strategies.polymarket: {}: {}'
                        .format(type(exc).__name__, exc)}


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def aggregate_signals(signals):
    """Roll signal rows into totals and a per-strategy view.

    The accounting identity `evaluations == entries + skips` is asserted here,
    both overall and per strategy, because it is the shadow loop's own
    invariant and a report that quietly disagrees with it would be the first
    place the violation went unnoticed.
    """
    per = {}
    total_entries = 0
    total_skips = 0
    reasons = Counter()
    for row in signals:
        name = row['strategy_id']
        bucket = per.setdefault(name, {
            'evaluations': 0, 'entries': 0, 'skips': 0,
            'reasons': Counter()})
        bucket['evaluations'] += 1
        if row['acted']:
            bucket['entries'] += 1
            total_entries += 1
        else:
            bucket['skips'] += 1
            total_skips += 1
            reason = row['skip_reason'] or 'unspecified'
            bucket['reasons'][reason] += 1
            reasons[reason] += 1

    for name, bucket in per.items():
        assert bucket['evaluations'] == bucket['entries'] + bucket['skips'], (
            'per-strategy identity violated for {}: {}'.format(name, bucket))
        cats, _ = skip_category_counts(bucket['reasons'])
        bucket['categories'] = dict(cats)

    evaluations = len(signals)
    assert evaluations == total_entries + total_skips, (
        'signal identity violated: evaluations={} entries={} skips={}'
        .format(evaluations, total_entries, total_skips))

    categories, unclassified = skip_category_counts(reasons)
    return {
        'evaluations': evaluations,
        'entries': total_entries,
        'skips': total_skips,
        'identity_ok': evaluations == total_entries + total_skips,
        'reasons': dict(reasons),
        'categories': dict(categories),
        'unclassified_reasons': dict(unclassified),
        'per_strategy': per,
        'strategies_seen': sorted(per),
    }


def aggregate_trades(closed_positions):
    """Wins, losses, flats and P&L over CLOSED positions.

    A flat trade (net exactly zero) is its own count. It is not a win, and
    folding it into losses would make a break-even look like a loss.
    `pnl_net IS NULL` on a closed row is a bookkeeping fault, counted as
    `unpriced` and kept OUT of every P&L number rather than treated as zero.
    """
    wins = losses = flats = unpriced = 0
    gross_profit = 0.0
    gross_loss = 0.0
    per = {}
    for row in closed_positions:
        name = row.get('strategy_id') or 'unknown'
        bucket = per.setdefault(name, {
            'closed': 0, 'wins': 0, 'losses': 0, 'flats': 0, 'unpriced': 0,
            'pnl': 0.0, 'gross_profit': 0.0, 'gross_loss': 0.0})
        bucket['closed'] += 1
        pnl = row.get('pnl_net')
        if pnl is None or not math.isfinite(float(pnl)):
            unpriced += 1
            bucket['unpriced'] += 1
            continue
        pnl = float(pnl)
        bucket['pnl'] += pnl
        if pnl > 0:
            wins += 1
            bucket['wins'] += 1
            gross_profit += pnl
            bucket['gross_profit'] += pnl
        elif pnl < 0:
            losses += 1
            bucket['losses'] += 1
            gross_loss += -pnl
            bucket['gross_loss'] += -pnl
        else:
            flats += 1
            bucket['flats'] += 1

    decided = wins + losses
    total_priced = decided + flats
    assert len(closed_positions) == total_priced + unpriced, (
        'trade accounting lost rows: closed={} priced={} unpriced={}'
        .format(len(closed_positions), total_priced, unpriced))

    for bucket in per.values():
        bucket['win_rate'] = win_rate(bucket['wins'], bucket['losses'])
        bucket['profit_factor'] = profit_factor(bucket['gross_profit'],
                                                bucket['gross_loss'])
    return {
        'closed': len(closed_positions),
        'wins': wins, 'losses': losses, 'flats': flats, 'unpriced': unpriced,
        'decided': decided,
        'pnl': gross_profit - gross_loss,
        'gross_profit': gross_profit,
        'gross_loss': gross_loss,
        'win_rate': win_rate(wins, losses),
        'profit_factor': profit_factor(gross_profit, gross_loss),
        'avg_pnl': safe_div(gross_profit - gross_loss, total_priced),
        'per_strategy': per,
    }


def open_exposure(open_positions):
    """Premium sitting in positions that have not resolved.

    On a binary the premium plus fee IS the maximum loss, exactly, so this is
    a measurement and not a stop-distance estimate.
    """
    at_risk = 0.0
    for row in open_positions:
        qty = float(row.get('qty') or 0.0)
        entry = float(row.get('entry_px') or 0.0)
        fees = float(row.get('fees') or 0.0)
        at_risk += qty * entry + fees
    return {'count': len(open_positions), 'premium_at_risk': at_risk}


def summarise_events(audit_rows, risk_rows):
    """Pull the events an operator has to see out of the audit stream."""
    by_type = Counter(r['event_type'] for r in audit_rows)
    notable = []
    health = {}
    identity_violations = 0
    last_stats = None
    for row in audit_rows:
        event = row['event_type']
        if event in ('halt', 'resume', 'accounting_violation',
                     'shadow_start', 'shadow_stop'):
            notable.append({'ts_ms': int(row['ts']), 'event': event})
        if event == 'shadow_stats':
            try:
                last_stats = json.loads(row['payload_json'])
            except ValueError:
                pass
    if last_stats:
        health = dict(last_stats.get('health') or {})
        identity_violations = int(last_stats.get('identity_violations') or 0)
    for row in risk_rows:
        notable.append({'ts_ms': int(row['ts']),
                        'event': 'risk_event:' + row['type']})
    notable.sort(key=lambda item: item['ts_ms'])
    return {
        'audit_counts': dict(by_type),
        'notable': notable,
        'loop_health': health,
        'loop_identity_violations': identity_violations,
        'halts': by_type.get('halt', 0),
        'resumes': by_type.get('resume', 0),
        'accounting_violations': by_type.get('accounting_violation', 0),
        'sessions_started': by_type.get('shadow_start', 0),
        'sessions_stopped': by_type.get('shadow_stop', 0),
    }


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def rule(title, width=64):
    return '{}\n{}'.format(title, '-' * min(width, max(len(title), 12)))


def render_skip_table(reasons, limit=12, indent='  '):
    lines = []
    total = sum(reasons.values())
    for reason, count in sorted(reasons.items(),
                                key=lambda kv: (-kv[1], kv[0]))[:limit]:
        category = classify_reason(reason)
        lines.append('{}{:>6}  {:<34} {:>6}  {}'.format(
            indent, count, reason[:34], fmt_pct_of(count, total), category))
    if not lines:
        lines.append(indent + 'none')
    return lines


def render_categories(categories, unclassified, indent='  '):
    lines = []
    total = sum(categories.values())
    for name in CATEGORY_ORDER:
        count = categories.get(name, 0)
        if count == 0 and name == UNCLASSIFIED:
            continue
        lines.append('{}{:<14} {:>7}  {:>6}   {}'.format(
            indent, name, count, fmt_pct_of(count, total),
            CATEGORY_NOTE[name]))
    if unclassified:
        lines.append(indent + 'UNCLASSIFIED reasons needing a rule:')
        for reason, count in sorted(unclassified.items(),
                                    key=lambda kv: (-kv[1], kv[0])):
            lines.append('{}  {:>6}  {}'.format(indent, count, reason))
    return lines


def render_equity(equity, indent='  '):
    lines = []
    if equity['status'] != 'OK':
        lines.append('{}NOT_TESTED: {}'.format(indent, equity['note']))
        if equity.get('prior_close') is not None:
            lines.append('{}last snapshot before window: {} at {}'.format(
                indent, fmt_usd(equity['prior_close']),
                ms_to_et_string(equity['prior_close_ts'])))
        return lines
    lines.append('{}start   {}'.format(indent, fmt_usd(equity['start'])))
    lines.append('{}end     {}'.format(indent, fmt_usd(equity['end'])))
    lines.append('{}min     {}'.format(indent, fmt_usd(equity['min'])))
    lines.append('{}max     {}'.format(indent, fmt_usd(equity['max'])))
    change_pct = ('n/a' if equity['change_pct'] is None
                  else '{:+.2f}%'.format(equity['change_pct'] * 100.0))
    lines.append('{}change  {} ({})'.format(
        indent, fmt_usd(equity['change']), change_pct))
    lines.append('{}snapshots {}, max open risk {}'.format(
        indent, equity['count'], fmt_usd(equity.get('max_open_risk'))))
    return lines


def dump_json(payload):
    """Convention 19: a non-finite must fail at write time, not at read time."""
    return json.dumps(payload, indent=2, sort_keys=True, allow_nan=False,
                      default=str)


def write_summary_file(text, filename, summary_dir=None):
    """`--send` lands here.

    A script cannot call an MCP tool: MCP tools are exposed to the agent, not
    to a subprocess, and there is no local HTTP endpoint for `messages_send`.
    So `--send` renders to a file and prints the path. Cron or Raven picks the
    file up and delivers it. This is a drop box, not a send, and it is named
    honestly in both scripts' docstrings.
    """
    # Resolved at CALL time, not bound as a default at import time, so a
    # caller (or a test) that overrides SUMMARY_DIR is actually obeyed.
    summary_dir = summary_dir or SUMMARY_DIR
    os.makedirs(summary_dir, exist_ok=True)
    path = os.path.join(summary_dir, filename)
    with open(path, 'w') as handle:
        handle.write(text if text.endswith('\n') else text + '\n')
    return path


def error_report(kind, target, message, as_json=False):
    """The ONLY output produced when a source could not be read.

    It says UNREADABLE and it reports no counts. A zero here would turn an
    outage into a flat day, which is the specific misreading convention 11 and
    D-255 exist to prevent.
    """
    if as_json:
        return dump_json({
            'status': 'ERROR',
            'report': kind,
            'target': target,
            'error': message,
            'note': 'source unreadable: NOT_TESTED, not zero. No counts are '
                    'reported because none were read.',
        })
    return '\n'.join([
        'SHADOW {} {} - ERROR'.format(kind.upper(), target),
        '',
        'STATUS: UNREADABLE / NOT_TESTED',
        '  {}'.format(message),
        '',
        'No counts are reported. An unreadable source is not an empty one',
        '(convention 11). Do not read this as a day with zero activity.',
    ])
