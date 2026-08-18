"""Read live Polymarket shadow-trading results and turn them into Forge gaps.

The graveyard tells Forge what happened in a BACKTEST. This module tells it what
happened in the SHADOW LOOP: which strategies actually fired against a live
book, which never fired, and - the part that matters most - WHY they did not.

The central distinction, and the reason this module exists rather than a
`SELECT count(*) GROUP BY strategy`:

  DATA_BLOCKER  the strategy could never have fired because an input it needs
                was absent. `no_spot_or_strike` is not "the condition was not
                met", it is "the condition was never evaluated". Convention 11:
                that strategy is NOT_TESTED. It did not look and decline.
  GENUINE       the strategy had every input, evaluated its condition, and the
                condition was false. That IS a measurement, though a thin one.
  SIM_LIMIT     the strategy DECIDED to act and the paper adapter could not
                model the fill (a maker quote against a taker-only simulator).
                Also NOT_TESTED, and for a reason that is ours, not the
                market's.
  UNKNOWN       a reason string this module has never seen. Convention 20: it
                is counted and surfaced, never folded into one of the three
                above, because a silently reclassified skip is a missing number.

Conventions enforced here rather than trusted to a reader:
  11. an unreadable DB is NOT an empty DB. `evaluate()` returns
      status='unreadable' with the exception text; it never returns a clean
      zero that would read as "the strategies looked and declined"
  19. every number that leaves here is finite, so the caller can
      json.dump(allow_nan=False) it
  20. every decision row lands in exactly one bucket, and the accounting
      identity is ASSERTED: entries + skips + other == total rows, and
      sum(skip class counts) == skips

This module is READ ONLY. It opens the DB with mode=ro because the shadow loop
may be writing to it right now.
"""
import collections
import csv
import json
import math
import os
import sqlite3
from typing import Any, Dict, List, Optional, Tuple

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_DB = os.path.join(ROOT, 'db', 'trading.db')
DEFAULT_PAPER_LOG = os.path.join(
    ROOT, 'research', 'polymarket_paper', 'polymarket_paper_log.csv')

# A strategy with three evaluations that never fired has told us nothing. This
# is the floor below which "never fired" is not yet a gap, only a small sample.
# Convention 17: a hardcoded threshold is an assumption with an expiry date.
# The expiry here is "when a shadow session routinely runs thousands of cycles",
# at which point 30 is far too generous and should rise.
MIN_EVALUATIONS_FOR_GAP = 30

# Above this share of a strategy's skips being data-blocked, the strategy is
# NOT_TESTED rather than tested-and-silent. Set at a bare majority on purpose:
# if most of the time the inputs were not there, the minority of evaluated
# cycles is not a sample anyone should reason from.
DATA_BLOCKED_FRACTION = 0.5

DATA_BLOCKER = 'DATA_BLOCKER'
GENUINE = 'GENUINE'
SIM_LIMIT = 'SIM_LIMIT'
UNKNOWN = 'UNKNOWN'

# Every skip reason emitted by strategies/polymarket/*.py, classified once,
# here, with the input that is missing named for the blockers. Sourced by
# grepping `decide('SKIP', '...')` across strategies/polymarket/ on 2026-08-17.
#
# The rule for deciding a row: does the reason name a MISSING INPUT or a FALSE
# CONDITION? `no_atr` is missing input. `lead_below_zone` is a false condition
# computed FROM an input that was present. When a reason could be read either
# way, it goes to DATA_BLOCKER, because over-reporting NOT_TESTED costs a
# re-test and under-reporting it puts a fabricated verdict in the record.
SKIP_CLASSIFICATION: Dict[str, Tuple[str, str]] = {
    # --- inputs that were absent -------------------------------------------
    'no_spot_or_strike': (DATA_BLOCKER, 'window strike (Chainlink 60s TWAP, '
                                        'not published by Gamma) and/or spot'),
    'no_lead_or_atr': (DATA_BLOCKER, 'lead_bps (needs the strike) and/or '
                                     'atr14'),
    'no_atr': (DATA_BLOCKER, 'atr14'),
    'no_spot': (DATA_BLOCKER, 'spot price'),
    'no_market': (DATA_BLOCKER, 'resolved market'),
    'no_orderbook': (DATA_BLOCKER, 'CLOB orderbook'),
    'no_asks': (DATA_BLOCKER, 'ask side of the book'),
    'no_bids_to_join': (DATA_BLOCKER, 'bid side of the book'),
    'no_magnitude_data': (DATA_BLOCKER, 'move magnitude series'),
    'no_window_clock': (DATA_BLOCKER, 'window clock (seconds_remaining)'),
    'no_window_open': (DATA_BLOCKER, 'window open price'),
    'invalid_window_open': (DATA_BLOCKER, 'usable window open price'),
    'invalid_strike': (DATA_BLOCKER, 'usable strike'),
    'missing_market_leg': (DATA_BLOCKER, 'the 5m or the 15m market leg'),
    'insufficient_window_history': (DATA_BLOCKER, 'prior windows (warmup)'),
    'unreadable_window_direction': (DATA_BLOCKER, 'per-window direction'),
    'zero_atr_undefined_ratio': (DATA_BLOCKER, 'non-zero atr14'),
    'zero_atr_undefined_stretch': (DATA_BLOCKER, 'non-zero atr14'),
    'insufficient_book_depth': (DATA_BLOCKER, 'book depth'),
    'insufficient_ask_depth': (DATA_BLOCKER, 'ask depth'),
    'insufficient_depth_for_pair': (DATA_BLOCKER, 'book depth on both legs'),
    'degenerate_quote': (DATA_BLOCKER, 'a well-formed two-sided quote'),

    # --- the simulator, not the market -------------------------------------
    'maker_fill_not_simulated': (SIM_LIMIT, 'the paper adapter models taker '
                                            'fills only; this is a QUOTE'),
    'symmetric_disabled': (SIM_LIMIT, 'a config switch, not a market state'),

    # --- conditions that were evaluated and were false ----------------------
    'no_streak': (GENUINE, ''),
    'not_stretched': (GENUINE, ''),
    'not_through_strike': (GENUINE, ''),
    'not_a_coin_flip': (GENUINE, ''),
    'no_reversal_yet': (GENUINE, ''),
    'lead_below_zone': (GENUINE, ''),
    'lead_above_zone': (GENUINE, ''),
    'lead_inside_noise': (GENUINE, ''),
    'book_too_tight_to_arm': (GENUINE, ''),
    'book_not_wide_enough': (GENUINE, ''),
    'ask_above_band': (GENUINE, ''),
    'ask_above_cap': (GENUINE, ''),
    'effective_ask_above_band': (GENUINE, ''),
    'effective_ask_below_band': (GENUINE, ''),
    'effective_ask_above_cap': (GENUINE, ''),
    'edge_below_threshold': (GENUINE, ''),
    'edge_threshold_exceeds_fair_value': (GENUINE, ''),
    'fair_value_outside_tradeable_band': (GENUINE, ''),
    'pair_cost_above_cap': (GENUINE, ''),
    'pair_cost_above_binned_fair': (GENUINE, ''),
    'pair_cost_above_edge_threshold': (GENUINE, ''),
    'pair_unfillable_at_caps': (GENUINE, ''),
    'no_profitable_completion': (GENUINE, ''),
    'completion_ask_above_cap': (GENUINE, ''),
    'unfillable_at_cap': (GENUINE, ''),
    'unfillable_at_band_high': (GENUINE, ''),
    'unsizable_at_notional_cap': (GENUINE, ''),
    'past_quote_window': (GENUINE, ''),
    'late_in_window': (GENUINE, ''),
    'too_late_in_window': (GENUINE, ''),
    'too_close_to_resolution': (GENUINE, ''),
    'out_of_time_band': (GENUINE, ''),
    'window_not_open': (GENUINE, ''),
    'already_entered_this_window': (GENUINE, ''),
    'max_trades_this_window': (GENUINE, ''),
    'pair_complete': (GENUINE, ''),
    'unpaired_leg_held_to_resolution': (GENUINE, ''),
}


def classify_skip_reason(reason: Optional[str]) -> Tuple[str, str]:
    """Return (class, missing_input) for one skip reason.

    An unrecognised reason returns UNKNOWN, never a guess. A guess here would
    silently move a NOT_TESTED strategy into the "ran and found nothing" pile,
    which is the exact error convention 11 exists to prevent.
    """
    if reason is None or reason == '':
        return UNKNOWN, 'skip with no reason recorded'
    hit = SKIP_CLASSIFICATION.get(reason)
    if hit is None:
        # `fair_value_*` is a family emitted with a suffix. Match the prefix
        # rather than guessing, and only for prefixes we have actually seen.
        if reason.startswith('fair_value_'):
            return GENUINE, ''
        return UNKNOWN, f'reason {reason!r} is not in SKIP_CLASSIFICATION'
    return hit


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

class ShadowUnreadable(Exception):
    """The evidence could not be read. Not the same as there being none."""


def _connect_ro(path: str) -> sqlite3.Connection:
    """Open the DB read-only. The shadow loop may be writing to it right now."""
    if not os.path.exists(path):
        raise ShadowUnreadable(f'no such database: {path}')
    try:
        conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        # Force a real read so a corrupt or locked file fails HERE, with a
        # message, rather than three functions later as an empty result set.
        conn.execute('select count(*) from sqlite_master').fetchone()
        return conn
    except sqlite3.Error as exc:
        raise ShadowUnreadable(f'{type(exc).__name__}: {exc}') from exc


def _table_names(conn: sqlite3.Connection) -> List[str]:
    return [r[0] for r in conn.execute(
        "select name from sqlite_master where type='table'")]


def _require_tables(conn: sqlite3.Connection, needed: Tuple[str, ...]) -> None:
    present = set(_table_names(conn))
    missing = [t for t in needed if t not in present]
    if missing:
        raise ShadowUnreadable(
            'schema is missing table(s): ' + ','.join(missing))


def _finite(value: Any) -> Optional[float]:
    """Coerce to a finite float or None. Convention 19: nothing non-finite
    leaves this module, because json.dump(allow_nan=False) downstream would
    raise on it and the caller would lose the whole record over one NaN."""
    if value is None:
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def read_decisions(conn: sqlite3.Connection,
                   mode: Optional[str] = None) -> List[Dict[str, Any]]:
    """Every signal/decision row. `acted=1` is an entry, `acted=0` is a skip."""
    _require_tables(conn, ('signals',))
    sql = ('select ts, pair, tf, strategy_id, pattern, direction, confidence, '
           'acted, skip_reason, mode from signals')
    params: Tuple[Any, ...] = ()
    if mode:
        sql += ' where mode = ?'
        params = (mode,)
    return [dict(r) for r in conn.execute(sql, params)]


def read_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    _require_tables(conn, ('positions',))
    return [dict(r) for r in conn.execute('select * from positions')]


def read_equity(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    _require_tables(conn, ('equity_snapshots',))
    return [dict(r) for r in conn.execute(
        'select ts, equity, cash, open_risk, mode from equity_snapshots '
        'order by ts')]


def read_paper_log(path: str) -> Dict[str, Any]:
    """The CSV the shadow loop writes alongside the DB.

    It carries columns the `signals` table does not: `resolution`, `won`,
    `pnl_usdc`, `position_id`. Those are the only place a RESOLVED binary shows
    up, so the CSV is not redundant with the DB even though the decision rows
    overlap. A missing CSV is reported, not swallowed.
    """
    if not os.path.exists(path):
        return {'status': 'absent', 'path': path, 'error': 'no such file'}
    try:
        with open(path, newline='') as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, csv.Error) as exc:
        return {'status': 'unreadable', 'path': path,
                'error': f'{type(exc).__name__}: {exc}'}

    actions = collections.Counter(r.get('action') or '' for r in rows)
    resolved = [r for r in rows if (r.get('resolution') or '').strip()]
    won = collections.Counter(
        (r.get('won') or '').strip() for r in resolved)
    pnl_values = [_finite(r.get('pnl_usdc')) for r in rows]
    pnl_values = [v for v in pnl_values if v is not None]
    return {
        'status': 'ok',
        'path': os.path.relpath(path, ROOT),
        'n_rows': len(rows),
        'actions': dict(actions),
        'n_resolved': len(resolved),
        'won_counts': dict(won),
        'n_rows_with_pnl': len(pnl_values),
        'pnl_usdc_total': round(sum(pnl_values), 6) if pnl_values else 0.0,
    }


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def summarise_positions(positions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Closed and open positions, per strategy. Wins, losses, PnL."""
    closed = [p for p in positions if p.get('closed_ts') is not None]
    open_ = [p for p in positions if p.get('closed_ts') is None]

    by_strategy: Dict[str, Dict[str, Any]] = {}
    wins = losses = flats = unknown_pnl = 0
    total_net = 0.0
    for pos in closed:
        sid = str(pos.get('strategy_id'))
        agg = by_strategy.setdefault(sid, {
            'n_closed': 0, 'wins': 0, 'losses': 0, 'flats': 0,
            'pnl_net_total': 0.0, 'n_pnl_missing': 0,
        })
        agg['n_closed'] += 1
        pnl = _finite(pos.get('pnl_net'))
        if pnl is None:
            pnl = _finite(pos.get('pnl_gross'))
        if pnl is None:
            # A closed position with no PnL is not a flat trade. It is an
            # unreadable one (convention 11), so it gets its own counter.
            agg['n_pnl_missing'] += 1
            unknown_pnl += 1
            continue
        agg['pnl_net_total'] = round(agg['pnl_net_total'] + pnl, 8)
        total_net = round(total_net + pnl, 8)
        if pnl > 0:
            agg['wins'] += 1
            wins += 1
        elif pnl < 0:
            agg['losses'] += 1
            losses += 1
        else:
            agg['flats'] += 1
            flats += 1

    assert wins + losses + flats + unknown_pnl == len(closed), (
        'position accounting identity broken: '
        f'{wins}+{losses}+{flats}+{unknown_pnl} != {len(closed)} closed')

    return {
        'n_positions': len(positions),
        'n_closed': len(closed),
        'n_open': len(open_),
        'wins': wins,
        'losses': losses,
        'flats': flats,
        'n_closed_with_unreadable_pnl': unknown_pnl,
        'pnl_net_total': total_net,
        'by_strategy': by_strategy,
    }


def summarise_decisions(decisions: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-strategy firing behaviour and skip-reason breakdown.

    Convention 20 is the whole design of this function. Every row lands in
    exactly one of entries/skips/malformed, every skip lands in exactly one
    class, and both identities are asserted rather than assumed.
    """
    per: Dict[str, Dict[str, Any]] = {}
    entries = skips = malformed = 0
    unknown_reasons: Dict[str, int] = collections.Counter()

    for row in decisions:
        sid = str(row.get('strategy_id'))
        agg = per.setdefault(sid, {
            'n_evaluations': 0,
            'n_entries': 0,
            'n_skips': 0,
            'n_malformed': 0,
            'skip_reasons': collections.Counter(),
            'skip_classes': collections.Counter(),
            'first_ts': None,
            'last_ts': None,
            'markets': set(),
        })
        agg['n_evaluations'] += 1
        ts = row.get('ts')
        if isinstance(ts, (int, float)) and math.isfinite(float(ts)):
            agg['first_ts'] = ts if agg['first_ts'] is None \
                else min(agg['first_ts'], ts)
            agg['last_ts'] = ts if agg['last_ts'] is None \
                else max(agg['last_ts'], ts)
        if row.get('pair'):
            agg['markets'].add(str(row['pair']))

        acted = row.get('acted')
        if acted == 1:
            agg['n_entries'] += 1
            entries += 1
        elif acted == 0:
            agg['n_skips'] += 1
            skips += 1
            reason = row.get('skip_reason')
            key = reason if reason else '<null_skip_reason>'
            agg['skip_reasons'][key] += 1
            cls, _ = classify_skip_reason(reason)
            agg['skip_classes'][cls] += 1
            if cls == UNKNOWN:
                unknown_reasons[key] += 1
        else:
            # Neither acted nor skipped. Convention 20: this is a bucket, not
            # a `continue`.
            agg['n_malformed'] += 1
            malformed += 1

    assert entries + skips + malformed == len(decisions), (
        'decision accounting identity broken: '
        f'{entries}+{skips}+{malformed} != {len(decisions)} rows')

    out: Dict[str, Dict[str, Any]] = {}
    for sid, agg in per.items():
        assert sum(agg['skip_classes'].values()) == agg['n_skips'], (
            f'skip-class identity broken for {sid}: '
            f"{sum(agg['skip_classes'].values())} != {agg['n_skips']}")
        blocked = (agg['skip_classes'].get(DATA_BLOCKER, 0)
                   + agg['skip_classes'].get(SIM_LIMIT, 0))
        blocked_fraction = (blocked / agg['n_skips']) if agg['n_skips'] else 0.0
        dominant = agg['skip_reasons'].most_common(1)
        out[sid] = {
            'n_evaluations': agg['n_evaluations'],
            'n_entries': agg['n_entries'],
            'n_skips': agg['n_skips'],
            'n_malformed': agg['n_malformed'],
            'entry_rate': round(agg['n_entries'] / agg['n_evaluations'], 6)
                          if agg['n_evaluations'] else 0.0,
            'skip_reasons': dict(agg['skip_reasons']),
            'skip_classes': dict(agg['skip_classes']),
            'blocked_fraction': round(blocked_fraction, 6),
            'dominant_skip_reason': dominant[0][0] if dominant else None,
            'dominant_skip_count': dominant[0][1] if dominant else 0,
            'first_ts': agg['first_ts'],
            'last_ts': agg['last_ts'],
            'n_markets': len(agg['markets']),
        }

    return {
        'n_rows': len(decisions),
        'n_entries': entries,
        'n_skips': skips,
        'n_malformed': malformed,
        'n_strategies': len(out),
        'by_strategy': out,
        'unknown_skip_reasons': dict(unknown_reasons),
    }


def summarise_equity(snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Overall paper performance from the equity curve."""
    if not snapshots:
        return {'n_snapshots': 0, 'status': 'no_snapshots'}
    eq = [(s['ts'], _finite(s.get('equity'))) for s in snapshots]
    eq = [(ts, v) for ts, v in eq if v is not None]
    if not eq:
        return {'n_snapshots': len(snapshots),
                'status': 'no_finite_equity_values'}
    first_ts, first = eq[0]
    last_ts, last = eq[-1]
    peak = eq[0][1]
    max_dd = 0.0
    for _, v in eq:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak)
    return {
        'status': 'ok',
        'n_snapshots': len(eq),
        'first_ts': first_ts,
        'last_ts': last_ts,
        'equity_first': first,
        'equity_last': last,
        'equity_peak': peak,
        'return_pct': round(((last - first) / first) * 100.0, 6)
                      if first else None,
        'max_drawdown_pct': round(max_dd * 100.0, 6),
        'open_risk_last': _finite(snapshots[-1].get('open_risk')),
    }


# ---------------------------------------------------------------------------
# Gaps
# ---------------------------------------------------------------------------

def derive_gaps(decision_summary: Dict[str, Any],
                position_summary: Dict[str, Any]) -> Dict[str, Any]:
    """The part Forge proposes against.

    Splits the never-fired strategies into NOT_TESTED (an input was missing, so
    the strategy never got to decide) and RAN_NO_ENTRY (every input present,
    condition false). Only the second is a measurement, and even then a thin
    one; convention 7 cuts both ways.
    """
    not_tested: List[Dict[str, Any]] = []
    ran_no_entry: List[Dict[str, Any]] = []
    fired: List[Dict[str, Any]] = []
    underpowered: List[Dict[str, Any]] = []

    for sid, rec in sorted(decision_summary.get('by_strategy', {}).items()):
        row = {
            'strategy': sid,
            'n_evaluations': rec['n_evaluations'],
            'n_entries': rec['n_entries'],
            'blocked_fraction': rec['blocked_fraction'],
            'dominant_skip_reason': rec['dominant_skip_reason'],
            'dominant_skip_count': rec['dominant_skip_count'],
            'skip_classes': rec['skip_classes'],
        }
        if rec['n_entries'] > 0:
            fired.append(row)
            continue
        if rec['n_evaluations'] < MIN_EVALUATIONS_FOR_GAP:
            # Too few looks to call anything. Not a gap, not a verdict.
            row['verdict'] = 'UNDERPOWERED'
            row['note'] = (f"{rec['n_evaluations']} evaluations is under the "
                           f'{MIN_EVALUATIONS_FOR_GAP} floor')
            underpowered.append(row)
            continue
        if rec['blocked_fraction'] >= DATA_BLOCKED_FRACTION:
            reason = rec['dominant_skip_reason']
            _, missing = classify_skip_reason(reason)
            row['verdict'] = 'NOT_TESTED'
            row['missing_input'] = missing
            row['note'] = (
                f"{rec['blocked_fraction']:.1%} of skips were data-blocked. "
                'This strategy did not look and decline; it never got to '
                'look. Convention 11.')
            not_tested.append(row)
        else:
            row['verdict'] = 'RAN_NO_ENTRY'
            row['note'] = (
                'inputs were present and the entry condition was false on '
                f"{rec['n_skips'] if 'n_skips' in rec else rec['n_evaluations']}"
                ' evaluations. A measurement, and a thin one.')
            ran_no_entry.append(row)

    # Dominant skip reasons across the whole session, with their class.
    reason_totals: collections.Counter = collections.Counter()
    for rec in decision_summary.get('by_strategy', {}).values():
        for reason, n in rec['skip_reasons'].items():
            reason_totals[reason] += n
    dominant = []
    for reason, n in reason_totals.most_common():
        cls, missing = classify_skip_reason(
            None if reason == '<null_skip_reason>' else reason)
        dominant.append({
            'reason': reason,
            'count': n,
            'class': cls,
            'missing_input': missing,
            'share_of_skips': round(n / decision_summary['n_skips'], 6)
                              if decision_summary.get('n_skips') else 0.0,
        })

    total = decision_summary.get('n_strategies', 0)
    bucketed = (len(not_tested) + len(ran_no_entry) + len(fired)
                + len(underpowered))
    assert bucketed == total, (
        f'gap accounting identity broken: {bucketed} bucketed != '
        f'{total} strategies')

    return {
        'strategies_not_tested': not_tested,
        'strategies_ran_no_entry': ran_no_entry,
        'strategies_fired': fired,
        'strategies_underpowered': underpowered,
        'dominant_skip_reasons': dominant,
        'unknown_skip_reasons': decision_summary.get(
            'unknown_skip_reasons', {}),
        'n_closed_positions': position_summary.get('n_closed', 0),
        'zero_entry_session': decision_summary.get('n_entries', 0) == 0,
        'min_evaluations_for_gap': MIN_EVALUATIONS_FOR_GAP,
        'data_blocked_fraction_threshold': DATA_BLOCKED_FRACTION,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def evaluate(db_path: str = DEFAULT_DB,
             paper_log_path: str = DEFAULT_PAPER_LOG,
             mode: Optional[str] = None) -> Dict[str, Any]:
    """Full shadow evaluation.

    Returns a dict with `status` of 'ok' or 'unreadable'. NEVER an empty 'ok':
    an unreadable DB is not an empty one (convention 11), and a caller that
    read a silent `{}` as "no strategy fired" would be recording a verdict the
    evidence does not support.
    """
    try:
        conn = _connect_ro(db_path)
    except ShadowUnreadable as exc:
        return {
            'status': 'unreadable',
            'db_path': db_path,
            'error': str(exc),
            'note': 'NOT_TESTED, not empty. Convention 11.',
        }

    try:
        decisions = read_decisions(conn, mode=mode)
        positions = read_positions(conn)
        equity = read_equity(conn)
        tables = sorted(_table_names(conn))
    except (ShadowUnreadable, sqlite3.Error) as exc:
        return {
            'status': 'unreadable',
            'db_path': db_path,
            'error': f'{type(exc).__name__}: {exc}',
            'note': 'NOT_TESTED, not empty. Convention 11.',
        }
    finally:
        conn.close()

    decision_summary = summarise_decisions(decisions)
    position_summary = summarise_positions(positions)
    equity_summary = summarise_equity(equity)
    paper_log = read_paper_log(paper_log_path)

    return {
        'status': 'ok',
        'db_path': os.path.relpath(db_path, ROOT)
                   if db_path.startswith(ROOT) else db_path,
        'mode_filter': mode,
        'tables': tables,
        'decisions': decision_summary,
        'positions': position_summary,
        'equity': equity_summary,
        'paper_log': paper_log,
        'gaps': derive_gaps(decision_summary, position_summary),
    }


# ---------------------------------------------------------------------------
# Turning the evaluation into Forge candidates
# ---------------------------------------------------------------------------

def shadow_candidates(evaluation: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build proposal candidates from what the shadow loop actually showed.

    One `repair` per NOT_TESTED strategy: the input it needs is missing, which
    is a fixable engineering gap and not an edge question. `expected_edge_bps`
    is null for all of them, because the edge of a strategy that has never
    evaluated its own condition is not knowable (convention 11).

    Nothing is generated for a RAN_NO_ENTRY strategy: "the condition was false
    297 times" is a measurement Forge should report, not act on, and acting on
    it would be discovering a condition by scanning (convention 4).
    """
    if evaluation.get('status') != 'ok':
        return []
    gaps = evaluation.get('gaps', {})
    out: List[Dict[str, Any]] = []

    for row in gaps.get('strategies_not_tested', []):
        sid = row['strategy']
        slug = sid.lower().lstrip('_')
        if slug.startswith('pm_'):
            slug = slug[3:]
        reason = row.get('dominant_skip_reason')
        missing = row.get('missing_input') or 'an unnamed input'
        n_eval = row['n_evaluations']
        out.append({
            'name': f'shadow_unblock_{slug}',
            'kind': 'repair',
            'asset_class': 'PREDICTION_MARKET',
            'source': ('agents/forge_shadow_eval.py over db/trading.db '
                       '(measured, live shadow session)'),
            'thesis': (
                f'{sid} has never evaluated its own entry condition in the '
                f'shadow loop: it skipped {row["dominant_skip_count"]} of '
                f'{n_eval} evaluations on {reason!r}, which is a missing '
                f'input ({missing}) rather than a false condition. Supplying '
                'that input is what turns this strategy from NOT_TESTED into '
                'testable.'),
            'expected_edge_bps': None,
            'kill_condition': (
                f'After {missing} is supplied, if {sid} still enters on fewer '
                f'than 1% of evaluations over 500 or more shadow cycles as '
                'measured by agents/forge_shadow_eval.py against db/trading.db, '
                'and scores no better than 0 net cents per share over 200 or '
                'more resolved positions in backtest/polymarket_harness.py, it '
                'is retired rather than repaired a second time.'),
            'entry_exit_rules': (
                'Unchanged. This is a repair to the CONTEXT the strategy is '
                f'handed, not to its logic: {missing} must be present and '
                'correct before any entry rule of this strategy has been '
                'exercised even once.'),
            'data_requirements': (
                f'BLOCKER: {missing}. Measured over {n_eval} live shadow '
                f'evaluations, {row["blocked_fraction"]:.1%} of skips were '
                'data-blocked. Until that input exists this strategy is '
                'NOT_TESTED (convention 11) and must not be reported as having '
                'looked and declined.'),
            'related_graveyard_findings': (
                'None. PREDICTION_MARKET has no graveyard rows at all, so this '
                'proposal rests on live shadow measurement rather than on a '
                'buried family. D-268: every Polymarket strategy is NOT_TESTED '
                'until backtest/polymarket_harness.py scores it.'),
            'body': _render_repair_body(row, evaluation),
        })
    return out


def _render_repair_body(row: Dict[str, Any],
                        evaluation: Dict[str, Any]) -> str:
    sid = row['strategy']
    classes = row.get('skip_classes', {})
    lines = [
        '## What was measured',
        '',
        f'Source: `db/trading.db` `signals`, read by '
        f'`agents/forge_shadow_eval.py`. Session covers '
        f"{evaluation['decisions']['n_rows']} decision rows across "
        f"{evaluation['decisions']['n_strategies']} strategies.",
        '',
        f'`{sid}`:',
        '',
        '| Bucket | Count |',
        '|---|---|',
        f"| evaluations | {row['n_evaluations']} |",
        f"| entries | {row['n_entries']} |",
    ]
    for cls in (DATA_BLOCKER, SIM_LIMIT, GENUINE, UNKNOWN):
        if classes.get(cls):
            lines.append(f'| skips classed {cls} | {classes[cls]} |')
    lines += [
        '',
        f"Dominant skip reason: `{row['dominant_skip_reason']}` "
        f"({row['dominant_skip_count']} of {row['n_evaluations']}).",
        '',
        '## Why this is NOT_TESTED and not a failure',
        '',
        'A skip that names a missing input is not the strategy declining. It '
        'is the strategy never being asked. Convention 11 says NOT_TESTED '
        'means "could not run", never "ran and found nothing", and reporting '
        'this strategy as having produced zero entries without that label '
        'would put a verdict in the record that the evidence does not carry.',
        '',
        '## The honest limit',
        '',
        'This says nothing about whether the strategy has an edge. It says '
        'the question has not been asked yet. The edge estimate is null on '
        'purpose: inventing a bps figure here would be a fabricated number, '
        'and fabricated numbers get cited.',
    ]
    return '\n'.join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', default=DEFAULT_DB)
    parser.add_argument('--paper-log', default=DEFAULT_PAPER_LOG)
    parser.add_argument('--mode', default=None,
                        help="filter signals by mode, e.g. 'paper'")
    args = parser.parse_args(argv)
    result = evaluate(args.db, args.paper_log, mode=args.mode)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    return 0 if result.get('status') == 'ok' else 1


if __name__ == '__main__':
    raise SystemExit(main())
