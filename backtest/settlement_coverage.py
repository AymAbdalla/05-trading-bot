"""Coverage and agreement for the settlement resolution ledger (proposal 038).

This is the ACCEPTANCE TEST for the repair, and it is deliberately a
`backtest/` tool rather than anything the loop imports. Rule 6: the only
consumers of `market_resolutions` are `backtest/` and
`agents/forge_shadow_eval.py`. A resolution record exists at window CLOSE,
after every entry and exit decision for that window, so a strategy reading it
is look-ahead or a bug.

## The two numbers, and why they are separate

**Coverage** (038's kill condition, rule 2). Over 200 or more closed positions
booked AFTER the ledger went live, resolution must be available from the ledger
for 99% or more of the distinct `(pair, outcome_side)` market-sides those
positions touch. Reported as a fraction with BOTH numerator and denominator
(convention 20). Baseline before the repair: 325/864 = 37.6% at 2026-08-19
11:30 UTC. Below 95% is FAILED and reverted, not tuned. Backfilled rows do NOT
count - `LIVE_SOURCES` is the default filter here for exactly that reason.

**Agreement** (038's kill condition, second clause). Wherever the ledger and
the old sibling inference BOTH have an answer, they must agree. The required
disagreement rate is 0.00, and a single disagreement means one of the two is
wrong and the ledger cannot be trusted until it is known which.

That check is only worth anything because the two are INDEPENDENT reads. The
ledger writes `source='venue'` from the CLOB's `tokens[].winner` field; the
sibling inference is recovered from `positions.exit_px`, which the paper
adapter set from Gamma's `outcomePrices` via `prices.resolution_price`.
Different endpoint, different field, different failure modes.

## The sibling inference, and its bias

Resolution is recoverable today only from a position on the same market-side
held to settlement - `exit_reason` is the bare 'stop' (0.00) or 'target' (1.00)
rather than a distinct settlement reason, and everything sold early is prefixed
'sell:'. `outcome_side` is not a column; it lives in
`signals.features_json`, reached through `positions.signal_id`.

The method is SOUND - of the pairs where both sides were independently
recovered, every one shows exactly one side at 1.00, which is the arithmetic a
binary must satisfy - and BIASED, one way. A winning side is sold early by
`profit_target` and leaves no settlement row; a losing side rots to 0.00 and
records one. Measured 2026-08-19: 29.9% of singly-recovered sides settled 1.00
against a ~50% unbiased benchmark. Never use this map as a sample. It is here
to be CHECKED AGAINST, and to be backfilled from under its own marked source.

## The counterfactual (proposal 043, `--counterfactual`)

A THIRD mode, beside coverage/agreement and beside `--backfill`. It answers one
question per exit reason: what price did the exit take, and what was that same
market-side worth at resolution? `--backfill` WRITES `market_resolutions`; this
READS it, and the two deliberately do not share a code path, because a
reporting run that can write its own inputs is not a measurement (043 rule 1).
`main()` refuses the two flags in one invocation for the same reason.

The join is `market_resolutions.market_slug = positions.pair` AND
`market_resolutions.outcome_side = signals.features_json -> outcome_side`, with
`positions.signal_id = signals.id`. BOTH halves are required: `positions.pair`
identifies the MARKET, and a market has two sides that resolve oppositely, so a
join on slug alone scores half the book against the wrong outcome and does it
silently. There is NO `market_slug` key in `features_json`, and the first
attempt at this join used one and matched 0 of 195. `counterfactual()` RAISES
`ZeroMatchError` rather than printing an empty table (rule 3, convention 20).
An absent ledger, an empty one and a book with no market-side keys are
NOT_TESTED instead - could not run, never ran and found nothing.

Reported PER EXIT REASON and never pooled across them, and never across the two
databases (rule 10, convention 32). Pooling is not a presentation choice: an
exit named for the condition that triggers it cannot be graded on the P&L of
the positions it triggered on, so `sell:profit_target` and `sell:price_stop`
are populations selected on opposite conditions and their average means nothing
about either. Every figure carries its MATCH RATE as numerator and denominator
(rule 5) - a counterfactual on a matched subset whose match rate is not
reported is proposal 041's censoring error wearing a different hat.

The break-even is an IDENTITY, not an estimate, and that is what makes this
admissible under D-342 R5: a share sold at price s returns s, a share held
returns 1.00 times the settle rate, so holding wins if and only if the realised
settle rate exceeds the share-weighted mean of s. No probability model, no fair
value and no calibration enters anywhere, and none may be added.

Every figure is quoted with the instrument's own error bar. Rule 6's self-check
runs on EVERY invocation, over positions whose `exit_px` is exactly 0.00 or
1.00 - settlements the position recorded independently of the ledger, through
Gamma's `outcomePrices` rather than the CLOB's `winner` field. Above
`SELF_CHECK_MAX_DISAGREEMENT_RATE` the counterfactual is NOT_TESTED whatever it
says, because at that rate the instrument is measuring itself.

Nothing outside `backtest/` and `agents/forge_shadow_eval.py` may read this.
Resolution is knowable only after the window closes, so a strategy consuming it
is look-ahead by construction (043 rule 8, 038 rule 6).

## Contradictions

A market-side carrying BOTH 0.00 and 1.00 is arithmetically impossible for one
side of one binary. Two exist, and they are 038's named first test cases:

    sol-updown-5m-1787056800 / Up
    btc-updown-5m-1787134200 / Down

They are reported as `contradictory`, never resolved to a value, and never
backfilled. Picking one would be a coin flip wearing a measurement's clothes.
"""
import argparse
import json
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.polymarket.resolution_ledger import (  # noqa: E402
    LIVE_SOURCES, SOURCE_SIBLING_INFERENCE_BACKFILL, ensure_schema,
    resolution_row_for, table_exists, write_resolutions)

#: `exit_reason` values that mean SETTLEMENT rather than a sale. Everything the
#: loop sells is prefixed 'sell:'; settlement alone is bare. Keying on the
#: reason AND on the price, because either one alone has a false positive: a
#: sale can land exactly at 0.00, and a bare reason could in principle be
#: written by a future path that is not settlement.
SETTLEMENT_REASONS = ('stop', 'target')

#: The only two prices a binary can settle at.
SETTLEMENT_PRICES = (0.0, 1.0)


def open_ro(db_path):
    """Read-only connection. The live loop holds this file open in WAL."""
    return sqlite3.connect('file:{0}?mode=ro'.format(db_path), uri=True)


def sibling_inference_map(conn, since_ms=None):
    """`(pair, outcome_side)` -> 1.0, 0.0, or None where CONTRADICTORY.

    Returns `(resolved, report)`. `resolved` holds only the market-sides with
    exactly one settlement price observed; a side seen at both 0.00 and 1.00 is
    left OUT of it and listed in `report['contradictory']` instead, because
    picking one of two impossible answers is a coin flip that looks like a
    measurement.

    `report['touched']` is every distinct market-side any closed position
    touched, recoverable or not - it is the DENOMINATOR of the coverage
    fraction, and it is counted here rather than re-derived so the numerator
    and denominator can never come from two different queries.

    `since_ms` filters on `positions.opened_ts`, which is in MILLISECONDS. The
    ledger's own `window_ts` and `resolved_ts` are in SECONDS. Mixing them is a
    factor-of-1000 error that produces an empty result and looks like a market
    that was never traded.
    """
    sql = ('SELECT p.pair, p.exit_px, p.exit_reason, s.features_json '
           'FROM positions p LEFT JOIN signals s ON s.id = p.signal_id '
           'WHERE p.closed_ts IS NOT NULL')
    params = []
    if since_ms is not None:
        sql += ' AND p.opened_ts >= ?'
        params.append(int(since_ms))

    observed = {}
    report = {'closed_positions': 0, 'no_outcome_side': 0,
              'touched': set(), 'contradictory': []}
    for pair, exit_px, reason, features_json in conn.execute(sql, params):
        report['closed_positions'] += 1
        side = None
        if features_json:
            try:
                side = json.loads(features_json).get('outcome_side')
            except (ValueError, TypeError, AttributeError):
                side = None
        if not side or not pair:
            # No signal row, no features_json, or no outcome_side on it. The
            # market-side key itself is unavailable, so this position cannot
            # even be COUNTED in the denominator, let alone resolved. Named
            # and counted rather than dropped (convention 20).
            report['no_outcome_side'] += 1
            continue
        key = (str(pair), str(side))
        report['touched'].add(key)
        if reason in SETTLEMENT_REASONS and exit_px in SETTLEMENT_PRICES:
            observed.setdefault(key, set()).add(float(exit_px))

    resolved = {}
    for key, prices in observed.items():
        if len(prices) == 1:
            resolved[key] = next(iter(prices))
        else:
            report['contradictory'].append(
                {'market_slug': key[0], 'outcome_side': key[1],
                 'prices': sorted(prices)})
    report['recoverable'] = len(resolved)
    report['touched_count'] = len(report['touched'])
    report['settled_one'] = sum(1 for v in resolved.values() if v == 1.0)
    return resolved, report


def coverage(conn, since_ms=None, sources=LIVE_SOURCES):
    """038's kill condition, as a fraction with numerator AND denominator.

    The denominator is every distinct `(pair, outcome_side)` a closed position
    touched in the window; the numerator is how many of those the LEDGER can
    answer. `sources` defaults to `LIVE_SOURCES`, which excludes
    `sibling_inference_backfill` - rule 4 measures coverage on markets FETCHED
    AFTER the ledger landed, and letting recovered history count toward it
    would let the repair pass on data that predates it.

    Reports `verdict` against 038's own thresholds and NOT_TESTED below the
    200-closed-position floor, because convention 11 says NOT_TESTED means
    "could not run", never "ran and found nothing" - and 038 says explicitly:
    do not grade a partial sample.
    """
    _resolved, report = sibling_inference_map(conn, since_ms)
    touched = report['touched']
    if not table_exists(conn):
        # The ledger has not landed in THIS database yet. That is NOT_TESTED,
        # and saying so is the whole point: reporting it as 0/889 would read
        # like a recorder that ran and found nothing.
        return {
            'closed_positions': report['closed_positions'],
            'no_outcome_side': report['no_outcome_side'],
            'numerator': None, 'denominator': len(touched),
            'fraction': None, 'sources': list(sources),
            'verdict': 'NOT_TESTED',
            'reason': 'market_resolutions table absent from this database; '
                      'the ledger activates at the next loop restart',
            'ledger_table_present': False,
            'missing_sample': [], 'missing_total': None,
            'baseline_note': '325/864 = 37.6% at 2026-08-19 11:30 UTC, '
                             'pre-repair',
        }
    covered, missing = 0, []
    for slug, side in sorted(touched):
        if resolution_row_for(conn, slug, side, sources) is not None:
            covered += 1
        else:
            missing.append({'market_slug': slug, 'outcome_side': side})
    denominator = len(touched)
    fraction = (float(covered) / denominator) if denominator else None
    if report['closed_positions'] < 200:
        verdict = 'NOT_TESTED'
    elif fraction is None:
        verdict = 'NOT_TESTED'
    elif fraction >= 0.99:
        verdict = 'PASS'
    elif fraction >= 0.95:
        verdict = 'INCOMPLETE'
    else:
        verdict = 'FAILED'
    return {
        'closed_positions': report['closed_positions'],
        'no_outcome_side': report['no_outcome_side'],
        'numerator': covered,
        'denominator': denominator,
        'fraction': fraction,
        'sources': list(sources) if sources is not None else None,
        'ledger_table_present': True,
        'verdict': verdict,
        'missing_sample': missing[:20],
        'missing_total': len(missing),
        'baseline_note': '325/864 = 37.6% at 2026-08-19 11:30 UTC, pre-repair',
    }


def disagreements(conn, sources=LIVE_SOURCES):
    """Where the ledger and the sibling inference BOTH answer, do they agree?

    Returns a dict carrying the overlap size, the disagreement list and the
    rate. 038 requires the rate to be exactly 0.00; anything above it means one
    of the two reads is wrong and the ledger cannot be trusted until it is
    known which. The check has teeth only because the two are independent
    reads - see the module docstring.

    Contradictory market-sides are reported separately and are NOT counted as
    disagreements: they are a fault in the inference, not evidence about the
    ledger, and pooling them would let a known-bad input manufacture a failure.
    """
    inferred, report = sibling_inference_map(conn)
    if not table_exists(conn):
        return {'overlap': 0, 'disagreements': [], 'disagreement_count': 0,
                'rate': None, 'required_rate': 0.0, 'verdict': 'NOT_TESTED',
                'reason': 'market_resolutions table absent from this database',
                'contradictory_inference': report['contradictory']}
    overlap, bad = 0, []
    for (slug, side), inferred_px in sorted(inferred.items()):
        row = resolution_row_for(conn, slug, side, sources)
        if row is None or row['resolved_px'] is None:
            continue
        overlap += 1
        if float(row['resolved_px']) != float(inferred_px):
            bad.append({'market_slug': slug, 'outcome_side': side,
                        'ledger_px': row['resolved_px'],
                        'ledger_source': row['source'],
                        'inferred_px': inferred_px})
    return {
        'overlap': overlap,
        'disagreements': bad,
        'disagreement_count': len(bad),
        'rate': (float(len(bad)) / overlap) if overlap else None,
        'required_rate': 0.0,
        'verdict': ('NOT_TESTED' if not overlap
                    else ('PASS' if not bad else 'FAILED')),
        'contradictory_inference': report['contradictory'],
    }


def backfill(conn, dry_run=True):
    """Write the recoverable history under `sibling_inference_backfill`.

    PERMITTED by rule 4 and MARKED, so the recovered market-sides are not lost
    and can still never be mistaken for observations. These rows are excluded
    from `coverage()` by default and must never count toward the kill
    condition's number.

    `INSERT OR IGNORE` inside `write_resolutions` means this can never
    overwrite a live `venue` row. Contradictory market-sides are skipped and
    counted: a side seen at both 0.00 and 1.00 has no value to write.

    Default is `dry_run=True`. This writes into a database the shadow loop
    holds open, so making the destructive direction the one you have to ask for
    is the point.
    """
    inferred, report = sibling_inference_map(conn)
    rows = [(slug, side, px, None)
            for (slug, side), px in sorted(inferred.items())]
    result = {
        'candidates': len(rows),
        'contradictory_skipped': len(report['contradictory']),
        'source': SOURCE_SIBLING_INFERENCE_BACKFILL,
        'dry_run': bool(dry_run),
        'written': 0,
    }
    if dry_run:
        return result
    ensure_schema(conn)
    result['written'] = write_resolutions(
        conn, rows, SOURCE_SIBLING_INFERENCE_BACKFILL)
    return result


# -- the counterfactual (proposal 043) ---------------------------------------

#: Everything the loop SELLS carries this prefix; settlement is written as the
#: bare `stop`/`target` reason. Only a sale has a counterfactual: a settled
#: position was never sold, so its `exit_px` IS the resolution price and the
#: comparison degenerates into the ledger checking itself.
EARLY_EXIT_PREFIX = 'sell:'

#: The ONE exit reason 043 grades. `sell:salvage_floor` is 100%
#: PM_fair_value_settlement_exit in both databases, so the graded arm is that
#: one strategy. Every other reason is reported as CONTEXT and marked not
#: gradeable - the proposal asks a question about the salvage floor, and
#: grading whatever else happens to be in the table is scanning.
KILL_GRADED_EXIT_REASON = 'sell:salvage_floor'

#: Matched positions required before the salvage question is graded at all.
#: The reading that MOTIVATED 043 was 59 matched positions and the proposal
#: explicitly forbids grading those 59.
KILL_MIN_MATCHED = 400

#: The verdict band, per share. Not a taste: 4x the 0.0025 net directional
#: bias measured against positions with known outcomes, so a verdict either
#: way survives the ledger being wrong at four times the rate we can currently
#: demonstrate it is wrong at.
KILL_BAND = 0.010

#: Above this share-weighted self-check disagreement rate the counterfactual
#: is NOT_TESTED whatever it says, because at that rate the instrument is
#: measuring itself rather than the book (043 rule 6).
SELF_CHECK_MAX_DISAGREEMENT_RATE = 0.0500

#: How long the instrument may run short of KILL_MIN_MATCHED before the
#: experiment is recorded NOT_TESTED and requeued rather than left open.
KILL_REQUEUE_DAYS = 14.0

#: The self-check at 043's snapshot, carried so a later run can see whether
#: the instrument's own error bar is moving rather than inherit it from a
#: document (convention 25).
SELF_CHECK_BASELINE_NOTE = ('25 of 2016 shares, 1.24%, split 15 against 10, '
                            'at the 2026-08-19 snapshot')

#: Rule 7. Stated in every report, because it is what makes the comparison
#: admissible under D-342 R5 at all.
BREAK_EVEN_IDENTITY = (
    'holding beats exiting at price s if and only if the realised settle rate '
    'on those shares exceeds the share-weighted mean of s; a share sold at s '
    'returns s, a share held returns 1.00 times the settle rate')

BREAK_EVEN_NO_MODEL = (
    'no probability model, no fair value and no calibration enters this '
    'measurement and none may be added to it: the payoff difference is an '
    'identity in two RECORDED prices, not the output of a forecaster')

#: Named so the reader does not discover it as a defect. Settlement redemption
#: charges no exit fee while a sale is charged one, so a taker fee biases the
#: comparison AGAINST the early exit by roughly one fee per share. It runs
#: against the result rather than producing it, and the default fee is 0.0.
FEE_INCIDENCE_NOTE = (
    'the entry cost is common to both arms and cancels. A taker fee applies '
    'to the sale and NOT to settlement redemption, so any non-zero fee makes '
    'the early exit look worse than measured here, never better')


class ZeroMatchError(RuntimeError):
    """The ledger holds rows and the join matched none of them.

    Raised rather than returned, because an empty counterfactual printed as a
    table is indistinguishable from a real null result. The first attempt at
    this join keyed on a `market_slug` field in `signals.features_json` that
    does not exist and matched 0 of 195 while reporting itself fine. A silent
    zero is a missing number (convention 20).

    NOT raised when the ledger is absent, when it holds no rows from the
    requested sources, or when no closed position carries a market-side key.
    Those are NOT_TESTED - could not run, never ran and found nothing.
    """


def _outcome_side(features_json):
    """The side, from `features_json` and from nowhere else.

    `positions` has no `outcome_side` column and `positions.pair` is the
    MARKET, not the side. A market has two sides that resolve oppositely, so
    the side has to come from the signal or half the book scores against the
    wrong outcome.
    """
    if not features_json:
        return None
    try:
        return json.loads(features_json).get('outcome_side')
    except (ValueError, TypeError, AttributeError):
        return None


def _ledger_map(conn, sources):
    """`(market_slug, lowered outcome_side)` -> `resolved_px`, for `sources`.

    Read as ONE query and held in memory: the join runs over thousands of
    positions and a per-position lookup would issue a SELECT each.

    Rows whose `resolved_px` is NULL are left OUT entirely. NULL means NOT
    RECORDED, and admitting it as 0.00 would convert every unrecorded market
    into a realised loss - the same failure `resolution_for` refuses on the
    read path, reached through a report instead.
    """
    sql = ('SELECT market_slug, outcome_side, resolved_px '
           'FROM market_resolutions WHERE resolved_px IS NOT NULL')
    params = []
    if sources is not None:
        listed = [str(s) for s in sources]
        if not listed:
            return {}
        sql += ' AND source IN ({0})'.format(','.join('?' * len(listed)))
        params.extend(listed)
    out = {}
    for slug, side, px in conn.execute(sql, params):
        if slug is None or side is None:
            continue
        out[(str(slug), str(side).lower())] = float(px)
    return out


def _empty_self_check(reason):
    """The rule-6 block with nothing in it, so the KEYS are always present.

    A report that omits its own error bar when it could not compute one reads
    like a report that did not need one.
    """
    return dict(
        positions=0, shares=0.0, disagreeing_shares=0.0, rate=None,
        ledger_loss_position_settled_win_shares=0.0,
        ledger_win_position_settled_loss_shares=0.0,
        ledger_px_not_binary_shares=0.0,
        net_directional_bias=None,
        max_rate=SELF_CHECK_MAX_DISAGREEMENT_RATE,
        verdict='NOT_TESTED', reason=reason,
        baseline_note=SELF_CHECK_BASELINE_NOTE,
        strict=dict(positions=0, shares=0.0, disagreeing_shares=0.0,
                    rate=None))


def _finish_self_check(acc, strict, reason=None):
    """Turn the accumulators into the reported block.

    Share-weighted, not position-weighted: a disagreement on a 200-share
    position and one on a 1-share position are not the same amount of wrong,
    and every other figure in this report is in shares.
    """
    block = _empty_self_check(reason)
    for key in ('positions', 'shares', 'disagreeing_shares'):
        block[key] = acc[key]
    block['ledger_loss_position_settled_win_shares'] = acc['settled_win']
    block['ledger_win_position_settled_loss_shares'] = acc['settled_loss']
    block['ledger_px_not_binary_shares'] = acc['not_binary']
    block['strict'] = dict(
        positions=strict['positions'], shares=strict['shares'],
        disagreeing_shares=strict['disagreeing_shares'],
        rate=((strict['disagreeing_shares'] / strict['shares'])
              if strict['shares'] else None))
    if not acc['shares']:
        return block
    block['rate'] = acc['disagreeing_shares'] / acc['shares']
    block['net_directional_bias'] = (
        (acc['settled_win'] - acc['settled_loss']) / acc['shares'])
    block['verdict'] = (
        'FAILED' if block['rate'] > SELF_CHECK_MAX_DISAGREEMENT_RATE
        else 'PASS')
    block['reason'] = None
    return block


def _kill_verdict(rows_by_reason, self_check, ledger_span_days):
    """043's kill condition, graded on `sell:salvage_floor` and nothing else.

    The self-check OVERRIDES the result in one direction only: above
    SELF_CHECK_MAX_DISAGREEMENT_RATE the answer is NOT_TESTED however clean
    the margin looks. A self-check that could not run does NOT block the
    verdict - it is reported alongside it, because refusing to grade until an
    independent settlement happens to overlap would make the grade hostage to
    a population the experiment does not control.
    """
    row = rows_by_reason.get(KILL_GRADED_EXIT_REASON)
    verdict = dict(
        graded_exit_reason=KILL_GRADED_EXIT_REASON,
        matched=(0 if row is None else row['matched']),
        required_matched=KILL_MIN_MATCHED,
        band=KILL_BAND,
        mean_exit_px=(None if row is None else row['mean_exit_px']),
        realised_settle_rate=(None if row is None
                              else row['realised_settle_rate']),
        margin=None,
        self_check_verdict=self_check['verdict'],
        self_check_rate=self_check['rate'],
        ledger_span_days=ledger_span_days,
        requeue_after_days=KILL_REQUEUE_DAYS,
        verdict='NOT_TESTED', reason=None)
    if self_check['verdict'] == 'FAILED':
        verdict['reason'] = (
            'the ledger self-check disagrees on %.4f of settled shares, above '
            'the %.4f ceiling; at that rate the instrument is measuring '
            'itself, so the counterfactual is NOT_TESTED whatever it says'
            % (self_check['rate'], SELF_CHECK_MAX_DISAGREEMENT_RATE))
        return verdict
    if verdict['matched'] < KILL_MIN_MATCHED:
        verdict['reason'] = (
            '%d matched %s positions against the %d the kill condition '
            'requires; 043 forbids grading the partial sample'
            % (verdict['matched'], KILL_GRADED_EXIT_REASON,
               KILL_MIN_MATCHED))
        if ledger_span_days is not None and ledger_span_days >= (
                KILL_REQUEUE_DAYS):
            verdict['reason'] += (
                '; the ledger has been running %.1f days, past the %.1f-day '
                'requeue clause, so record NOT_TESTED and requeue'
                % (ledger_span_days, KILL_REQUEUE_DAYS))
        return verdict
    margin = row['realised_settle_rate'] - row['mean_exit_px']
    verdict['margin'] = margin
    if margin >= KILL_BAND:
        verdict['verdict'] = 'NEGATIVE'
        verdict['reason'] = (
            'the realised settle rate exceeds the mean salvage price by '
            '%.4f, at or past the %.4f band: holding beat salvaging. Record '
            'the answer as NEGATIVE. This is NOT authority to change the '
            'floor - rule 0 sends that to a separate proposal'
            % (margin, KILL_BAND))
    elif -margin >= KILL_BAND:
        verdict['verdict'] = 'CONFIRMED'
        verdict['reason'] = (
            'the mean salvage price exceeds the realised settle rate by '
            '%.4f, at or past the %.4f band: salvaging beat holding, and the '
            'hold-through direction is closed for this exit'
            % (-margin, KILL_BAND))
    else:
        verdict['verdict'] = 'INCONCLUSIVE'
        verdict['reason'] = (
            'the two prices differ by %.4f, inside the %.4f band, which is '
            '4x the demonstrated ledger bias: recorded as INCONCLUSIVE, not '
            'as a result in either direction' % (abs(margin), KILL_BAND))
    return verdict


def _ledger_span_days(conn, sources):
    """How long the ledger has been recording, in days, or None.

    Measured from the ledger's own `resolved_ts` column rather than from the
    wall clock, so the number is a property of the data and two runs over the
    same database agree.
    """
    sql = ('SELECT MIN(resolved_ts), MAX(resolved_ts) '
           'FROM market_resolutions WHERE resolved_ts IS NOT NULL')
    params = []
    if sources is not None:
        listed = [str(s) for s in sources]
        if not listed:
            return None
        sql += ' AND source IN ({0})'.format(','.join('?' * len(listed)))
        params.extend(listed)
    low, high = conn.execute(sql, params).fetchone()
    if low is None or high is None:
        return None
    return (float(high) - float(low)) / 86400.0


def counterfactual(conn, since_ms=None, sources=LIVE_SOURCES):
    """What each exit took, against what that market-side turned out worth.

    READS `market_resolutions`; writes nothing, anywhere, ever. It shares no
    code path with `backfill()`, which WRITES it - a reporting run that can
    write its own inputs is not a measurement (043 rule 1).

    Reported PER EXIT REASON and never pooled across them. Pooling is not a
    presentation choice here: `sell:profit_target` fires because a position is
    up and `sell:price_stop` fires because it is down, so a pooled delta is
    the average of two populations selected on opposite conditions and means
    nothing about either.

    Every figure carries its MATCH RATE as numerator and denominator (rule 5).
    A counterfactual computed on a matched subset whose match rate is not
    reported is proposal 041's censoring error wearing a different hat.

    RAISES `ZeroMatchError` when the ledger holds rows, positions carry keys,
    and the join matches none of them. Returns NOT_TESTED - never a zero - for
    an absent ledger, an empty one, or a book with no market-side keys.

    `since_ms` filters `positions.opened_ts`, which is MILLISECONDS. The
    ledger's `window_ts` and `resolved_ts` are SECONDS. Mixing them is a
    factor-of-1000 error that empties the result and looks like a market that
    was never traded.
    """
    result = dict(
        sources=(list(sources) if sources is not None else None),
        since_ms=(None if since_ms is None else int(since_ms)),
        ledger_table_present=False, ledger_rows=0, ledger_span_days=None,
        status='NOT_TESTED', reason=None,
        closed_positions=0, keyed_positions=0, no_outcome_side=0,
        matched_positions=0, unpriceable_positions=0,
        by_exit_reason=[],
        self_check=_empty_self_check('the counterfactual did not run'),
        break_even=dict(identity=BREAK_EVEN_IDENTITY,
                        no_model=BREAK_EVEN_NO_MODEL,
                        fee_incidence=FEE_INCIDENCE_NOTE),
        verdict=dict(graded_exit_reason=KILL_GRADED_EXIT_REASON,
                     matched=0, required_matched=KILL_MIN_MATCHED,
                     band=KILL_BAND, mean_exit_px=None,
                     realised_settle_rate=None, margin=None,
                     self_check_verdict='NOT_TESTED', self_check_rate=None,
                     ledger_span_days=None,
                     requeue_after_days=KILL_REQUEUE_DAYS,
                     verdict='NOT_TESTED',
                     reason='the counterfactual did not run'))

    if not table_exists(conn):
        result['reason'] = (
            'market_resolutions is absent from this database, so there is no '
            'ledger to counterfactual against. NOT_TESTED means could not '
            'run, never ran and found nothing (convention 11)')
        return result
    result['ledger_table_present'] = True
    ledger = _ledger_map(conn, sources)
    result['ledger_rows'] = len(ledger)
    result['ledger_span_days'] = _ledger_span_days(conn, sources)

    sql = ('SELECT p.exit_reason, p.pair, p.exit_px, p.qty, s.features_json '
           'FROM positions p LEFT JOIN signals s ON s.id = p.signal_id '
           'WHERE p.closed_ts IS NOT NULL')
    params = []
    if since_ms is not None:
        sql += ' AND p.opened_ts >= ?'
        params.append(int(since_ms))

    buckets = {}
    acc = dict(positions=0, shares=0.0, disagreeing_shares=0.0,
               settled_win=0.0, settled_loss=0.0, not_binary=0.0)
    strict = dict(positions=0, shares=0.0, disagreeing_shares=0.0)

    for reason, pair, exit_px, qty, features_json in conn.execute(sql, params):
        # A NULL exit_reason on a CLOSED position is a missing number, not an
        # empty string to be pooled with the rest (convention 20).
        label = reason if reason else '(null exit_reason)'
        bucket = buckets.get(label)
        if bucket is None:
            bucket = dict(closed=0, keyed=0, matched=0, no_outcome_side=0,
                          unpriceable=0, shares=0.0, proceeds=0.0,
                          realised=0.0)
            buckets[label] = bucket
        bucket['closed'] += 1
        result['closed_positions'] += 1

        side = _outcome_side(features_json)
        if not side or not pair:
            bucket['no_outcome_side'] += 1
            result['no_outcome_side'] += 1
            continue
        bucket['keyed'] += 1
        result['keyed_positions'] += 1

        resolved_px = ledger.get((str(pair), str(side).lower()))
        if resolved_px is None:
            continue
        if qty is None or exit_px is None:
            # Resolution is known but the money is not. Counted under its own
            # name rather than defaulted to zero shares, which would silently
            # shrink the denominator of every per-share figure.
            bucket['unpriceable'] += 1
            result['unpriceable_positions'] += 1
            continue

        shares = float(qty)
        price = float(exit_px)
        bucket['matched'] += 1
        result['matched_positions'] += 1
        bucket['shares'] += shares
        bucket['proceeds'] += price * shares
        if resolved_px >= 1.0:
            bucket['realised'] += shares

        if price in SETTLEMENT_PRICES:
            # Rule 6. This position settled INDEPENDENTLY of the ledger: the
            # paper adapter took the price from Gamma's outcomePrices, the
            # ledger reads the CLOB's winner field. Different endpoint,
            # different field, different failure modes - so where they
            # overlap they are a real check and not a tautology.
            acc['positions'] += 1
            acc['shares'] += shares
            settlement_reason = reason in SETTLEMENT_REASONS
            if settlement_reason:
                strict['positions'] += 1
                strict['shares'] += shares
            if resolved_px != price:
                acc['disagreeing_shares'] += shares
                if settlement_reason:
                    strict['disagreeing_shares'] += shares
                if resolved_px not in SETTLEMENT_PRICES:
                    acc['not_binary'] += shares
                elif resolved_px == 0.0:
                    acc['settled_win'] += shares
                else:
                    acc['settled_loss'] += shares

    if not ledger:
        result['reason'] = (
            'market_resolutions holds no rows with a recorded price from '
            'sources %s. There is no join to get wrong yet, so this is '
            'NOT_TESTED and not a keying fault' % (result['sources'],))
        return result
    if not result['keyed_positions']:
        result['reason'] = (
            'no closed position in this window carries a market-side key: '
            '%d closed, %d without an outcome_side on their signal. The key '
            'is unavailable, so the join could not run'
            % (result['closed_positions'], result['no_outcome_side']))
        return result
    if not result['matched_positions']:
        raise ZeroMatchError(
            'the counterfactual join matched 0 of %d keyed closed positions '
            'against %d ledger rows. Both halves of the key are required: '
            'market_resolutions.market_slug = positions.pair AND '
            'market_resolutions.outcome_side = json_extract('
            'signals.features_json, %s). There is NO market_slug key in '
            'features_json; keying on one matched 0 of 195 the first time '
            'this was built. A silent zero is a missing number '
            '(convention 20)'
            % (result['keyed_positions'], len(ledger),
               repr('$.outcome_side')))

    result['self_check'] = _finish_self_check(acc, strict)
    rows_by_reason = {}
    for label in sorted(buckets, key=lambda k: (-buckets[k]['matched'], k)):
        bucket = buckets[label]
        shares = bucket['shares']
        early_exit = label.startswith(EARLY_EXIT_PREFIX)
        mean_exit_px = (bucket['proceeds'] / shares) if shares else None
        settle_rate = (bucket['realised'] / shares) if shares else None
        gradeable = (label == KILL_GRADED_EXIT_REASON
                     and bucket['matched'] >= KILL_MIN_MATCHED)
        if not early_exit:
            not_gradeable_reason = (
                'settlement, not a sale: exit_px IS the resolution price, so '
                'proceeds and realised value are the same quantity measured '
                'twice and the delta is ledger disagreement alone')
        elif label != KILL_GRADED_EXIT_REASON:
            not_gradeable_reason = (
                'reported as context; 043 grades %s and nothing else, and '
                'n=%d here is a record, not evidence about this exit'
                % (KILL_GRADED_EXIT_REASON, bucket['matched']))
        elif not gradeable:
            not_gradeable_reason = (
                '%d matched, below the %d the kill condition requires'
                % (bucket['matched'], KILL_MIN_MATCHED))
        else:
            not_gradeable_reason = None
        row = dict(
            exit_reason=label,
            early_exit=early_exit,
            closed=bucket['closed'],
            keyed=bucket['keyed'],
            matched=bucket['matched'],
            no_outcome_side=bucket['no_outcome_side'],
            unpriceable=bucket['unpriceable'],
            match_rate_numerator=bucket['matched'],
            match_rate_denominator=bucket['closed'],
            match_rate=((float(bucket['matched']) / bucket['closed'])
                        if bucket['closed'] else None),
            shares=shares,
            proceeds_usd=bucket['proceeds'],
            realised_value_usd=bucket['realised'],
            delta_usd=bucket['proceeds'] - bucket['realised'],
            mean_exit_px=mean_exit_px,
            realised_settle_rate=settle_rate,
            delta_per_share=(None if shares == 0
                             else mean_exit_px - settle_rate),
            gradeable=gradeable,
            not_gradeable_reason=not_gradeable_reason)
        result['by_exit_reason'].append(row)
        rows_by_reason[label] = row

    result['verdict'] = _kill_verdict(rows_by_reason, result['self_check'],
                                      result['ledger_span_days'])
    result['status'] = 'ok'
    return result


def format_counterfactual(report):
    """The report as lines, match rate first so no figure reads alone."""
    lines = []
    if report['status'] != 'ok':
        lines.append('COUNTERFACTUAL  [%s]  %s'
                     % (report['status'], report['reason']))
        return lines
    lines.append(
        'COUNTERFACTUAL  %d matched of %d keyed of %d closed, %d without an '
        'outcome_side, %d unpriceable; ledger rows %d, sources %s'
        % (report['matched_positions'], report['keyed_positions'],
           report['closed_positions'], report['no_outcome_side'],
           report['unpriceable_positions'], report['ledger_rows'],
           report['sources']))
    lines.append('BREAK-EVEN      %s' % report['break_even']['identity'])
    check = report['self_check']
    lines.append(
        'SELF-CHECK      %s of %s settled shares disagree = %s over %d '
        'positions  [%s]'
        % (check['disagreeing_shares'], check['shares'],
           'n/a' if check['rate'] is None else '%.4f' % check['rate'],
           check['positions'], check['verdict']))
    lines.append(
        '                ledger-loss/settled-win %s shares, '
        'ledger-win/settled-loss %s shares, net bias %s  (baseline %s)'
        % (check['ledger_loss_position_settled_win_shares'],
           check['ledger_win_position_settled_loss_shares'],
           'n/a' if check['net_directional_bias'] is None
           else '%.4f' % check['net_directional_bias'],
           check['baseline_note']))
    for row in report['by_exit_reason']:
        lines.append(
            '%-44s match %d/%d = %s'
            % (row['exit_reason'], row['match_rate_numerator'],
               row['match_rate_denominator'],
               'n/a' if row['match_rate'] is None
               else '%.1f%%' % (100 * row['match_rate'])))
        lines.append(
            '%-44s shares %.1f  sold %.2f  worth %.2f  delta %+.2f USD'
            % ('', row['shares'], row['proceeds_usd'],
               row['realised_value_usd'], row['delta_usd']))
        lines.append(
            '%-44s mean exit %s  settle rate %s  delta %s /share  %s'
            % ('',
               'n/a' if row['mean_exit_px'] is None
               else '%.4f' % row['mean_exit_px'],
               'n/a' if row['realised_settle_rate'] is None
               else '%.4f' % row['realised_settle_rate'],
               'n/a' if row['delta_per_share'] is None
               else '%+.4f' % row['delta_per_share'],
               'GRADEABLE' if row['gradeable']
               else 'NOT GRADEABLE: ' + row['not_gradeable_reason']))
    verdict = report['verdict']
    lines.append('VERDICT   %s: %d/%d matched  [%s]'
                 % (verdict['graded_exit_reason'], verdict['matched'],
                    verdict['required_matched'], verdict['verdict']))
    lines.append('          %s' % verdict['reason'])
    return lines


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='Coverage and agreement for the settlement resolution '
                    'ledger (Forge proposal 038).')
    parser.add_argument('--db', default='db/trading.db')
    parser.add_argument(
        '--since', type=float, default=None,
        help='Unix SECONDS. Only closed positions opened at or after this '
             'count. Pass the ledger changeover time T to measure the kill '
             'condition on markets fetched after the ledger landed.')
    parser.add_argument(
        '--backfill', action='store_true',
        help='WRITE the recoverable history under source '
             'sibling_inference_backfill. Requires a writable --db.')
    parser.add_argument(
        '--counterfactual', action='store_true',
        help='READ the ledger and report, per exit reason, what each exit '
             'took against what that market-side settled at (proposal 043). '
             'Read-only, and refused together with --backfill.')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    if args.counterfactual and args.backfill:
        print('REFUSED: --backfill WRITES market_resolutions and '
              '--counterfactual READS it. A reporting run that can write its '
              'own inputs is not a measurement (043 rule 1). They do not '
              'share a code path and they do not share an invocation.',
              file=sys.stderr)
        return 2

    since_ms = None if args.since is None else int(args.since * 1000)

    if args.counterfactual:
        conn = open_ro(args.db)
        try:
            report = counterfactual(conn, since_ms)
        except ZeroMatchError as exc:
            print('ZERO MATCH  %s' % exc, file=sys.stderr)
            return 2
        out = dict(db=args.db, since_sec=args.since, counterfactual=report)
        if args.json:
            print(json.dumps(out, indent=2, default=str))
        else:
            for line in format_counterfactual(report):
                print(line)
        return 0

    if args.backfill:
        conn = sqlite3.connect(args.db, timeout=15.0)
    else:
        conn = open_ro(args.db)

    out = {
        'db': args.db,
        'since_sec': args.since,
        'coverage': coverage(conn, since_ms),
        'agreement': disagreements(conn),
    }
    if args.backfill:
        out['backfill'] = backfill(conn, dry_run=False)

    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0

    cov = out['coverage']
    if cov.get('reason'):
        print('NOTE      {0}'.format(cov['reason']))
    print('COVERAGE  {0}/{1} = {2}  [{3}]'.format(
        'n/a' if cov['numerator'] is None else cov['numerator'],
        cov['denominator'],
        'n/a' if cov['fraction'] is None else '%.1f%%' % (100 * cov['fraction']),
        cov['verdict']))
    print('          closed positions {0}, no outcome_side {1}, sources {2}'
          .format(cov['closed_positions'], cov['no_outcome_side'],
                  cov['sources']))
    agr = out['agreement']
    print('AGREEMENT overlap {0}, disagreements {1}, rate {2}  [{3}]'.format(
        agr['overlap'], agr['disagreement_count'],
        'n/a' if agr['rate'] is None else '%.4f' % agr['rate'],
        agr['verdict']))
    for row in agr['disagreements']:
        print('   DISAGREE {market_slug} {outcome_side} ledger={ledger_px} '
              '({ledger_source}) inferred={inferred_px}'.format(**row))
    for row in agr['contradictory_inference']:
        print('   CONTRADICTORY INFERENCE {market_slug} {outcome_side} '
              '{prices}'.format(**row))
    if 'backfill' in out:
        print('BACKFILL  wrote {written} of {candidates} candidates '
              '(skipped {contradictory_skipped} contradictory) as '
              '{source}'.format(**out['backfill']))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
