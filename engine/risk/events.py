"""Recording side of the deterministic risk module (D-342 R2).

`engine/risk/constraints.py` decides; this module writes. The split is
deliberate: a pure evaluator can be tested exhaustively without a database, and
every side effect the risk path can have is in this one file.

Two side effects exist, and no others:

1. **Every denial writes a `risk_events` row.** Convention 20 - a silent
   `continue` is a missing number. The kill condition for this module is
   "no constraint binds more than 5 times in 30 days", and it is mechanically
   checkable only because the rows exist. `denials_by_constraint()` below IS
   that query; do not re-derive it by hand.

2. **A drawdown breach engages `engine.halt`** - the single definition of the
   kill switch. No second halt path, no env override, no config key. This
   module never defines "halted"; it calls `write_halt` and lets every other
   path read the same file.

## Why the halt write is conditional here and unconditional in the executor

`engine/executor.py:_trigger_auto_halt` writes unconditionally, because it runs
from a periodic backstop and an ops backstop firing during an existing halt
should still leave a record. This path runs PER ENTRY ATTEMPT, so an
unconditional write would rewrite the HALT file on every candidate for as long
as the drawdown persists - and each rewrite mints a new ack id, invalidating the
one a human is already holding. `write_halt`'s own docstring names this: callers
that must not clobber an existing halt check `is_halted()` first. The
`risk_events` row is still written every time, so the count is never lost.

## What this module deliberately does NOT do

It does not check `is_halted()` as an entry gate. The entry path already owns
that check, and a second copy is the failure mode `engine/halt.py` was written
to end. This module only ever ADDS a halt; it never re-decides one.
"""
import dataclasses
import json
import logging
import time
import uuid

from engine.halt import is_halted, write_halt
from engine.risk import constraints as C

logger = logging.getLogger(__name__)

#: The single `risk_events.type` value this module writes. One new type rather
#: than one per constraint: `type` is consumed by the dashboard reader and by
#: tests, so a narrow blast radius matters. The constraint name lives in
#: `details_json.constraint`, which is what the kill condition groups on.
RISK_EVENT_TYPE = 'risk_constraint'

#: The kill-condition threshold from PLAN section 5: a constraint that binds
#: this many times or fewer over the window is decorative.
DECORATIVE_BINDING_THRESHOLD = 5

#: The kill-condition window, in days.
KILL_CONDITION_WINDOW_DAYS = 30


def _ms():
    return int(time.time() * 1000)


def _drawdown_attribution(conn, decision):
    """Sigma and hours-to-limit for a drawdown breach, or `{}` (049 hold 3).

    D-380 R2 hold 3: a breach must be readable as "the rate changed" or "the
    clock ran out" AT THE MOMENT IT HAPPENS, not only in hindsight, because the
    numbers it needs - the epoch's own hourly distribution and its market-side
    counts - are cheapest to capture while the breach is happening.

    Three properties this deliberately has:

    1. **Informational only.** Nothing written here may ever be read back by a
       gate, a strategy or a halt decision (049 rule 3). It is a field on a
       record, not an input.
    2. **It cannot block the write.** Any failure - a missing table, a thin
       epoch, an import error under a partial checkout - returns `{}` and logs.
       A breach that went unrecorded because its *annotation* raised would be a
       far worse defect than a breach recorded without a sigma.
    3. **The import is lazy.** `backtest/` is analysis tooling and the risk
       path must not depend on it at import time; only a breach - which is
       rare, and today impossible in shadow - pays for it.
    """
    if decision.constraint != C.CONSTRAINT_DRAWDOWN:
        return {}
    try:
        from backtest.drawdown_attribution import breach_payload_fields
        limit_frac = (decision.detail or {}).get(
            'limit_frac', C.DEFAULT_LIMITS.max_drawdown_frac)
        return breach_payload_fields(conn, float(limit_frac))
    except Exception:
        logger.warning('risk: drawdown attribution unavailable for this '
                       'breach; the row is written without it', exc_info=True)
        return {}


#: Constraints a caller wants MEASURED rather than ENFORCED, per constraint
#: name. D-383 (Aym ruling 2026-08-20) puts the shadow book here: the shadow
#: `max_drawdown_frac` moved 1.0 -> 0.25 so the portfolio drawdown constraint
#: finally FIRES and feeds 049's attribution instrument, but D-383 R2 is
#: explicit that this is MEASUREMENT ONLY - "it does NOT halt trading. The book
#: still runs to $0 and re-funds per D-358."
#:
#: A naive limit change cannot deliver that, and the failure is silent. The
#: drawdown check is the FIRST check in `constraints.check`, so once a book is
#: past the limit EVERY entry attempt denies on drawdown, and
#: `evaluate_and_record` engages `engine.halt` on the first one. That HALT file
#: is process-wide and not per-database, so one book breaching would freeze
#: entries on ALL THREE shadow books. The book would look alive and enter
#: nothing.
#:
#: So "measure only" has to mean two things at once, and both are required:
#:
#:   1. NO HALT. The breach never reaches `engage_drawdown_halt`.
#:   2. NO REFUSAL. The breach is recorded and then STEPPED OVER, so the
#:      remaining constraints - per-trade, per-event, aggregate - decide the
#:      entry on their own merits. A measured constraint must not become an
#:      unenforced one for its NEIGHBOURS: an oversized order is still denied,
#:      and denied under its own name.
#:
#: Step-over is done by re-running the pure evaluator with the measured
#: constraint neutralised, which is why the neutraliser is a limits transform
#: rather than a flag inside `check`. `constraints.check` stays deterministic
#: and side-effect free (its own docstring promises that), and the real-money
#: path never sees any of this: `measure_only` defaults to empty, so
#: `DEFAULT_LIMITS` behaviour is byte-for-byte what it was.
_MEASURE_ONLY_NEUTRALISERS = {
    # +inf rather than 1.0: `drawdown_frac()` is bounded above by 1.0, and the
    # check is `drawdown > limit`, so 1.0 leaves a total wipeout unmeasurable
    # by exactly one edge case. Nothing can exceed +inf.
    C.CONSTRAINT_DRAWDOWN: lambda limits: dataclasses.replace(
        limits, max_drawdown_frac=float('inf')),
}

#: Minimum seconds between two recorded rows for the SAME measured constraint.
#:
#: THIS IS A DELIBERATE DEPARTURE from "record every denial exactly as today"
#: (convention 20), and it is here because measurement-only breaks the
#: assumption convention 20 was written under. Enforced, a drawdown breach
#: records once and then halts, so "every denial" is a handful of rows.
#: Measured, nothing stops it: the book keeps trading while in breach, so every
#: entry attempt for as long as the drawdown persists - hours - writes another
#: identical row. Two things break if it does. `denials_by_constraint` is the
#: kill-condition harness and counts rows, so an unthrottled drawdown would
#: report as the least decorative constraint in the module purely because it is
#: the only one that repeats. And `_drawdown_attribution` runs
#: `backtest.drawdown_attribution.epochs`, a full scan of the closed book, in
#: the entry hot path.
#:
#: 300s keeps every distinct breach EPISODE (049 reads episodes, and a drawdown
#: instrument that needs sub-5-minute resolution is measuring noise) while
#: bounding both costs. It applies ONLY in measure-only mode; the enforced path
#: is untouched and `test_an_existing_halt_is_never_reminted` still records all
#: three of its denials.
#:
#: OPEN JUDGEMENT CALL for Raven/Aym - see the D-383 handoff. If the ruling is
#: that literally every breach attempt must be a row, set this to 0.0.
MEASURE_ONLY_RECORD_INTERVAL_SEC = 300.0

#: Last monotonic time each measured constraint was recorded, this process.
#: Process-global on purpose: each shadow book is its own process, so there is
#: no cross-book leakage to reason about.
_measure_only_last_record = {}


def reset_measure_only_throttle():
    """Clear the throttle. For tests, and for a caller starting a fresh book."""
    _measure_only_last_record.clear()


def _measure_only_should_record(constraint, now=None):
    """True if this measured breach is due to be written. Advances the clock."""
    if MEASURE_ONLY_RECORD_INTERVAL_SEC <= 0:
        return True
    now = time.monotonic() if now is None else now
    last = _measure_only_last_record.get(constraint)
    if last is not None and (now - last) < MEASURE_ONLY_RECORD_INTERVAL_SEC:
        return False
    _measure_only_last_record[constraint] = now
    return True


def record_denial(conn, decision, ts_ms=None):
    """Write one `risk_events` row for a denial. Returns the row id.

    Raises on an allowing decision: recording an allow as a denial would inflate
    the very count the kill condition reads.
    """
    if decision.allowed:
        raise ValueError('record_denial called with an allowing decision')
    row_id = str(uuid.uuid4())
    details = {
        'constraint': decision.constraint,
        'reason': decision.reason,
        'halt_required': decision.halt_required,
    }
    details.update(decision.detail or {})
    details.update(_drawdown_attribution(conn, decision))
    conn.execute(
        'INSERT INTO risk_events (id, ts, type, details_json) VALUES (?, ?, ?, ?)',
        (row_id, _ms() if ts_ms is None else int(ts_ms), RISK_EVENT_TYPE,
         json.dumps(details, default=str)))
    return row_id


def engage_drawdown_halt(conn, decision):
    """Route a drawdown breach into `engine.halt`. Returns the ack id, or None.

    Returns None when a halt is already engaged - see the module docstring: the
    existing ack id is left intact rather than reminted per entry attempt.
    """
    if not decision.halt_required:
        raise ValueError('engage_drawdown_halt called on a non-halt decision')
    if is_halted():
        logger.warning('risk: drawdown breach while already halted (%s)',
                       decision.reason)
        return None
    halt_id = write_halt('auto: {}'.format(decision.reason))
    conn.execute(
        'INSERT INTO risk_events (id, ts, type, details_json) VALUES (?, ?, ?, ?)',
        (str(uuid.uuid4()), _ms(), RISK_EVENT_TYPE,
         json.dumps({'constraint': C.CONSTRAINT_DRAWDOWN,
                     'event': 'halt_engaged',
                     'halt_id': halt_id,
                     'reason': decision.reason,
                     **(decision.detail or {}),
                     **_drawdown_attribution(conn, decision)}, default=str)))
    logger.error('RISK HALT (drawdown): %s; resume requires: '
                 'botctl.py resume --ack %s', decision.reason, halt_id)
    return halt_id


def evaluate_and_record(conn, open_positions, candidate, equity,
                        limits=C.DEFAULT_LIMITS, measure_only=frozenset()):
    """Evaluate `candidate`, record any denial, engage the halt if required.

    The one function an entry path should call. Returns the `Decision` the
    caller must act on, so the caller still sees which constraint bound and why.

    WIRED into `engine.polymarket.shadow_loop`'s entry path as of D-343
    (Task 1); the PM gate's duplicate caps were delegated in the same change
    (D-343 R1, see `constraints.py`). The code is in the tree but a running
    process only picks it up at its next restart (convention 13).

    `measure_only` is the D-383 shadow path - see `_MEASURE_ONLY_NEUTRALISERS`
    for the whole argument. Constraints named in it are RECORDED and then
    stepped over: no halt, no refusal, and the constraints after them still
    decide the entry. It defaults to empty, so the real-money path through
    `DEFAULT_LIMITS` is exactly what it was before D-383.
    """
    decision = C.check(open_positions, candidate, equity, limits)

    # Step over each measured constraint in turn. `measured` bounds the loop at
    # one pass per constraint: a neutraliser that failed to neutralise (a bug)
    # must not spin here, it must fall through to the enforcement below.
    measured = set()
    while (not decision.allowed
           and decision.constraint in measure_only
           and decision.constraint in _MEASURE_ONLY_NEUTRALISERS
           and decision.constraint not in measured):
        measured.add(decision.constraint)
        if _measure_only_should_record(decision.constraint):
            record_denial(conn, decision)
        limits = _MEASURE_ONLY_NEUTRALISERS[decision.constraint](limits)
        decision = C.check(open_positions, candidate, equity, limits)

    if decision.allowed:
        return decision
    record_denial(conn, decision)
    # A measured constraint never engages the halt, even on the fall-through
    # path where no neutraliser existed for it. That path still DENIES - it is
    # the safe direction - but D-383 R2 forbids the halt unconditionally.
    if decision.halt_required and decision.constraint not in measure_only:
        engage_drawdown_halt(conn, decision)
    return decision


def denials_by_constraint(conn, since_ts_ms=None):
    """`{constraint_name: count}` over the window. THE kill-condition harness.

    This is the query PLAN section 5 names: "`risk_events` table, grouped by
    constraint name". The module is DEAD if, over
    `KILL_CONDITION_WINDOW_DAYS`, no constraint appears more than
    `DECORATIVE_BINDING_THRESHOLD` times - the caps are then set above the
    book's natural range and are decorative.

    Constraints that never bound are reported as 0 rather than omitted. A
    missing key reads as "not measured"; an explicit zero is the finding
    (convention 11).
    """
    if since_ts_ms is None:
        since_ts_ms = _ms() - KILL_CONDITION_WINDOW_DAYS * 86400 * 1000
    counts = {name: 0 for name in C.ALL_CONSTRAINTS}
    rows = conn.execute(
        "SELECT json_extract(details_json, '$.constraint') AS constraint_name, "
        "COUNT(*) AS n FROM risk_events "
        "WHERE type = ? AND ts >= ? "
        "AND json_extract(details_json, '$.event') IS NULL "
        "GROUP BY constraint_name",
        (RISK_EVENT_TYPE, int(since_ts_ms))).fetchall()
    for row in rows:
        name = row['constraint_name'] if not isinstance(row, tuple) else row[0]
        n = row['n'] if not isinstance(row, tuple) else row[1]
        if name is None:
            # A row we wrote without a constraint name would make the kill
            # condition unreadable. Surface it rather than dropping it.
            counts['unnamed'] = counts.get('unnamed', 0) + n
        else:
            counts[name] = counts.get(name, 0) + n
    return counts


def is_decorative(counts):
    """True when NO constraint bound more than the threshold - the kill
    condition from PLAN section 5, evaluated."""
    return not any(n > DECORATIVE_BINDING_THRESHOLD for n in counts.values())
