"""What a `portfolio_drawdown` breach is MADE of (proposal 049, D-380 R2).

048 says the drawdown RATIO is composed of incompatible parts. This says that
even with a perfectly composed ratio, a fixed LEVEL on it carries little
information about this book, because an accumulating quantity with a stationary
negative drift reaches any fixed level as a function of how long the process has
been left running. This module is the instrument that grades that claim. It
decides nothing.

Read-only. One database at a time (convention 32). No pooling of maker with
taker fills, no pooling across databases, and - rule 4 - no counterfactual.

## What it reports

1. **The epoch.** Epoch boundaries are process restarts, derived from
   `equity_snapshots` (see `rebase_equity`), never hand-entered. Per epoch:
   start, snapshot-derived duration, close span, realised `sum(pnl_net)`,
   realised USD/hour, the full-hour mean and standard deviation, the implied
   hours-to-limit at the epoch's own mean rate, and the distance in sigma
   between the limit and the epoch-mean path at the same uptime.
2. **Cluster counts beside every close count.** 046: a market-side resolves
   ONCE, so ~22 shares keyed to it are one draw observed 22 times. Every figure
   here prints the number of independent MARKET-SIDES beside the share count.
   A close count printed alone is the defect 046 was filed for, so this module
   has no code path that prints one.
3. **Composition** by `strategy_id` and by `exit_reason`, with LOSERS and
   WINNERS as separate subtotals. A net of -367.65 built from -2,085.05 against
   +1,717.39 is a different book from one that drifted quietly to -367.65, and
   only the separate subtotals show the difference.

## What it must never do (049 rules 3, 4, 5)

- It must NOT compute a book-without-strategy-X number. The composition is an
  ACCOUNTING decomposition, labelled as one everywhere it is printed. Removing
  a strategy would change cash, the entry sequence, which signals reach a cap
  and therefore what every other strategy did, so "-193.97 of the -367.65" does
  NOT license "the halt would not have fired without it". There is deliberately
  no flag that produces that number.
- No strategy may read this module, the sigma, or the drawdown. Same rule as
  043 rule 8, 038 rule 6, 042 rule 7, 048 rule 7.
- It recommends nothing about resuming a book, about `max_drawdown_frac`, or
  about a restart.

## The sigma is first-order and wrong in a knowable direction

The hour sums are treated as independent to get `sd * sqrt(hours)`. They are
not independent - hours are clustered by market-side (046) - so the true sigma
is LARGER and every sigma printed here is an upper bound on how anomalous
anything looks. The direction is the point: the conclusion 049 draws survives
the correction, because a larger sigma only moves a breach closer to the middle
of the book's own distribution. Every sigma is labelled first-order at the
point of printing, not only here.

## Live caveat, recorded rather than discovered later (D-380 recording note)

`shadow_loop.SHADOW_RISK_LIMITS` sets `max_drawdown_frac=1.0` (D-359 / A-17,
auto-halt disabled in shadow) and `EquityState.drawdown_frac()` is bounded
above by 1.0, so the `portfolio_drawdown` constraint CANNOT fire on any shadow
book today. The enriched breach payload this module feeds is therefore correct
but DORMANT in shadow; it goes live only under the real-money `DEFAULT_LIMITS`
(0.25) or if a future ruling lowers the shadow limit. `--limit-frac` is read
from the risk module rather than hardcoded so this report can never quote a
limit nobody is running (convention 25). `shadow_limit_note()` prints the live
shadow value, read from the source, not from this docstring.

## Usage

    env -u PYTHONPATH .venv/bin/python backtest/drawdown_attribution.py \\
        --db db/trading.db [--limit-frac 0.40] [--all-epochs] [--json]
"""
import argparse
import json
import math
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.risk import constraints as risk_constraints  # noqa: E402
# The single definition of a market-side's side. `positions` has no
# `outcome_side` column and `positions.pair` is the MARKET, not the side, so
# the side comes from the signal's `features_json` and from nowhere else.
# Imported rather than copied: two reporters that disagree about what a
# market-side IS would produce two different cluster counts for one book.
from backtest.settlement_coverage import (  # noqa: E402
    _outcome_side as outcome_side, open_ro)

#: Milliseconds in an hour. `positions.opened_ts` / `closed_ts` and
#: `equity_snapshots.ts` are all MILLISECONDS; `market_tape.ts` is seconds and
#: is not read here.
MS_PER_HOUR = 3600.0 * 1000.0

#: Rule 4, printed wherever the composition is printed.
ACCOUNTING_LABEL = (
    'ACCOUNTING DECOMPOSITION, NOT A COUNTERFACTUAL: these subtotals add up to '
    'the epoch net and nothing more. Removing a strategy would change cash, '
    'the entry sequence and which signals reached a cap, so a share of the '
    'loss does NOT license a claim about what the book would have done '
    'without it')

#: Named at the point of printing, not only in the module docstring.
SIGMA_LABEL = (
    'FIRST-ORDER: hour sums are treated as independent to get sd*sqrt(h). '
    'Hours are clustered by market-side (046), so the true sigma is LARGER '
    'and this figure is an UPPER bound on how anomalous the breach looks')

#: D-353 R2. A sweep books the full premium as a loss at exit 0.00 after a
#: process death. Those bookings are real money out of the paper book and are
#: counted, but they are a RESTART ARTIFACT rather than strategy performance,
#: so they are named separately wherever the epoch total is printed.
ORPHAN_REASON = 'orphaned:process_death'

#: The kill condition's rollback test: the reporter's realised USD/hour must
#: agree with a direct one-line SQL over the same rows to within this.
SELF_CHECK_TOLERANCE_USD_PER_HOUR = 0.01

#: 049's bar. After this many recorded breaches across both books, if EVERY
#: breach reads under `KILL_SIGMA_CONFIRM` from its own epoch's mean path, the
#: level limit carries no anomaly information on this book and a RATE test is
#: the correct instrument - which is then a live proposal for Raven and Aym,
#: not a claim this module makes.
KILL_MIN_BREACHES = 5
KILL_SIGMA_CONFIRM = 1.0
#: At or beyond this, the thesis is WRONG and 049 is TESTED_FAILED: a level
#: limit that catches genuine excursions is exactly what it is named after.
KILL_SIGMA_REFUTE = 2.0


# ---------------------------------------------------------------------------
# Epochs, derived from the data
# ---------------------------------------------------------------------------

def rebase_equity(conn, mode='paper'):
    """The value `get_equity()` re-bases to on restart, DERIVED from the table.

    Never hand-entered and never read from a launcher flag: a restart epoch is
    derived from the data or it is not derived at all (convention 25). The
    signature is that a fresh process reports one exact equity value that the
    previous process did not, and it reports it again on every subsequent
    restart - so the re-base value is the modal TARGET of an equity change.
    Ordinary equity values are floats carrying cents of P&L and essentially
    never repeat; the re-base value repeats once per restart.

    Returns `(value, occurrences)`, or `(None, 0)` when the table holds fewer
    than two rows or no value repeats - which is "we could not derive an
    epoch", never "there is one epoch" (convention 11).
    """
    rows = conn.execute(
        'SELECT equity FROM equity_snapshots WHERE mode = ? ORDER BY ts',
        (mode,)).fetchall()
    counts = {}
    previous = None
    for (equity,) in rows:
        value = float(equity)
        if previous is not None and abs(value - previous) > 1e-9:
            key = round(value, 6)
            counts[key] = counts.get(key, 0) + 1
        previous = value
    if not counts:
        return None, 0
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0]))
    if best[1] < 2:
        # One change target seen once is a normal P&L tick, not a re-base.
        return None, 0
    return best[0], best[1]


def epochs(conn, mode='paper', rebase=None):
    """Every process epoch in `equity_snapshots`, oldest first.

    An epoch STARTS at the first snapshot of a process: the first row overall,
    or any row whose equity is exactly the re-base value when the previous row
    was something else. The direction of the jump is deliberately not tested -
    a book that peaked above the re-base value falls TO it on restart, and a
    rule that only looked for jumps up would silently merge those two epochs.

    Each epoch is `{index, start_ts, end_ts, snapshots, first_equity,
    rebase_derived}` with `end_ts=None` on the current one. Returns `[]` only
    when the table is empty.

    **`rebase_derived=False` is a caveat, not a pass.** A book that has never
    restarted shows no re-base signature, so the whole series is returned as
    ONE epoch - which is the useful answer, because a fresh book is exactly
    when a first drawdown can fire. But it is an assumption, not a
    measurement: if that book DID restart and happened to re-base to a value
    the series never repeated, this merges two epochs into one and every
    uptime below is an OVER-estimate. Callers print the flag; they do not
    silently treat it as a derived boundary.

    KNOWN SLOP, named rather than hidden: snapshots are written every
    `DEFAULT_EQUITY_SNAPSHOT_SEC` (300s), so `start_ts` is the first snapshot
    of the process and can trail the true process start by up to that interval.
    Every uptime here is therefore a lower bound by up to five minutes.
    """
    if rebase is None:
        rebase, _ = rebase_equity(conn, mode)
    derived = rebase is not None
    rows = conn.execute(
        'SELECT ts, equity FROM equity_snapshots WHERE mode = ? ORDER BY ts',
        (mode,)).fetchall()
    if not rows:
        return []
    found = []
    previous = None
    for ts, equity in rows:
        value = float(equity)
        is_start = previous is None or (
            derived and abs(value - rebase) < 1e-9
            and abs(previous - rebase) > 1e-9)
        if is_start:
            found.append({'index': len(found), 'start_ts': int(ts),
                          'end_ts': None, 'snapshots': 0,
                          'first_equity': value, 'rebase_derived': derived})
        found[-1]['snapshots'] += 1
        previous = value
    for earlier, later in zip(found, found[1:]):
        earlier['end_ts'] = later['start_ts']
    return found


def _epoch_window(epoch):
    """`(start_ms, end_ms_or_None)` for a SQL range on `closed_ts`."""
    return int(epoch['start_ts']), (
        None if epoch['end_ts'] is None else int(epoch['end_ts']))


# ---------------------------------------------------------------------------
# Cluster counting (046)
# ---------------------------------------------------------------------------

def _closes(conn, epoch, mode='paper'):
    """Closed positions whose realisation landed IN `epoch`, with side keys.

    Keyed on `closed_ts`, not `opened_ts`: this module measures the P&L that
    hit the book DURING the epoch, and a position opened before a restart and
    swept after it really did debit the later book.

    Each row is `(strategy_id, exit_reason, pnl_net, pair, outcome_side)` with
    `outcome_side` None where the signal row or its `features_json` cannot
    supply one. Those are counted and named by every caller (convention 20),
    never dropped, because a position that cannot be KEYED still moved money.
    """
    start, end = _epoch_window(epoch)
    sql = ('SELECT p.strategy_id, p.exit_reason, p.pnl_net, p.pair, '
           's.features_json '
           'FROM positions p LEFT JOIN signals s ON s.id = p.signal_id '
           'WHERE p.closed_ts IS NOT NULL AND p.pnl_net IS NOT NULL '
           'AND p.mode = ? AND p.closed_ts >= ?')
    params = [mode, start]
    if end is not None:
        sql += ' AND p.closed_ts < ?'
        params.append(end)
    out = []
    for strategy_id, reason, pnl_net, pair, features_json in conn.execute(
            sql, params):
        out.append((strategy_id, reason, float(pnl_net), pair,
                    outcome_side(features_json)))
    return out


def _cluster_summary(rows):
    """`{closes, market_sides, unkeyed_closes, usd}` over `_closes` rows.

    `market_sides` is the count of distinct `(pair, outcome_side)` keys - the
    independent unit under 046. `unkeyed_closes` is how many of `closes` could
    not contribute a key; it is reported beside the cluster count so nobody
    reads a small `market_sides` as a small book when it is really a book with
    missing signal rows.
    """
    sides = set()
    unkeyed = 0
    usd = 0.0
    for _strategy, _reason, pnl_net, pair, side in rows:
        usd += pnl_net
        if pair and side:
            sides.add((str(pair), str(side)))
        else:
            unkeyed += 1
    return {'closes': len(rows), 'market_sides': len(sides),
            'unkeyed_closes': unkeyed, 'usd': round(usd, 4)}


# ---------------------------------------------------------------------------
# The rate, the clock and the sigma
# ---------------------------------------------------------------------------

def _hourly_buckets(conn, epoch, mode='paper'):
    """Realised P&L per FULL wall-clock hour of the epoch, oldest first.

    A partial trailing hour is excluded: a mean and an sd taken over buckets of
    unequal width would understate the sd, and the sd is what every sigma here
    divides by.
    """
    start, end = _epoch_window(epoch)
    sql = ('SELECT closed_ts, pnl_net FROM positions '
           'WHERE closed_ts IS NOT NULL AND pnl_net IS NOT NULL '
           'AND mode = ? AND closed_ts >= ?')
    params = [mode, start]
    if end is not None:
        sql += ' AND closed_ts < ?'
        params.append(end)
    buckets = {}
    latest = start
    for closed_ts, pnl_net in conn.execute(sql, params):
        index = int((int(closed_ts) - start) // MS_PER_HOUR)
        buckets[index] = buckets.get(index, 0.0) + float(pnl_net)
        latest = max(latest, int(closed_ts))
    complete = int((latest - start) // MS_PER_HOUR)
    return [round(buckets.get(i, 0.0), 4) for i in range(complete)]


def _mean_sd(values):
    """`(mean, sample sd, n)`. sd is None below two observations - one point
    has no spread, and reporting 0.0 would make every sigma infinite."""
    n = len(values)
    if n == 0:
        return None, None, 0
    mean = sum(values) / n
    if n < 2:
        return mean, None, n
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    return mean, math.sqrt(variance), n


def epoch_stats(conn, epoch, limit_frac, mode='paper'):
    """Uptime, rate, hourly distribution, hours-to-limit and sigma for `epoch`.

    `limit_frac` is a fraction of the epoch's own re-base equity, so the limit
    is expressed against the same denominator the running process uses. Every
    returned figure is point-in-time: all three books are live under read.
    """
    rows = _closes(conn, epoch, mode)
    summary = _cluster_summary(rows)
    orphans = _cluster_summary([r for r in rows if r[1] == ORPHAN_REASON])

    start, end = _epoch_window(epoch)
    close_times = conn.execute(
        'SELECT MIN(closed_ts), MAX(closed_ts) FROM positions '
        'WHERE closed_ts IS NOT NULL AND pnl_net IS NOT NULL AND mode = ? '
        'AND closed_ts >= ?' + ('' if end is None else ' AND closed_ts < ?'),
        [mode, start] + ([] if end is None else [end])).fetchone()
    first_close, last_close = close_times if close_times else (None, None)

    close_span_h = None
    if first_close is not None and last_close is not None:
        close_span_h = (int(last_close) - int(first_close)) / MS_PER_HOUR
    snapshot_span_h = None
    if end is not None:
        snapshot_span_h = (end - start) / MS_PER_HOUR
    elif last_close is not None:
        snapshot_span_h = (int(last_close) - start) / MS_PER_HOUR

    # 049 defines the realised rate over the CLOSE span, which is also what the
    # kill condition's rollback test pins it to. The snapshot span is reported
    # beside it because they are different facts: the first names how long the
    # book was TRADING, the second how long the process was UP.
    rate = None
    if close_span_h and close_span_h > 0:
        rate = summary['usd'] / close_span_h

    hourly = _hourly_buckets(conn, epoch, mode)
    mean, sd, hours = _mean_sd(hourly)

    rebase = epoch['first_equity']
    limit_usd = abs(rebase * limit_frac)

    # The clock: at the epoch's OWN mean hourly rate, when does an accumulating
    # loss reach the limit? None when the mean is non-negative - a book that is
    # not losing never arrives, and a negative or infinite "hours" would read
    # as a forecast rather than as "does not apply".
    hours_to_limit = None
    if mean is not None and mean < 0:
        hours_to_limit = limit_usd / abs(mean)

    # The sigma: how far the LIMIT sits beyond the epoch's own mean path at the
    # observed uptime. This is 049's headline figure and the one a breach is
    # read against. FIRST-ORDER (see SIGMA_LABEL).
    sigma_at_limit = None
    sigma_observed = None
    if sd is not None and sd > 0 and hours > 0 and mean is not None:
        expected = mean * hours
        sd_sum = sd * math.sqrt(hours)
        sigma_at_limit = ((-limit_usd) - expected) / sd_sum
        sigma_observed = (summary['usd'] - expected) / sd_sum

    return {
        'epoch_index': epoch['index'],
        'start_ts': start,
        'end_ts': end,
        'is_current': end is None,
        'rebase_equity': rebase,
        'rebase_derived': epoch.get('rebase_derived', True),
        'snapshots': epoch['snapshots'],
        'snapshot_span_hours': _round(snapshot_span_h, 4),
        'close_span_hours': _round(close_span_h, 4),
        'closes': summary['closes'],
        'market_sides': summary['market_sides'],
        'unkeyed_closes': summary['unkeyed_closes'],
        'realised_usd': summary['usd'],
        'realised_usd_per_hour': _round(rate, 4),
        'orphan_closes': orphans['closes'],
        'orphan_market_sides': orphans['market_sides'],
        'orphan_usd': orphans['usd'],
        'full_hours': hours,
        'hourly_mean_usd': _round(mean, 4),
        'hourly_sd_usd': _round(sd, 4),
        'hourly_usd': hourly,
        'limit_frac': limit_frac,
        'limit_usd': round(limit_usd, 4),
        'hours_to_limit': _round(hours_to_limit, 4),
        'sigma_at_limit': _round(sigma_at_limit, 4),
        'sigma_observed': _round(sigma_observed, 4),
        'sigma_note': SIGMA_LABEL,
    }


def _round(value, places):
    return None if value is None else round(value, places)


# ---------------------------------------------------------------------------
# Composition (049 rules 4 and 5)
# ---------------------------------------------------------------------------

def _group(rows, key_index, net):
    """Subtotal `rows` by one column, with cluster counts and net share.

    Returns `(entries, losers, winners)`. Losers and winners are SEPARATE
    subtotals, never only the net (rule 5). `share_of_net` is omitted - left
    None - when the epoch net is zero or has the opposite sign to the group:
    a "share" of a net the group is pulling AGAINST is a number that reads
    like an attribution and is not one.
    """
    grouped = {}
    for row in rows:
        grouped.setdefault(row[key_index] or 'unnamed', []).append(row)
    entries = []
    for name, group_rows in grouped.items():
        summary = _cluster_summary(group_rows)
        share = None
        if net and summary['usd'] and (summary['usd'] < 0) == (net < 0):
            share = round(summary['usd'] / net, 4)
        entries.append({'name': name, 'closes': summary['closes'],
                        'market_sides': summary['market_sides'],
                        'unkeyed_closes': summary['unkeyed_closes'],
                        'usd': summary['usd'], 'share_of_net': share})
    entries.sort(key=lambda e: e['usd'])
    losers = _cluster_summary([r for r in rows if r[2] < 0])
    winners = _cluster_summary([r for r in rows if r[2] > 0])
    return entries, losers, winners


def composition(conn, epoch, mode='paper'):
    """Realised P&L by `strategy_id` and by `exit_reason` for `epoch`.

    An ACCOUNTING decomposition and nothing else - see `ACCOUNTING_LABEL`,
    which is carried in the returned dict so it travels with the numbers into
    `--json` output too. There is deliberately no code path here that computes
    a book-without-strategy-X figure (rule 4).
    """
    rows = _closes(conn, epoch, mode)
    net = round(sum(r[2] for r in rows), 4)
    by_strategy, s_losers, s_winners = _group(rows, 0, net)
    by_reason, r_losers, r_winners = _group(rows, 1, net)
    return {
        'label': ACCOUNTING_LABEL,
        'net_usd': net,
        'by_strategy': by_strategy,
        'by_exit_reason': by_reason,
        'loss_channels': r_losers,
        'win_channels': r_winners,
        'strategy_losers': s_losers,
        'strategy_winners': s_winners,
    }


# ---------------------------------------------------------------------------
# The kill condition's rollback test
# ---------------------------------------------------------------------------

def self_check(conn, epoch, stats, mode='paper'):
    """049's rollback test, evaluated rather than described.

    The reporter aggregates P&L through a join, a python accumulation and an
    hourly bucketing. This recomputes the same rate with one SQL statement over
    the same rows. A disagreement beyond
    `SELF_CHECK_TOLERANCE_USD_PER_HOUR` means the reporting path is dropping or
    double-counting rows - typically a join that lost positions with no signal
    - and the measurement must be ROLLED BACK, not explained.

    `agrees` is None when the rate is unmeasurable (an epoch with fewer than
    two closes has no span). That is "could not run", never "passed".
    """
    start, end = _epoch_window(epoch)
    row = conn.execute(
        'SELECT SUM(pnl_net), MIN(closed_ts), MAX(closed_ts), COUNT(*) '
        'FROM positions WHERE closed_ts IS NOT NULL AND pnl_net IS NOT NULL '
        'AND mode = ? AND closed_ts >= ?'
        + ('' if end is None else ' AND closed_ts < ?'),
        [mode, start] + ([] if end is None else [end])).fetchone()
    total, first_close, last_close, count = row
    direct_rate = None
    if (first_close is not None and last_close is not None
            and int(last_close) > int(first_close)):
        span_h = (int(last_close) - int(first_close)) / MS_PER_HOUR
        direct_rate = float(total) / span_h
    reported = stats.get('realised_usd_per_hour')
    if direct_rate is None or reported is None:
        agrees, delta = None, None
    else:
        delta = abs(reported - direct_rate)
        agrees = delta <= SELF_CHECK_TOLERANCE_USD_PER_HOUR
    return {
        'reported_usd_per_hour': reported,
        'direct_usd_per_hour': _round(direct_rate, 6),
        'delta_usd_per_hour': _round(delta, 6),
        'tolerance_usd_per_hour': SELF_CHECK_TOLERANCE_USD_PER_HOUR,
        'agrees': agrees,
        'direct_closes': int(count or 0),
        'reported_closes': stats.get('closes'),
        'closes_agree': int(count or 0) == stats.get('closes'),
    }


# ---------------------------------------------------------------------------
# Recorded breaches, and the grading bar
# ---------------------------------------------------------------------------

def recorded_breaches(conn):
    """Every `portfolio_drawdown` row already on this book's `risk_events`.

    Reads the enriched fields where they exist and reports them as absent where
    they do not: the enrichment is forward-only, so historical breaches have no
    sigma and saying so is the honest answer (convention 11).
    """
    rows = conn.execute(
        "SELECT id, ts, details_json FROM risk_events "
        "WHERE json_extract(details_json, '$.constraint') = ? ORDER BY ts",
        (risk_constraints.CONSTRAINT_DRAWDOWN,)).fetchall()
    out = []
    for row_id, ts, details_json in rows:
        try:
            details = json.loads(details_json)
        except (ValueError, TypeError):
            details = {}
        out.append({
            'id': row_id, 'ts': int(ts),
            'event': details.get('event'),
            'drawdown_frac': details.get('drawdown_frac'),
            'limit_frac': details.get('limit_frac'),
            'sigma_observed': details.get('sigma_observed'),
            'hours_to_limit': details.get('hours_to_limit'),
            'enriched': 'sigma_observed' in details,
        })
    return out


def grade(breaches):
    """049's bar, evaluated. NOT_TESTED until `KILL_MIN_BREACHES` are enriched.

    Only ENRICHED breaches can be graded: a breach with no sigma is not a
    breach that read low, it is one that was never measured. Counting it either
    way would be the "absence of a number is a zero" mistake convention 11
    exists to stop.
    """
    graded = [b for b in breaches
              if b['enriched'] and b.get('sigma_observed') is not None]
    sigmas = [abs(float(b['sigma_observed'])) for b in graded]
    if len(graded) < KILL_MIN_BREACHES:
        verdict = 'NOT_TESTED'
        reason = ('{0} enriched breach(es) of the {1} the bar requires'
                  .format(len(graded), KILL_MIN_BREACHES))
    elif any(s >= KILL_SIGMA_REFUTE for s in sigmas):
        verdict = 'TESTED_FAILED'
        reason = ('a breach read at or beyond {0} sigma - the level limit is '
                  'discriminating and 049 is wrong'.format(KILL_SIGMA_REFUTE))
    elif all(s < KILL_SIGMA_CONFIRM for s in sigmas):
        verdict = 'CONFIRMED'
        reason = ('every breach read under {0} sigma from its own epoch mean '
                  'path; a RATE test is the correct instrument and that is a '
                  'proposal for Raven and Aym, not a change made here'
                  .format(KILL_SIGMA_CONFIRM))
    else:
        verdict = 'NOT_TESTED'
        reason = 'breaches sit between {0} and {1} sigma - keep counting'.format(
            KILL_SIGMA_CONFIRM, KILL_SIGMA_REFUTE)
    return {'verdict': verdict, 'reason': reason,
            'enriched_breaches': len(graded), 'total_breaches': len(breaches),
            'sigmas': [round(s, 4) for s in sigmas]}


# ---------------------------------------------------------------------------
# The two fields the breach payload gains (049 hold 3)
# ---------------------------------------------------------------------------

def breach_payload_fields(conn, limit_frac, mode='paper'):
    """`{sigma_observed, hours_to_limit, ...}` for the CURRENT epoch, or `{}`.

    This is the whole of what `engine/risk/events.py` adds to a
    `portfolio_drawdown` payload. It returns a dict rather than raising on a
    thin book: a breach must be RECORDED whatever the reporter can or cannot
    say about it, so an unmeasurable epoch contributes no keys instead of
    blocking the write.

    Everything here is informational. Nothing in it may ever be read back by a
    gate, a strategy or a halt decision (049 rule 3).
    """
    found = epochs(conn, mode)
    if not found:
        return {}
    stats = epoch_stats(conn, found[-1], limit_frac, mode)
    fields = {
        'sigma_observed': stats['sigma_observed'],
        'sigma_at_limit': stats['sigma_at_limit'],
        'hours_to_limit': stats['hours_to_limit'],
        'epoch_uptime_hours': stats['close_span_hours'],
        'epoch_realised_usd': stats['realised_usd'],
        'epoch_realised_usd_per_hour': stats['realised_usd_per_hour'],
        'epoch_closes': stats['closes'],
        'epoch_market_sides': stats['market_sides'],
        'sigma_note': SIGMA_LABEL,
    }
    return {k: v for k, v in fields.items() if v is not None}


def shadow_limit_note():
    """The live shadow `max_drawdown_frac`, READ FROM SOURCE, or a failure.

    Printed with every report because a hours-to-limit figure computed against
    a limit nobody is running is a number that will be quoted (convention 25).
    """
    try:
        from engine.polymarket.shadow_loop import SHADOW_RISK_LIMITS
    except Exception as exc:                      # pragma: no cover - defensive
        return 'shadow limit UNREADABLE ({0})'.format(exc)
    frac = SHADOW_RISK_LIMITS.max_drawdown_frac
    if frac >= 1.0:
        return ('shadow max_drawdown_frac reads {0} from source: '
                'drawdown_frac() is bounded above by 1.0, so the '
                'portfolio_drawdown constraint CANNOT fire on a shadow book '
                'and the enriched payload is DORMANT there (D-359 / A-17). '
                'Real-money DEFAULT_LIMITS is {1}.'
                .format(frac, risk_constraints.DEFAULT_LIMITS.max_drawdown_frac))
    return ('shadow max_drawdown_frac reads {0} from source; real-money '
            'DEFAULT_LIMITS is {1}'
            .format(frac, risk_constraints.DEFAULT_LIMITS.max_drawdown_frac))


# ---------------------------------------------------------------------------
# Report assembly and CLI
# ---------------------------------------------------------------------------

def report(conn, limit_frac, mode='paper', all_epochs=False):
    """The whole report for ONE database. Never pooled with another."""
    rebase, occurrences = rebase_equity(conn, mode)
    found = epochs(conn, mode, rebase)
    if not found:
        return {'rebase_equity': rebase, 'rebase_occurrences': occurrences,
                'epochs': [], 'current': None, 'composition': None,
                'self_check': None, 'rebase_derived': False,
                'reason': 'equity_snapshots holds no rows for this mode, so '
                          'no epoch exists to report on',
                'breaches': [], 'grade': grade([]),
                'shadow_limit_note': shadow_limit_note()}
    wanted = found if all_epochs else [found[-1]]
    stats = [epoch_stats(conn, e, limit_frac, mode) for e in wanted]
    current = epoch_stats(conn, found[-1], limit_frac, mode)
    breaches = recorded_breaches(conn)
    return {
        'rebase_equity': rebase,
        'rebase_occurrences': occurrences,
        'rebase_derived': rebase is not None,
        'epoch_count': len(found),
        'epochs': stats,
        'current': current,
        'composition': composition(conn, found[-1], mode),
        'self_check': self_check(conn, found[-1], current, mode),
        'breaches': breaches,
        'grade': grade(breaches),
        'shadow_limit_note': shadow_limit_note(),
    }


def _fmt(value, spec='%.4f'):
    return 'n/a' if value is None else spec % value


def format_report(out, db_path):
    """Human lines. Every close count is printed WITH its cluster count."""
    lines = ['DATABASE  {0}   (this book only - never pooled, convention 32)'
             .format(db_path)]
    lines.append('NOTE      {0}'.format(out['shadow_limit_note']))
    if out.get('reason'):
        lines.append('NOTE      {0}'.format(out['reason']))
        return lines
    if out['rebase_derived']:
        lines.append('EPOCHS    {0} derived; re-base equity {1} seen {2}x'
                     .format(out['epoch_count'],
                             _fmt(out['rebase_equity'], '%.2f'),
                             out['rebase_occurrences']))
    else:
        lines.append('EPOCHS    1 ASSUMED - no restart signature in '
                     'equity_snapshots. If this book DID restart to a value '
                     'the series never repeats, two epochs are merged here '
                     'and every uptime below is an OVER-estimate.')
    for stats in out['epochs']:
        lines.extend(_format_epoch(stats))
    check = out['self_check']
    lines.append('SELFCHECK rate reported {0} vs direct {1}, delta {2} '
                 '(tolerance {3})  [{4}]'.format(
                     _fmt(check['reported_usd_per_hour']),
                     _fmt(check['direct_usd_per_hour']),
                     _fmt(check['delta_usd_per_hour'], '%.6f'),
                     check['tolerance_usd_per_hour'],
                     'AGREE' if check['agrees'] else
                     ('UNMEASURABLE' if check['agrees'] is None
                      else 'ROLLBACK')))
    lines.append('          closes reported {0} vs direct {1}  [{2}]'.format(
        check['reported_closes'], check['direct_closes'],
        'AGREE' if check['closes_agree'] else 'ROLLBACK'))
    comp = out['composition']
    lines.append('')
    lines.append('COMPOSITION of the current epoch, net {0}'.format(
        _fmt(comp['net_usd'], '%.2f')))
    lines.append('          {0}'.format(comp['label']))
    lines.append('  loss channels  {closes} closes / {market_sides} '
                 'market-sides  {usd}'.format(**comp['loss_channels']))
    lines.append('  win  channels  {closes} closes / {market_sides} '
                 'market-sides  {usd}'.format(**comp['win_channels']))
    for title, key in (('by exit_reason', 'by_exit_reason'),
                       ('by strategy_id', 'by_strategy')):
        lines.append('  {0}:'.format(title))
        for entry in comp[key]:
            lines.append('    {0:<48} {1:>6} closes / {2:>5} sides  '
                         '{3:>12}  {4}'.format(
                             entry['name'][:48], entry['closes'],
                             entry['market_sides'], entry['usd'],
                             'n/a' if entry['share_of_net'] is None
                             else '%.1f%% of net' % (100 * entry['share_of_net'])))
    g = out['grade']
    lines.append('')
    lines.append('BREACHES  {0} recorded, {1} enriched  [{2}]  {3}'.format(
        g['total_breaches'], g['enriched_breaches'], g['verdict'], g['reason']))
    for breach in out['breaches']:
        lines.append('  {0}  drawdown {1}  sigma {2}  hours_to_limit {3}  '
                     '{4}'.format(
                         breach['ts'], _fmt(breach['drawdown_frac']),
                         _fmt(breach['sigma_observed']),
                         _fmt(breach['hours_to_limit']),
                         'enriched' if breach['enriched'] else 'PRE-ENRICHMENT'))
    return lines


def _format_epoch(stats):
    tag = 'CURRENT' if stats['is_current'] else 'epoch %d' % stats['epoch_index']
    return [
        '',
        'EPOCH {0}  start_ts {1}  re-base {2}'.format(
            tag, stats['start_ts'], _fmt(stats['rebase_equity'], '%.2f')),
        '  uptime      close span {0} h / snapshot span {1} h '
        '(snapshot span is a LOWER bound, 300s snapshot interval)'.format(
            _fmt(stats['close_span_hours'], '%.2f'),
            _fmt(stats['snapshot_span_hours'], '%.2f')),
        '  realised    {0} USD over {1} closes / {2} market-sides '
        '({3} unkeyed)'.format(
            _fmt(stats['realised_usd'], '%.2f'), stats['closes'],
            stats['market_sides'], stats['unkeyed_closes']),
        '  of which    {0} orphan closes / {1} market-sides booked at '
        'D-353 R2, {2} USD (restart artifact, not strategy performance)'
        .format(stats['orphan_closes'], stats['orphan_market_sides'],
                _fmt(stats['orphan_usd'], '%.2f')),
        '  rate        {0} USD/h  (full hours {1}, mean {2}, sd {3})'.format(
            _fmt(stats['realised_usd_per_hour'], '%.2f'), stats['full_hours'],
            _fmt(stats['hourly_mean_usd'], '%.2f'),
            _fmt(stats['hourly_sd_usd'], '%.2f')),
        '  limit       frac {0} = {1} USD off the re-base'.format(
            stats['limit_frac'], _fmt(stats['limit_usd'], '%.2f')),
        '  clock       hours to limit at this epoch mean: {0}'.format(
            _fmt(stats['hours_to_limit'], '%.2f')),
        '  sigma       limit sits {0} sigma beyond the mean path; observed '
        'sits {1}'.format(_fmt(stats['sigma_at_limit'], '%.3f'),
                          _fmt(stats['sigma_observed'], '%.3f')),
        '              {0}'.format(SIGMA_LABEL),
    ]


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Attribute a portfolio_drawdown breach to uptime, rate '
                    'and composition (proposal 049, D-380 R2). Read-only, one '
                    'database at a time, no counterfactual.')
    parser.add_argument('--db', default='db/trading.db')
    parser.add_argument(
        '--limit-frac', type=float,
        default=risk_constraints.DEFAULT_LIMITS.max_drawdown_frac,
        help='drawdown fraction the clock counts down to. Defaults to the '
             'real-money DEFAULT_LIMITS value read from engine.risk.'
             'constraints, never a constant typed here (convention 25).')
    parser.add_argument('--mode', default='paper')
    parser.add_argument('--all-epochs', action='store_true')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    if not (0 < args.limit_frac <= 1.0):
        print('--limit-frac must be in (0, 1]', file=sys.stderr)
        return 2
    if not os.path.exists(args.db):
        print('no such database: {0}'.format(args.db), file=sys.stderr)
        return 2

    conn = open_ro(args.db)
    out = report(conn, args.limit_frac, args.mode, args.all_epochs)
    if args.json:
        print(json.dumps({'db': args.db, **out}, indent=2, default=str))
        return 0
    for line in format_report(out, args.db):
        print(line)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
