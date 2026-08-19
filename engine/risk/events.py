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
                     **(decision.detail or {})}, default=str)))
    logger.error('RISK HALT (drawdown): %s; resume requires: '
                 'botctl.py resume --ack %s', decision.reason, halt_id)
    return halt_id


def evaluate_and_record(conn, open_positions, candidate, equity,
                        limits=C.DEFAULT_LIMITS):
    """Evaluate `candidate`, record any denial, engage the halt if required.

    The one function an entry path should call. Returns the `Decision`
    unchanged, so the caller still sees which constraint bound and why.

    NOT WIRED into any live path as of D-342 R2: activation is the restart
    AFTER the ONE at ~03:45 EDT 2026-08-20, and before it is wired the
    duplication with the Polymarket gate documented in `constraints.py` must be
    resolved to a single authoritative cap.
    """
    decision = C.check(open_positions, candidate, equity, limits)
    if decision.allowed:
        return decision
    record_denial(conn, decision)
    if decision.halt_required:
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
