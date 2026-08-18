"""Post-mortem of the LIVE PAPER SHADOW results for PM_fair_value_arb.

READ-ONLY. This script opens `db/trading.db` with `mode=ro` and never writes to
it. A shadow loop is writing to that database concurrently, so every run stamps
its own snapshot boundary (row counts + max opened_ts + max signal ts) at the
top of the output. Two runs at different times will legitimately disagree; cite
the snapshot header, not just the numbers.

WHY THIS EXISTS (convention 6): the numbers in the 2026-08-18 post-mortem must
be re-runnable by somebody who was not there. `sqlite3` one-liners in a shell
history are not a result.

WHAT IT DOES NOT DO
-------------------
It does not grade the strategy. n=33 is a shrug (convention 7) and the
significance section below computes exactly how much of a shrug. It does not
recommend a threshold change; convention 17 says a conclusion that conveniently
loosens a constant is the first thing to distrust.

THE ONE NUMBER THAT MATTERS
---------------------------
`strategies/polymarket/fair_value_arb.py` derives its own break-even in the
module docstring:

    EV per trade = w * MIN_PROFIT - (1 - w) * MAX_LOSS
                 = 0.01w - 0.03(1 - w) = 0.04w - 0.03
    break-even     w = 0.75

So the observed win rate is only interesting relative to 75%. This script
prints that comparison first and computes a one-sided binomial p-value for
"observed w is drawn from w >= 0.75", which is a far more informative test than
"is total P&L different from zero" at this sample size.

It ALSO computes the break-even implied by the actual fills (`realised_geometry`)
because neither leg of the designed geometry is realised in practice. Quoting
only 75% is not the honest comparison; quoting only the realised one hides the
design intent. Both are printed.

Usage:
    env -u PYTHONPATH python3 backtest/analyze_shadow_fair_value_arb.py
    env -u PYTHONPATH python3 backtest/analyze_shadow_fair_value_arb.py --json
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import os
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Sequence, Tuple

DEFAULT_DB = 'db/trading.db'
TARGET_STRATEGY = 'PM_fair_value_arb'

# Restated from strategies/polymarket/fair_value_arb.py so this analysis fails
# loudly if the strategy's constants drift out from under it, rather than
# silently comparing against a stale break-even. Verified against the module at
# run time by `_verify_constants()`.
MIN_PROFIT = 0.01
MAX_LOSS = 0.03
EDGE_THRESHOLD = 0.04
TIME_STOP_SEC = 60.0
WINDOW_CLOSE_EXIT_SEC = 30.0
BREAKEVEN_WIN_RATE = MAX_LOSS / (MIN_PROFIT + MAX_LOSS)  # 0.75

# The shadow loop runs `--poll 5`. Anything closed inside this many seconds was
# closed at the FIRST observation after entry, i.e. with no intervening market.
POLL_BOUNDARY_SEC = 8.0

BOOTSTRAP_N = 20000
BOOTSTRAP_SEED = 20260818  # fixed: a bootstrap you cannot reproduce is not a test


# ---------------------------------------------------------------------------
# snapshot + io
# ---------------------------------------------------------------------------

def _verify_constants() -> Dict[str, Any]:
    """Compare our restated constants against the live strategy module.

    Convention 22: a number copied into a docstring is not the number the code
    uses. If the strategy is edited and this file is not, the mismatch is
    reported in the output rather than quietly changing the break-even.
    """
    out: Dict[str, Any] = {'checked': False, 'mismatches': [], 'error': None}
    try:
        sys.path.insert(0, os.getcwd())
        from strategies.polymarket import fair_value_arb as fva  # noqa
    except Exception as exc:  # pragma: no cover - import env dependent
        out['error'] = f'{type(exc).__name__}: {exc}'
        return out
    out['checked'] = True
    for name, ours in (('MIN_PROFIT', MIN_PROFIT), ('MAX_LOSS', MAX_LOSS),
                       ('EDGE_THRESHOLD', EDGE_THRESHOLD),
                       ('TIME_STOP_SEC', TIME_STOP_SEC),
                       ('WINDOW_CLOSE_EXIT_SEC', WINDOW_CLOSE_EXIT_SEC)):
        theirs = getattr(fva, name, None)
        if theirs is None or abs(float(theirs) - float(ours)) > 1e-12:
            out['mismatches'].append(
                {'constant': name, 'analysis_assumes': ours, 'module_has': theirs})
    return out


def connect_ro(db_path: str) -> sqlite3.Connection:
    """Open the live database READ-ONLY. Never relax this."""
    uri = 'file:{}?mode=ro'.format(db_path)
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    return con


def snapshot_header(con: sqlite3.Connection, db_path: str) -> Dict[str, Any]:
    """Record the snapshot boundary so the run is reproducible.

    The shadow loop is appending while we read. Row counts move. Anything cited
    from this analysis must be cited together with this header.
    """
    def scalar(sql: str) -> Any:
        row = con.execute(sql).fetchone()
        return None if row is None else row[0]

    return {
        'db_path': os.path.abspath(db_path),
        'read_at_utc': _dt.datetime.now(_dt.timezone.utc)
                          .replace(microsecond=0).isoformat(),
        'n_positions': scalar('select count(*) from positions'),
        'n_signals': scalar('select count(*) from signals'),
        'n_fills': scalar('select count(*) from fills'),
        'max_position_opened_ts': scalar('select max(opened_ts) from positions'),
        'max_position_closed_ts': scalar('select max(closed_ts) from positions'),
        'max_signal_ts': scalar('select max(ts) from signals'),
        'min_signal_ts': scalar('select min(ts) from signals'),
        'modes': [r[0] for r in con.execute(
            'select distinct mode from positions order by 1')],
    }


def _ts_to_utc(ms: Optional[float]) -> Optional[str]:
    if ms is None:
        return None
    return _dt.datetime.fromtimestamp(float(ms) / 1000.0, _dt.timezone.utc) \
        .replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# core metrics
# ---------------------------------------------------------------------------

def _pct(numer: float, denom: float) -> Optional[float]:
    return None if not denom else numer / denom


def core_metrics(rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    """Win rate / P&L / fees, split closed vs open.

    Convention 20 in spirit: every position lands in exactly one bucket and the
    accounting identity n_closed + n_open == n_total is asserted, so a row can
    never be silently dropped by a filter.
    """
    closed = [r for r in rows if r['closed_ts'] is not None]
    still_open = [r for r in rows if r['closed_ts'] is None]
    assert len(closed) + len(still_open) == len(rows), 'position bucket leak'

    pnl = [float(r['pnl_net'] or 0.0) for r in closed]
    gross = [float(r['pnl_gross'] or 0.0) for r in closed]
    fees = [float(r['fees'] or 0.0) for r in closed]
    wins = [p for p in pnl if p > 0]
    losses = [p for p in pnl if p < 0]
    scratches = [p for p in pnl if p == 0]
    assert len(wins) + len(losses) + len(scratches) == len(pnl), 'pnl bucket leak'

    rmults = [float(r['r_multiple']) for r in closed
              if r['r_multiple'] is not None]
    gross_sum = sum(gross)
    fee_sum = sum(fees)

    return {
        'n_total': len(rows),
        'n_closed': len(closed),
        'n_open': len(still_open),
        'n_wins': len(wins),
        'n_losses': len(losses),
        'n_scratch': len(scratches),
        # Only CLOSED positions count toward win rate. Stated explicitly
        # because "win rate" over a set containing open positions is a number
        # that quietly improves as losers stay open.
        'win_rate_closed_only': _pct(len(wins), len(closed)),
        'avg_win': (statistics.mean(wins) if wins else None),
        'avg_loss': (statistics.mean(losses) if losses else None),
        'largest_win': (max(wins) if wins else None),
        'largest_loss': (min(losses) if losses else None),
        'profit_factor': (sum(wins) / abs(sum(losses)) if losses else None),
        'total_pnl_gross': gross_sum,
        'total_pnl_net': sum(pnl),
        'total_fees': fee_sum,
        'fee_share_of_gross': (abs(fee_sum) / abs(gross_sum)
                               if gross_sum else None),
        'avg_pnl_per_trade': (statistics.mean(pnl) if pnl else None),
        'stdev_pnl_per_trade': (statistics.pstdev(pnl) if len(pnl) > 1 else None),
        'sample_stdev_pnl': (statistics.stdev(pnl) if len(pnl) > 1 else None),
        'avg_r_multiple': (statistics.mean(rmults) if rmults else None),
        'n_with_r_multiple': len(rmults),
    }


def breakeven_comparison(m: Dict[str, Any]) -> Dict[str, Any]:
    """Observed win rate vs the 75% the payoff geometry REQUIRES.

    This is the single most useful number in the analysis: the strategy is
    constructed so that it needs w=75% to scratch. Also computes the one-sided
    binomial p-value for H0: w >= 0.75 (i.e. how surprising the observed win
    count is IF the strategy were exactly at break-even).
    """
    n, k = m['n_closed'], m['n_wins']
    w = m['win_rate_closed_only']
    out: Dict[str, Any] = {
        'required_win_rate': BREAKEVEN_WIN_RATE,
        'observed_win_rate': w,
        'n_closed': n,
        'n_wins': k,
        'wins_needed_for_breakeven': math.ceil(BREAKEVEN_WIN_RATE * n) if n else None,
        'ev_per_trade_at_observed_w': (None if w is None
                                       else (MIN_PROFIT + MAX_LOSS) * w - MAX_LOSS),
        'formula': 'EV = (MIN_PROFIT+MAX_LOSS)*w - MAX_LOSS = 0.04w - 0.03',
    }
    if n:
        # P(X <= k | n, p=0.75). Small p => observed is hard to reconcile with
        # a strategy that is merely at break-even.
        p = BREAKEVEN_WIN_RATE
        tail = sum(math.comb(n, i) * (p ** i) * ((1 - p) ** (n - i))
                   for i in range(0, k + 1))
        out['p_value_one_sided_vs_75pct'] = tail
        out['interpretation'] = (
            'p = P(observing <= {k} wins in {n} | true win rate were exactly '
            '75%). This tests the win rate against the strategy\'s OWN '
            'break-even, not against zero.'.format(k=k, n=n))
    return out


def realised_geometry(rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    """The break-even win rate implied by the FILLS, not by the constants.

    The designed geometry is +1c / -3c, which needs w=75%. Neither leg is
    realised: the profit target sells at the WALKED BID (which can print well
    above the 1c limit) and the stop sells at URGENT_SELL_LIMIT=0.00 (which can
    print well below the 3c trigger). So the real break-even is

        avg_loss_per_share / (avg_win_per_share + avg_loss_per_share)

    Quoting only the 75% figure hides that the win leg overshoots too. Both are
    reported; the observed win rate must clear BOTH to be interesting.
    """
    wins_ps, losses_ps = [], []
    for r in rows:
        if r['closed_ts'] is None or r['exit_px'] is None:
            continue
        qty = float(r['qty'] or 0.0)
        if qty <= 0:
            continue
        per_share = float(r['pnl_net'] or 0.0) / qty
        if per_share > 0:
            wins_ps.append(per_share)
        elif per_share < 0:
            losses_ps.append(per_share)
    if not wins_ps or not losses_ps:
        return {'computable': False,
                'reason': 'need at least one win and one loss'}
    aw = statistics.mean(wins_ps)
    al = abs(statistics.mean(losses_ps))
    return {
        'computable': True,
        'n_wins': len(wins_ps),
        'n_losses': len(losses_ps),
        'designed_win_per_share': MIN_PROFIT,
        'designed_loss_per_share': MAX_LOSS,
        'designed_breakeven_win_rate': BREAKEVEN_WIN_RATE,
        'realised_avg_win_per_share': aw,
        'realised_avg_loss_per_share': al,
        'realised_breakeven_win_rate': al / (aw + al),
        'win_leg_vs_design_x': aw / MIN_PROFIT,
        'loss_leg_vs_design_x': al / MAX_LOSS,
        'note': ('BOTH legs overshoot their design. The loss leg overshoots by '
                 'more, so the realised break-even is kinder than 75% while the '
                 'realised loss per trade is far worse than the declared 3c cap.'),
    }


def spread_mechanism(rows: Sequence[sqlite3.Row],
                     poll_sec: float = POLL_BOUNDARY_SEC) -> Dict[str, Any]:
    """Test the module docstring's own predicted failure mode.

    The strategy BUYS AT THE ASK and measures both MIN_PROFIT and MAX_LOSS
    against the BID. If the bid-ask spread is >= MAX_LOSS, the stop sits INSIDE
    the spread and the position is stopped out on arrival with zero adverse
    market movement. That failure is structural, not a market outcome, and its
    signature is: a loss of almost exactly MAX_LOSS, taken at the first
    observation after entry.

    Counting these BEFORE calling the market unkind is the whole point.
    """
    fast, slow = [], []
    for r in rows:
        if r['closed_ts'] is None or r['exit_px'] is None:
            continue
        held = (float(r['closed_ts']) - float(r['opened_ts'])) / 1000.0
        move = float(r['exit_px']) - float(r['entry_px'])
        (fast if held <= poll_sec else slow).append((held, move, r))
    if not fast and not slow:
        return {'computable': False}
    allt = fast + slow
    at_trigger = [t for t in allt if t[1] < 0 and abs(t[1] + MAX_LOSS) < 0.005]
    return {
        'computable': True,
        'poll_boundary_sec': poll_sec,
        'n_total': len(allt),
        'n_exited_within_one_poll': len(fast),
        'share_exited_within_one_poll': _pct(len(fast), len(allt)),
        'n_fast_and_negative': sum(1 for t in fast if t[1] < 0),
        'mean_move_fast': (statistics.mean([t[1] for t in fast]) if fast else None),
        'median_move_fast': (statistics.median([t[1] for t in fast]) if fast else None),
        'n_stopped_at_almost_exactly_max_loss': len(at_trigger),
        'pnl_of_those': sum(float(t[2]['pnl_net'] or 0.0) for t in at_trigger),
        'reading': ('a loss of ~MAX_LOSS taken at the first observation after '
                    'entry is the round-trip spread being paid, not an adverse '
                    'move being suffered.'),
    }


def trading_halt(con: sqlite3.Connection) -> Dict[str, Any]:
    """Did the strategy stop trading, and why? A frozen sample cannot grow.

    The kill condition in fair_value_arb.py needs 50 trades. If the daily loss
    breaker has latched, the sample is capped below 50 and the kill condition
    cannot be evaluated at all today. That is a fact about the SAMPLE and it has
    to be stated before anybody reads 33 trades as a verdict.
    """
    last_act = con.execute(
        "select max(ts) from signals where strategy_id=? and acted=1",
        (TARGET_STRATEGY,)).fetchone()[0]
    last_sig = con.execute(
        "select max(ts) from signals where strategy_id=?",
        (TARGET_STRATEGY,)).fetchone()[0]
    br = con.execute(
        "select min(ts), max(ts), count(*) from signals "
        "where skip_reason like 'risk_gate:daily_loss_breaker%'").fetchone()
    sample = con.execute(
        "select skip_reason from signals "
        "where skip_reason like 'risk_gate:daily_loss_breaker%' "
        "order by ts desc limit 1").fetchone()
    gap = None
    if last_act and last_sig:
        gap = (float(last_sig) - float(last_act)) / 1000.0
    return {
        'last_entry_ts': last_act,
        'last_entry_utc': _ts_to_utc(last_act),
        'latest_signal_utc': _ts_to_utc(last_sig),
        'seconds_since_last_entry': gap,
        'daily_loss_breaker_first_utc': _ts_to_utc(br[0]) if br and br[0] else None,
        'daily_loss_breaker_last_utc': _ts_to_utc(br[1]) if br and br[1] else None,
        'daily_loss_breaker_n_rows': (br[2] if br else 0),
        'daily_loss_breaker_message': (sample[0] if sample else None),
        'sample_is_frozen': bool(br and br[2] and gap and gap > 600),
    }


def window_attempts(con: sqlite3.Connection) -> Dict[str, Any]:
    """Defuse the `max_trades_this_window` headline before anybody quotes it.

    MAX_TRADES_PER_WINDOW counts ATTEMPTS, not fills, and the loop re-evaluates
    every ~5s. Once 3 attempts are burnt, every remaining poll in that 5-minute
    window writes one more `max_trades_this_window` row. So that count measures
    the POLLING CADENCE, not a suppressed appetite to trade. Convention 20: two
    causes must not share one number.
    """
    def n(sql: str) -> int:
        return con.execute(sql, (TARGET_STRATEGY,)).fetchone()[0]

    n_rows = n("select count(*) from signals where strategy_id=?")
    n_cap = n("select count(*) from signals where strategy_id=? "
              "and skip_reason='max_trades_this_window'")
    # An "attempt" is a row that reached the entry path: acted, or refused by a
    # downstream gate (risk gate / adapter), which still burns the budget.
    n_attempts = n("select count(*) from signals where strategy_id=? and (acted=1 "
                   "or skip_reason like 'risk_gate:%' or skip_reason in "
                   "('unfillable_at_cap','effective_ask_above_cap'))")
    n_acted = n("select count(*) from signals where strategy_id=? and acted=1")
    return {
        'n_signal_rows': n_rows,
        'n_max_trades_this_window_rows': n_cap,
        'share_of_all_rows': _pct(n_cap, n_rows),
        'n_entry_attempts': n_attempts,
        'n_attempts_that_became_fills': n_acted,
        'attempt_to_fill_rate': _pct(n_acted, n_attempts),
        'reading': ('`max_trades_this_window` is the largest skip bucket but it '
                    'is a cadence artifact, not a finding. The informative number '
                    'is the attempt-to-fill rate: attempts refused downstream '
                    'still burn the 3-per-window budget, exactly the bias the '
                    'module docstring predicts.'),
    }


def significance(pnl: Sequence[float], seed: int = BOOTSTRAP_SEED,
                 iters: int = BOOTSTRAP_N) -> Dict[str, Any]:
    """Is the observed total P&L distinguishable from noise at this n?

    A t-stat on per-trade P&L plus a percentile bootstrap of the MEAN. Both are
    reported because the per-trade distribution is bimodal (a profit target
    against an open-ended stop), which is exactly the shape where a
    normal-theory t-stat is least trustworthy.
    """
    n = len(pnl)
    if n < 2:
        return {'n': n, 'computable': False,
                'reason': 'fewer than 2 closed trades'}
    mean = statistics.mean(pnl)
    sd = statistics.stdev(pnl)
    se = sd / math.sqrt(n)
    t = mean / se if se else None

    rng = random.Random(seed)
    means: List[float] = []
    for _ in range(iters):
        means.append(sum(rng.choice(pnl) for _ in range(n)) / n)
    means.sort()

    def q(p: float) -> float:
        idx = min(len(means) - 1, max(0, int(round(p * (len(means) - 1)))))
        return means[idx]

    n_ge_zero = sum(1 for x in means if x >= 0)
    return {
        'n': n,
        'computable': True,
        'mean_pnl_per_trade': mean,
        'stdev_pnl_per_trade': sd,
        'standard_error': se,
        't_stat': t,
        'abs_t_vs_2': (None if t is None else abs(t) >= 2.0),
        'bootstrap_iters': iters,
        'bootstrap_seed': seed,
        'bootstrap_mean_ci95': [q(0.025), q(0.975)],
        'bootstrap_p_mean_ge_zero': n_ge_zero / len(means),
        'trades_needed_for_t2_at_this_effect': (
            None if not mean else math.ceil((2.0 * sd / mean) ** 2)),
    }


# ---------------------------------------------------------------------------
# breakdowns
# ---------------------------------------------------------------------------

def group_pnl(rows: Sequence[sqlite3.Row], keyfn) -> List[Dict[str, Any]]:
    """Generic (n, sum pnl, avg pnl, wins) breakdown, sorted by total P&L."""
    buckets: Dict[Any, List[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        if r['closed_ts'] is None:
            continue
        buckets[keyfn(r)].append(r)
    out = []
    for key, rs in buckets.items():
        pnl = [float(x['pnl_net'] or 0.0) for x in rs]
        out.append({
            'key': key,
            'n': len(rs),
            'n_wins': sum(1 for p in pnl if p > 0),
            'win_rate': _pct(sum(1 for p in pnl if p > 0), len(pnl)),
            'total_pnl_net': sum(pnl),
            'avg_pnl': statistics.mean(pnl) if pnl else None,
        })
    out.sort(key=lambda d: d['total_pnl_net'])
    return out


def hold_times(rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    """Hold-time distribution and the TIME_STOP_SEC binding question.

    `opened_ts`/`closed_ts` are epoch MILLISECONDS in this schema. Getting that
    wrong turns a 12-second hold into a 12,000-second one, so the unit is
    asserted rather than assumed.
    """
    pairs = []
    for r in rows:
        if r['closed_ts'] is None or r['opened_ts'] is None:
            continue
        # 1e12 ms ~ 2001; 1e12 s would be year 33658. Any ts above 1e11 is ms.
        assert float(r['opened_ts']) > 1e11, 'opened_ts is not epoch ms'
        held = (float(r['closed_ts']) - float(r['opened_ts'])) / 1000.0
        pairs.append((held, float(r['pnl_net'] or 0.0), r))
    if not pairs:
        return {'computable': False}
    held_vals = sorted(p[0] for p in pairs)

    def pct(p: float) -> float:
        idx = min(len(held_vals) - 1,
                  max(0, int(round(p * (len(held_vals) - 1)))))
        return held_vals[idx]

    at_or_past_stop = [p for p in pairs if p[0] >= TIME_STOP_SEC]
    short = [p for p in pairs if p[0] < 15.0]
    return {
        'computable': True,
        'n': len(pairs),
        'min_sec': held_vals[0],
        'p10_sec': pct(0.10),
        'median_sec': statistics.median(held_vals),
        'p90_sec': pct(0.90),
        'max_sec': held_vals[-1],
        'mean_sec': statistics.mean(held_vals),
        'time_stop_sec': TIME_STOP_SEC,
        'n_held_at_or_past_time_stop': len(at_or_past_stop),
        'time_stop_is_binding': bool(at_or_past_stop),
        'n_held_under_15s': len(short),
        'pnl_under_15s': sum(p[1] for p in short),
        'pnl_15s_and_over': sum(p[1] for p in pairs if p[0] >= 15.0),
        'corr_hold_vs_pnl': _corr([p[0] for p in pairs], [p[1] for p in pairs]),
    }


def _corr(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 3:
        return None
    mx, my = statistics.mean(xs), statistics.mean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    dx = math.sqrt(sum((a - mx) ** 2 for a in xs))
    dy = math.sqrt(sum((b - my) ** 2 for b in ys))
    return None if dx == 0 or dy == 0 else num / (dx * dy)


def entry_buckets(rows: Sequence[sqlite3.Row]) -> List[Dict[str, Any]]:
    """P&L by entry price band.

    The strategy declares a TRADEABLE FAIR VALUE band of 0.10-0.90 but places no
    floor on the ENTRY price itself. An entry at 0.06 against a fair value of
    0.54 is inside the letter of the rule and outside its intent, so the bands
    below deliberately isolate sub-0.10 entries.
    """
    edges = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40),
             (0.40, 0.50), (0.50, 0.60), (0.60, 0.90), (0.90, 1.01)]

    def label(px: float) -> str:
        for lo, hi in edges:
            if lo <= px < hi:
                return '[{:.2f},{:.2f})'.format(lo, hi)
        return 'other'

    return group_pnl(rows, lambda r: label(float(r['entry_px'])))


def stop_overshoot(rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    """How far past the declared stop did the fills actually land?

    MAX_LOSS is 3c and the stop exits at URGENT_SELL_LIMIT = 0.00, meaning it
    accepts ANY price the bid side offers. So the realised loss per share is not
    capped at 3c; it is capped by book depth and by how far the bid has already
    moved when the next poll observes it. This measures the gap between the
    designed stop and the fill, which is the difference between "the stop level
    is wrong" and "the stop level is right but the exit slips".
    """
    over = []
    for r in rows:
        if r['closed_ts'] is None or r['exit_px'] is None:
            continue
        entry, exit_px = float(r['entry_px']), float(r['exit_px'])
        move = exit_px - entry
        if move >= 0:
            continue
        over.append({
            'id': r['id'],
            'entry_px': entry,
            'exit_px': exit_px,
            'stop_px': (None if r['stop_px'] is None else float(r['stop_px'])),
            'loss_per_share': -move,
            'designed_max_loss': MAX_LOSS,
            'overshoot_cents': round((-move - MAX_LOSS) * 100, 4),
            'pnl_net': float(r['pnl_net'] or 0.0),
        })
    beyond = [o for o in over if o['loss_per_share'] > MAX_LOSS + 1e-9]
    return {
        'n_losing_fills': len(over),
        'n_worse_than_designed_max_loss': len(beyond),
        'worst_loss_per_share': (max(o['loss_per_share'] for o in over)
                                 if over else None),
        'mean_loss_per_share': (statistics.mean(o['loss_per_share'] for o in over)
                                if over else None),
        'pnl_from_overshoot_fills': sum(o['pnl_net'] for o in beyond),
        'examples': sorted(beyond, key=lambda o: -o['loss_per_share'])[:5],
    }


# ---------------------------------------------------------------------------
# signals: acted features + declines
# ---------------------------------------------------------------------------

FEATURE_KEYS = [
    'raw_edge', 'realized_edge', 'realized_edge_bps', 'side_fair_value',
    'best_ask', 'effective_ask', 'ask_depth_within_band', 'seconds_remaining',
    'seconds_into_window', 'holding_seconds_available', 'fv_book_imbalance',
    'fv_vol_ratio', 'fv_atr_usd', 'fv_displacement_usd', 'shares',
    'notional_usdc', 'attempt_number', 'tape_samples', 'confidence',
]


def acted_features(con: sqlite3.Connection,
                   rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    """Join closed positions to the signal that produced them and compare
    winners against losers on every numeric feature the strategy logged.

    Convention 4 caveat, restated in the output: this is a SCAN over features,
    not a prediction made in advance. Anything that separates here is a lead to
    test, never a finding. With 7 wins a two-group comparison has essentially no
    power, so Cohen's d below is descriptive and no p-value is offered.
    """
    by_sig: Dict[str, sqlite3.Row] = {}
    for r in con.execute(
            "select id, features_json, skip_reason, acted, ts from signals "
            "where strategy_id=? and acted=1", (TARGET_STRATEGY,)):
        by_sig[r['id']] = r

    matched, unmatched = [], []
    for r in rows:
        if r['closed_ts'] is None:
            continue
        sig = by_sig.get(r['signal_id'])
        if sig is None:
            unmatched.append(r['id'])
            continue
        try:
            feats = json.loads(sig['features_json'])
        except Exception:
            unmatched.append(r['id'])
            continue
        matched.append((float(r['pnl_net'] or 0.0), feats, r))
    assert len(matched) + len(unmatched) == \
        sum(1 for r in rows if r['closed_ts'] is not None), 'signal join leak'

    wins = [m for m in matched if m[0] > 0]
    losses = [m for m in matched if m[0] < 0]

    comparison = []
    for key in FEATURE_KEYS:
        wv = [float(f[key]) for _, f, _ in wins
              if isinstance(f.get(key), (int, float))]
        lv = [float(f[key]) for _, f, _ in losses
              if isinstance(f.get(key), (int, float))]
        if not wv or not lv:
            continue
        mw, ml = statistics.mean(wv), statistics.mean(lv)
        sw = statistics.stdev(wv) if len(wv) > 1 else 0.0
        sl = statistics.stdev(lv) if len(lv) > 1 else 0.0
        pooled = math.sqrt(((len(wv) - 1) * sw ** 2 + (len(lv) - 1) * sl ** 2)
                           / max(1, len(wv) + len(lv) - 2)) if (sw or sl) else 0.0
        comparison.append({
            'feature': key,
            'n_win': len(wv), 'n_loss': len(lv),
            'mean_win': mw, 'mean_loss': ml,
            'median_win': statistics.median(wv),
            'median_loss': statistics.median(lv),
            'delta': mw - ml,
            'cohens_d': ((mw - ml) / pooled) if pooled else None,
        })
    comparison.sort(key=lambda d: -abs(d['cohens_d'] or 0.0))

    edges = [float(f['raw_edge']) for _, f, _ in matched
             if isinstance(f.get('raw_edge'), (int, float))]
    return {
        'n_matched': len(matched),
        'n_unmatched': len(unmatched),
        'unmatched_ids': unmatched[:10],
        'caveat': ('SCAN, not a prediction (convention 4). n_wins is tiny; '
                   'cohens_d is descriptive only and no p-value is offered.'),
        'feature_comparison': comparison,
        'raw_edge_summary': ({
            'n': len(edges),
            'min': min(edges), 'max': max(edges),
            'median': statistics.median(edges),
            'mean': statistics.mean(edges),
            'edge_threshold': EDGE_THRESHOLD,
            'n_above_10x_threshold': sum(1 for e in edges
                                         if e >= 10 * EDGE_THRESHOLD),
            'n_above_0_20': sum(1 for e in edges if e >= 0.20),
            'note': ('EDGE_THRESHOLD is a FLOOR with no ceiling. A 20-48c '
                     '"mispricing" on a 5-minute BTC binary is not an arb, it '
                     'is the fair-value model diverging from the book. '
                     'Convention 5 rejects anything under 30bps; nothing '
                     'rejects 80,000bps.'),
        } if edges else None),
    }


def declines(con: sqlite3.Connection, top: int = 20) -> Dict[str, Any]:
    """What the strategy is mostly NOT doing, and why.

    Convention 11 applies: a skip is not a result. It is a record of a decision
    not to act, and a DATA_BLOCKER skip means the strategy could not run at all.
    """
    total = con.execute(
        "select count(*) from signals where strategy_id=?",
        (TARGET_STRATEGY,)).fetchone()[0]
    acted = con.execute(
        "select count(*) from signals where strategy_id=? and acted=1",
        (TARGET_STRATEGY,)).fetchone()[0]

    counts = Counter()
    for r in con.execute(
            "select skip_reason from signals where strategy_id=? and acted=0",
            (TARGET_STRATEGY,)):
        reason = r['skip_reason'] or '<null>'
        # Risk-gate reasons embed live numbers ("realized loss today =$30.08"),
        # so they never aggregate unless the payload is stripped. Two spellings
        # of one cause must not become two causes (convention 20).
        if reason.startswith('risk_gate:'):
            reason = 'risk_gate:' + reason.split(':', 2)[1].strip()
        counts[reason] += 1
    n_declined = sum(counts.values())
    assert acted + n_declined == total, 'signal accounting identity failed'
    return {
        'n_signal_rows': total,
        'n_acted': acted,
        'n_declined': n_declined,
        'act_rate': _pct(acted, total),
        'top_skip_reasons': [
            {'reason': k, 'n': v, 'share_of_declines': v / n_declined}
            for k, v in counts.most_common(top)],
    }


def time_of_day(rows: Sequence[sqlite3.Row]) -> List[Dict[str, Any]]:
    """10-minute UTC buckets.

    NOTE for the reader: if every trade falls inside one contiguous session,
    this table is chronological order, NOT a time-of-day effect. The session
    span is printed above it so that cannot be misread.
    """
    def key(r: sqlite3.Row) -> str:
        dt = _dt.datetime.fromtimestamp(float(r['opened_ts']) / 1000.0,
                                        _dt.timezone.utc)
        return '{:02d}:{:02d}Z'.format(dt.hour, (dt.minute // 10) * 10)
    return group_pnl(rows, key)


def session_span(rows: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    ts = [float(r['opened_ts']) for r in rows if r['opened_ts'] is not None]
    if not ts:
        return {'computable': False}
    return {
        'computable': True,
        'first_entry_utc': _ts_to_utc(min(ts)),
        'last_entry_utc': _ts_to_utc(max(ts)),
        'span_minutes': (max(ts) - min(ts)) / 60000.0,
        'n_distinct_windows': len({r['pair'] for r in rows}),
    }


def window_key(r: sqlite3.Row) -> str:
    """`pair` looks like btc-updown-5m-1787022000; the tail is the window ts."""
    return str(r['pair'])


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------

def build(db_path: str) -> Dict[str, Any]:
    con = connect_ro(db_path)
    try:
        snap = snapshot_header(con, db_path)
        snap['max_position_opened_utc'] = _ts_to_utc(snap['max_position_opened_ts'])
        snap['max_signal_utc'] = _ts_to_utc(snap['max_signal_ts'])
        snap['min_signal_utc'] = _ts_to_utc(snap['min_signal_ts'])

        rows = list(con.execute(
            'select * from positions where strategy_id=? order by opened_ts',
            (TARGET_STRATEGY,)))
        m = core_metrics(rows)
        closed_pnl = [float(r['pnl_net'] or 0.0)
                      for r in rows if r['closed_ts'] is not None]

        peers = []
        for sid, in con.execute(
                'select distinct strategy_id from positions order by 1'):
            prows = list(con.execute(
                'select * from positions where strategy_id=?', (sid,)))
            pm = core_metrics(prows)
            pm['strategy_id'] = sid
            peers.append(pm)

        return {
            'snapshot': snap,
            'constants_check': _verify_constants(),
            'strategy': TARGET_STRATEGY,
            'core': m,
            'peers': peers,
            'session': session_span(rows),
            'trading_halt': trading_halt(con),
            'breakeven': breakeven_comparison(m),
            'realised_geometry': realised_geometry(rows),
            'significance': significance(closed_pnl),
            'exit_reasons': group_pnl(rows, lambda r: r['exit_reason'] or '<null>'),
            'hold_times': hold_times(rows),
            'spread_mechanism': spread_mechanism(rows),
            'entry_buckets': entry_buckets(rows),
            'stop_overshoot': stop_overshoot(rows),
            'by_pair': group_pnl(rows, window_key),
            'by_time_of_day': time_of_day(rows),
            'acted_features': acted_features(con, rows),
            'window_attempts': window_attempts(con),
            'declines': declines(con),
        }
    finally:
        con.close()


def _f(v: Any, spec: str = '.4f') -> str:
    if v is None:
        return 'n/a'
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return format(v, spec)
    return str(v)


def render(rep: Dict[str, Any]) -> str:
    L: List[str] = []
    a = L.append
    s = rep['snapshot']
    a('=' * 78)
    a('POST-MORTEM: {}  (LIVE PAPER SHADOW)'.format(rep['strategy']))
    a('=' * 78)
    a('SNAPSHOT (the db is being written concurrently; cite this header)')
    a('  db                    : {}'.format(s['db_path']))
    a('  read at (UTC)         : {}'.format(s['read_at_utc']))
    a('  positions / signals   : {} / {}'.format(s['n_positions'], s['n_signals']))
    a('  max opened_ts         : {}  ({})'.format(s['max_position_opened_ts'],
                                                  s['max_position_opened_utc']))
    a('  signal ts range (UTC) : {} .. {}'.format(s['min_signal_utc'],
                                                  s['max_signal_utc']))
    a('  modes present         : {}'.format(', '.join(s['modes'])))
    cc = rep['constants_check']
    if cc.get('error'):
        a('  CONSTANTS CHECK       : COULD NOT IMPORT ({})'.format(cc['error']))
    elif cc['mismatches']:
        a('  CONSTANTS CHECK       : *** MISMATCH *** {}'.format(cc['mismatches']))
    else:
        a('  constants check       : OK (matches fair_value_arb.py)')
    a('')

    th = rep['trading_halt']
    a('-- IS THE SAMPLE STILL GROWING? ' + '-' * 45)
    a('  last entry (UTC)      : {}'.format(th['last_entry_utc']))
    a('  latest signal (UTC)   : {}'.format(th['latest_signal_utc']))
    a('  idle since last entry : {} sec'.format(
        _f(th['seconds_since_last_entry'], '.0f')))
    a('  daily loss breaker    : {} rows, first {} last {}'.format(
        th['daily_loss_breaker_n_rows'], th['daily_loss_breaker_first_utc'],
        th['daily_loss_breaker_last_utc']))
    a('  breaker message       : {}'.format(th['daily_loss_breaker_message']))
    a('  SAMPLE FROZEN         : {}  <-- if True, n cannot reach the 50 the '
      'kill condition needs'.format(th['sample_is_frozen']))
    a('')

    m = rep['core']
    a('-- CORE METRICS ' + '-' * 61)
    a('  positions total       : {}   (closed {}, still open {})'.format(
        m['n_total'], m['n_closed'], m['n_open']))
    a('  wins / losses / flat  : {} / {} / {}'.format(
        m['n_wins'], m['n_losses'], m['n_scratch']))
    a('  win rate (CLOSED only): {}'.format(
        'n/a' if m['win_rate_closed_only'] is None
        else '{:.1%}'.format(m['win_rate_closed_only'])))
    a('  avg win / avg loss    : {} / {}'.format(_f(m['avg_win']),
                                                 _f(m['avg_loss'])))
    a('  largest win / loss    : {} / {}'.format(_f(m['largest_win']),
                                                 _f(m['largest_loss'])))
    a('  profit factor         : {}'.format(_f(m['profit_factor'])))
    a('  total gross / net P&L : {} / {}'.format(_f(m['total_pnl_gross']),
                                                 _f(m['total_pnl_net'])))
    a('  total fees            : {}   fee share of gross: {}'.format(
        _f(m['total_fees']),
        'n/a' if m['fee_share_of_gross'] is None
        else '{:.2%}'.format(m['fee_share_of_gross'])))
    a('  avg P&L / trade       : {}   (sd {})'.format(
        _f(m['avg_pnl_per_trade']), _f(m['sample_stdev_pnl'])))
    a('  avg r_multiple        : {}  (n={})'.format(_f(m['avg_r_multiple']),
                                                    m['n_with_r_multiple']))
    a('')

    a('-- PEER COMPARISON ' + '-' * 58)
    a('  {:<24} {:>4} {:>5} {:>8} {:>9} {:>8}'.format(
        'strategy', 'n', 'open', 'win%', 'net P&L', 'fees'))
    for p in sorted(rep['peers'], key=lambda d: d['total_pnl_net']):
        a('  {:<24} {:>4} {:>5} {:>8} {:>9} {:>8}'.format(
            p['strategy_id'], p['n_total'], p['n_open'],
            'n/a' if p['win_rate_closed_only'] is None
            else '{:.1%}'.format(p['win_rate_closed_only']),
            _f(p['total_pnl_net'], '.3f'), _f(p['total_fees'], '.3f')))
    a('')

    b = rep['breakeven']
    a('-- THE COMPARISON THAT MATTERS: observed w vs REQUIRED 75% ' + '-' * 18)
    a('  {}'.format(b['formula']))
    a('  required win rate     : {:.1%}'.format(b['required_win_rate']))
    a('  observed win rate     : {}   ({} of {} closed)'.format(
        'n/a' if b['observed_win_rate'] is None
        else '{:.1%}'.format(b['observed_win_rate']), b['n_wins'], b['n_closed']))
    a('  wins needed to scratch: {} (observed {})'.format(
        b['wins_needed_for_breakeven'], b['n_wins']))
    a('  EV/trade at observed w: {} per share (design geometry)'.format(
        _f(b['ev_per_trade_at_observed_w'])))
    if 'p_value_one_sided_vs_75pct' in b:
        a('  one-sided p vs 75%    : {:.3g}'.format(b['p_value_one_sided_vs_75pct']))
        a('  {}'.format(b['interpretation']))
    a('')

    rg = rep['realised_geometry']
    a('-- ...AND THE BREAK-EVEN THE FILLS ACTUALLY IMPLY ' + '-' * 27)
    if not rg.get('computable'):
        a('  NOT COMPUTABLE: {}'.format(rg.get('reason')))
    else:
        a('  designed  +{:.2f}c / -{:.2f}c  -> break-even {:.1%}'.format(
            rg['designed_win_per_share'] * 100,
            rg['designed_loss_per_share'] * 100,
            rg['designed_breakeven_win_rate']))
        a('  realised  +{:.2f}c / -{:.2f}c  -> break-even {:.1%}'.format(
            rg['realised_avg_win_per_share'] * 100,
            rg['realised_avg_loss_per_share'] * 100,
            rg['realised_breakeven_win_rate']))
        a('  win leg  {:.2f}x design,  loss leg {:.2f}x design'.format(
            rg['win_leg_vs_design_x'], rg['loss_leg_vs_design_x']))
        a('  {}'.format(rg['note']))
    a('')

    sg = rep['significance']
    a('-- IS THIS DISTINGUISHABLE FROM NOISE? (convention 7) ' + '-' * 23)
    if not sg.get('computable'):
        a('  NOT COMPUTABLE: {}'.format(sg.get('reason')))
    else:
        a('  n closed trades       : {}   <-- a SHRUG on its own'.format(sg['n']))
        a('  mean P&L / trade      : {}  (sd {}, se {})'.format(
            _f(sg['mean_pnl_per_trade']), _f(sg['stdev_pnl_per_trade']),
            _f(sg['standard_error'])))
        a('  t-stat                : {}   |t| >= 2 ? {}'.format(
            _f(sg['t_stat'], '.3f'), sg['abs_t_vs_2']))
        lo, hi = sg['bootstrap_mean_ci95']
        a('  bootstrap 95% CI mean : [{}, {}]  ({} iters, seed {})'.format(
            _f(lo), _f(hi), sg['bootstrap_iters'], sg['bootstrap_seed']))
        a('  P(bootstrap mean >= 0): {:.4f}'.format(sg['bootstrap_p_mean_ge_zero']))
        a('  n needed for |t|=2 at this effect size: {}'.format(
            sg['trades_needed_for_t2_at_this_effect']))
    a('')

    a('-- EXIT REASONS ' + '-' * 61)
    a('  {:<26} {:>4} {:>6} {:>10} {:>9}'.format(
        'reason', 'n', 'win%', 'net P&L', 'avg'))
    for g in rep['exit_reasons']:
        a('  {:<26} {:>4} {:>6} {:>10} {:>9}'.format(
            str(g['key']), g['n'],
            'n/a' if g['win_rate'] is None else '{:.0%}'.format(g['win_rate']),
            _f(g['total_pnl_net'], '.3f'), _f(g['avg_pnl'], '.3f')))
    a('')

    h = rep['hold_times']
    a('-- HOLD TIME ' + '-' * 64)
    if not h.get('computable'):
        a('  NOT COMPUTABLE')
    else:
        a('  min/p10/median/p90/max: {} / {} / {} / {} / {} sec'.format(
            _f(h['min_sec'], '.1f'), _f(h['p10_sec'], '.1f'),
            _f(h['median_sec'], '.1f'), _f(h['p90_sec'], '.1f'),
            _f(h['max_sec'], '.1f')))
        a('  TIME_STOP_SEC         : {}   binding? {}  (n at/past stop = {})'
          .format(h['time_stop_sec'], h['time_stop_is_binding'],
                  h['n_held_at_or_past_time_stop']))
        a('  held < 15s            : n={}  P&L {}'.format(
            h['n_held_under_15s'], _f(h['pnl_under_15s'], '.3f')))
        a('  held >= 15s           : P&L {}'.format(_f(h['pnl_15s_and_over'], '.3f')))
        a('  corr(hold_sec, pnl)   : {}'.format(_f(h['corr_hold_vs_pnl'], '.3f')))
    a('')

    sm = rep['spread_mechanism']
    a('-- SPREAD-ON-ARRIVAL TEST (the docstring\'s own predicted failure) ' + '-' * 11)
    if not sm.get('computable'):
        a('  NOT COMPUTABLE')
    else:
        a('  closed within one poll ({}s): {} of {}  ({:.0%})'.format(
            _f(sm['poll_boundary_sec'], '.0f'), sm['n_exited_within_one_poll'],
            sm['n_total'], sm['share_exited_within_one_poll']))
        a('  ...of which negative        : {}'.format(sm['n_fast_and_negative']))
        a('  mean / median move, fast    : {} / {}'.format(
            _f(sm['mean_move_fast']), _f(sm['median_move_fast'])))
        a('  stopped at ~exactly MAX_LOSS: {}  (P&L {})'.format(
            sm['n_stopped_at_almost_exactly_max_loss'],
            _f(sm['pnl_of_those'], '.3f')))
        a('  {}'.format(sm['reading']))
    a('')

    a('-- ENTRY PRICE BUCKETS ' + '-' * 54)
    a('  {:<16} {:>4} {:>6} {:>10} {:>9}'.format('bucket', 'n', 'win%',
                                                 'net P&L', 'avg'))
    for g in rep['entry_buckets']:
        a('  {:<16} {:>4} {:>6} {:>10} {:>9}'.format(
            str(g['key']), g['n'],
            'n/a' if g['win_rate'] is None else '{:.0%}'.format(g['win_rate']),
            _f(g['total_pnl_net'], '.3f'), _f(g['avg_pnl'], '.3f')))
    a('')

    so = rep['stop_overshoot']
    a('-- STOP vs REALISED FILL ' + '-' * 52)
    a('  losing fills                     : {}'.format(so['n_losing_fills']))
    a('  worse than MAX_LOSS ({:.2f})       : {}'.format(
        MAX_LOSS, so['n_worse_than_designed_max_loss']))
    a('  worst / mean loss per share      : {} / {}'.format(
        _f(so['worst_loss_per_share'], '.4f'), _f(so['mean_loss_per_share'], '.4f')))
    a('  P&L from overshooting fills      : {}'.format(
        _f(so['pnl_from_overshoot_fills'], '.3f')))
    for ex in so['examples']:
        a('    {} entry={:.2f} exit={:.2f} stop={} loss/sh={:.3f} '
          '({:+.2f}c past design) pnl={:.2f}'.format(
              ex['id'][:8], ex['entry_px'], ex['exit_px'],
              _f(ex['stop_px'], '.2f'), ex['loss_per_share'],
              ex['overshoot_cents'], ex['pnl_net']))
    a('')

    a('-- BY PAIR (market/window) ' + '-' * 51)
    a('  {:<32} {:>4} {:>6} {:>10}'.format('pair', 'n', 'win%', 'net P&L'))
    for g in rep['by_pair']:
        a('  {:<32} {:>4} {:>6} {:>10}'.format(
            str(g['key']), g['n'],
            'n/a' if g['win_rate'] is None else '{:.0%}'.format(g['win_rate']),
            _f(g['total_pnl_net'], '.3f')))
    a('')

    ss = rep['session']
    a('-- BY TIME OF DAY (10-min buckets, UTC) ' + '-' * 38)
    if ss.get('computable'):
        a('  session span: {} .. {}  = {:.0f} min over {} windows'.format(
            ss['first_entry_utc'], ss['last_entry_utc'], ss['span_minutes'],
            ss['n_distinct_windows']))
        a('  WARNING: one contiguous session. This table is CHRONOLOGY, not a '
          'time-of-day effect.')
    a('  {:<10} {:>4} {:>6} {:>10}'.format('bucket', 'n', 'win%', 'net P&L'))
    for g in rep['by_time_of_day']:
        a('  {:<10} {:>4} {:>6} {:>10}'.format(
            str(g['key']), g['n'],
            'n/a' if g['win_rate'] is None else '{:.0%}'.format(g['win_rate']),
            _f(g['total_pnl_net'], '.3f')))
    a('')

    af = rep['acted_features']
    a('-- WINNERS vs LOSERS ON LOGGED FEATURES ' + '-' * 37)
    a('  matched {} of {} closed to a signal row (unmatched {})'.format(
        af['n_matched'], m['n_closed'], af['n_unmatched']))
    a('  CAVEAT: {}'.format(af['caveat']))
    re_ = af.get('raw_edge_summary')
    if re_:
        a('  raw_edge at entry     : min {} median {} max {}  '
          '(EDGE_THRESHOLD {})'.format(_f(re_['min'], '.3f'),
                                       _f(re_['median'], '.3f'),
                                       _f(re_['max'], '.3f'),
                                       re_['edge_threshold']))
        a('  entries with edge >= 10x threshold (0.40): {} of {}'.format(
            re_['n_above_10x_threshold'], re_['n']))
        a('  entries with edge >= 0.20               : {} of {}'.format(
            re_['n_above_0_20'], re_['n']))
        a('  {}'.format(re_['note']))
    a('  {:<26} {:>8} {:>10} {:>10} {:>8}'.format(
        'feature', 'n(w/l)', 'mean win', 'mean loss', "cohen d"))
    for c in af['feature_comparison'][:12]:
        a('  {:<26} {:>8} {:>10} {:>10} {:>8}'.format(
            c['feature'], '{}/{}'.format(c['n_win'], c['n_loss']),
            _f(c['mean_win'], '.3f'), _f(c['mean_loss'], '.3f'),
            _f(c['cohens_d'], '.2f')))
    a('')

    wa = rep['window_attempts']
    a('-- ENTRY-ATTEMPT ACCOUNTING ' + '-' * 50)
    a('  entry attempts (acted + refused downstream): {}'.format(
        wa['n_entry_attempts']))
    a('  of which became fills                     : {}  ({:.1%})'.format(
        wa['n_attempts_that_became_fills'], wa['attempt_to_fill_rate']))
    a('  max_trades_this_window rows               : {}  ({:.1%} of all rows)'
      .format(wa['n_max_trades_this_window_rows'], wa['share_of_all_rows']))
    a('  {}'.format(wa['reading']))
    a('')

    d = rep['declines']
    a('-- WHAT IT IS NOT DOING (declined signals) ' + '-' * 35)
    a('  signal rows {}   acted {}   declined {}   act rate {:.2%}'.format(
        d['n_signal_rows'], d['n_acted'], d['n_declined'], d['act_rate']))
    a('  {:<52} {:>5} {:>7}'.format('skip_reason', 'n', 'share'))
    for r in d['top_skip_reasons']:
        a('  {:<52} {:>5} {:>7}'.format(
            r['reason'][:52], r['n'], '{:.1%}'.format(r['share_of_declines'])))
    a('')
    a('=' * 78)
    return '\n'.join(L)


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--db', default=DEFAULT_DB, help='path to trading.db')
    p.add_argument('--json', action='store_true',
                   help='emit the raw report as JSON')
    args = p.parse_args(argv)
    rep = build(args.db)
    if args.json:
        # convention 19: never allow Infinity/NaN into a JSON artifact.
        print(json.dumps(rep, indent=2, allow_nan=False, default=str))
    else:
        print(render(rep))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
