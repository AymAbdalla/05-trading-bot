#!/usr/bin/env bash
#
# run_polymarket_shadow_realmc.sh - start the REALM C Polymarket shadow loop.
#
# WHAT REALM C IS
# ---------------
# D-363 R2. The third book. Its roster is every strategy that was neither
# measured in main nor in env B - which, once the registry is actually
# partitioned, turns out to be exactly the six SENTINEL-PAUSED strategies:
#
#   PM_box_builder            D-323, maker bleed  (-$54.30, 24.6% win)
#   PM_grid_hedge             D-323, maker bleed  (-$178.16, 26.0% win)
#   PM_dip_arb                D-322, and its tape carve-out went moot
#   PM_fair_value_arb_hft     D-322, -$221 at 22.7% against a 66.7% break-even
#   PM_fair_value_arb_inverse D-322, -$65 at 48.1% against a 75% break-even
#   PM_fair_value_mirror_fade D-326/D-329, taker-only t=1.19 on n=116
#
# Aym (D-363): "if any are not tested or were ruled out we should just test
# them together in a 3rd shadow realm." So the point of this book is NOT to
# re-run strategies anyone expects to make money. Every one of them was paused
# on measured bleed. The point is that "paused" and "measured under the current
# code" are different states, and D-363 wants no strategy left in the first one.
#
# EXPECT LOSSES HERE. This book existing is not a claim that these six are
# viable; reading a loss off it is not a new finding.
#
# HOW THE PAUSE IS LIFTED
# -----------------------
# `--unpause` restores each strategy's real `supported_market_types` (the value
# its own pause comment names as the revert target) for THIS PROCESS ONLY. The
# source stays sentinel-killed, so main and env B - separate processes - are
# untouched and still cannot route these six. See
# `engine/polymarket/shadow_loop.py:unpause_sentinel_strategies`.
#
# TWO OF THE SIX MAY NEVER ENTER. `PM_box_builder` and `PM_grid_hedge` are the
# only `uses_maker_orders = True` strategies and return QUOTE rather than
# ENTER; whether they produce fills depends on the maker path, not on this
# launcher. A zero entry count from either is a MEASUREMENT, and it needs to be
# reported as "no entries" rather than as "no edge" (convention 11/20).
#
# Usage:
#   ./run_polymarket_shadow_realmc.sh
#   tmux new-session -d -s shadow-realmc -c "$PWD" ./run_polymarket_shadow_realmc.sh
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# `env -u PYTHONPATH` (convention 14): Hermes leaks its 3.11 venv onto
# PYTHONPATH and numpy then fails to import in a way that reads as a broken
# install.
PY=(env -u PYTHONPATH python3)

DB="${DB:-db/trading-realm-c.db}"
LOG_DIR="${LOG_DIR:-research/polymarket_paper_realmc}"

# ---------------------------------------------------------------------------
# ROSTER - the definition of realm C, and the third disjoint slice of the
# registry (D-363 R5: every strategy runs in EXACTLY ONE realm).
#
#   main    16  diversified survivors, no fair_value
#   env B    4  fair_value isolation
#   realm C  6  this file
#   -----------
#   total   26  = len(build_strategies()), no gaps, no duplicates
#
# `tests/test_realm_partition.py` asserts that arithmetic against the live
# registry and against all three launchers, so a strategy added to the registry
# without being given a realm FAILS THE SUITE rather than silently going
# unmeasured. If you add a strategy, give it a realm.
# ---------------------------------------------------------------------------
STRATEGIES="${STRATEGIES:-PM_box_builder,PM_grid_hedge,PM_dip_arb,PM_fair_value_arb_hft,PM_fair_value_arb_inverse,PM_fair_value_mirror_fade}"

# Every name in this book is sentinel-paused, so the un-pause set IS the roster.
UNPAUSE="${UNPAUSE:-$STRATEGIES}"

die() {
    echo "run_polymarket_shadow_realmc: REFUSING TO START: $*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Gate 1: the config must resolve to paper mode.
# ---------------------------------------------------------------------------
[ -f config.yaml ] || die "no config.yaml at ${REPO_ROOT}"

MODE="$("${PY[@]}" - <<'PYEOF'
import sys
import yaml
with open('config.yaml') as f:
    cfg = yaml.safe_load(f) or {}
# Print the RAW value, not a default: a missing `mode:` key must fail the check
# below rather than quietly resolve to 'paper' here.
sys.stdout.write(str(cfg.get('mode')))
PYEOF
)"

if [ "$MODE" != "paper" ]; then
    die "config.yaml has mode='${MODE}', not 'paper'. This build has no live trading authority."
fi

# ---------------------------------------------------------------------------
# Gate 2: the live acknowledgment must not be set.
# ---------------------------------------------------------------------------
if [ -n "${TRADING_LIVE_ACK:-}" ]; then
    die "TRADING_LIVE_ACK is set. Unset it before a shadow run: unset TRADING_LIVE_ACK"
fi

# ---------------------------------------------------------------------------
# Gate 3: the CODE must still be in paper mode, not just the config.
# ---------------------------------------------------------------------------
"${PY[@]}" - <<'PYEOF' || die "paper-mode assertion failed (see above)"
import sys
from engine.polymarket import paper_adapter, shadow_loop
from strategies.polymarket import build_strategies

problems = []
if shadow_loop.PAPER_MODE is not True:
    problems.append('shadow_loop.PAPER_MODE is not True')
if shadow_loop.MODE != 'paper':
    problems.append('shadow_loop.MODE is %r' % shadow_loop.MODE)
if paper_adapter.PAPER_MODE is not True:
    problems.append('paper_adapter.PAPER_MODE is not True')
for s in build_strategies():
    if getattr(s, 'paper_mode', None) is not True:
        problems.append('%s.paper_mode is not True' % s.strategy_name)
if problems:
    sys.stderr.write('PAPER MODE VIOLATION: ' + '; '.join(problems) + '\n')
    sys.exit(1)
sys.stdout.write('run_polymarket_shadow_realmc: paper-mode assertions OK\n')
PYEOF

# ---------------------------------------------------------------------------
# Gate 4: the roster must be REAL, un-pausable, and routed once un-paused.
#
# This is env B's Gate 4 turned inside out. There, a sentinel-killed name is a
# corruption to refuse. Here it is the ENTRY REQUIREMENT - but the refusal it
# protects against is the same one: a book that silently measures fewer
# strategies than its roster names. Checked in one interpreter, in the same
# order the loop will do it, so this gate cannot pass on a state the loop then
# fails to reach.
# ---------------------------------------------------------------------------
STRATEGIES="$STRATEGIES" UNPAUSE="$UNPAUSE" "${PY[@]}" - <<'PYEOF' || die "roster check failed (see above)"
import os
import sys
from engine.polymarket.shadow_loop import (SENTINEL_MARKET_TYPES,
                                           unpause_sentinel_strategies)
from strategies.polymarket import build_strategies

wanted = [n.strip() for n in os.environ['STRATEGIES'].split(',') if n.strip()]
unpause = [n.strip() for n in os.environ['UNPAUSE'].split(',') if n.strip()]

dupes = sorted({n for n in wanted if wanted.count(n) > 1})
if dupes:
    sys.stderr.write('ROSTER: duplicate names: %s\n' % ', '.join(dupes))
    sys.exit(1)

known = {s.strategy_name for s in build_strategies()}
unknown = [n for n in wanted if n not in known]
if unknown:
    sys.stderr.write('ROSTER: no such strategy: %s\n' % ', '.join(unknown))
    sys.exit(1)

# Anything named for un-pausing must actually be paused, and must have a
# recorded restore value. `unpause_sentinel_strategies` raises on both.
try:
    restored = unpause_sentinel_strategies(unpause)
except ValueError as exc:
    sys.stderr.write('ROSTER: --unpause would be refused: %s\n' % exc)
    sys.exit(1)

# After the un-pause, every rostered name must be routed somewhere. A name
# still carrying the sentinel here would match nothing and shrink the book.
still_dead = [s.strategy_name for s in build_strategies()
              if s.strategy_name in wanted
              and tuple(s.supported_market_types) == SENTINEL_MARKET_TYPES]
if still_dead:
    sys.stderr.write('ROSTER: still sentinel-killed after --unpause: %s\n'
                     % ', '.join(sorted(still_dead)))
    sys.exit(1)

sys.stdout.write('run_polymarket_shadow_realmc: roster OK, %d strategies, '
                 '%d un-paused\n' % (len(wanted), len(restored)))
PYEOF

# ---------------------------------------------------------------------------
# Gate 5: the kill switch must be clear.
#
# engine.halt is process-wide, not per-database: a HALT blocks entries in ALL
# books. Import it rather than testing for a file so this script can never
# disagree with the engine about where the HALT file lives.
# ---------------------------------------------------------------------------
if "${PY[@]}" -c 'import sys
from engine.halt import is_halted
sys.exit(0 if is_halted() else 1)'; then
    echo "run_polymarket_shadow_realmc: HALT file is present:" >&2
    "${PY[@]}" botctl.py status >&2 || true
    die "clear it first: env -u PYTHONPATH python3 botctl.py resume --ack <halt_id>"
fi

# ---------------------------------------------------------------------------
# Launch.
# ---------------------------------------------------------------------------
mkdir -p logs "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/polymarket_shadow_realmc_${STAMP}.log"

# Launch provenance (D-332): who started this, and which process tree it became.
{
    echo "=== polymarket shadow session REALM C (paused/untested) ${STAMP} (UTC) ==="
    echo "repo:       ${REPO_ROOT}"
    echo "commit:     $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "mode:       ${MODE}  (PAPER ONLY - no wallet, no signer, no order API)"
    echo "db:         ${DB}"
    echo "log-dir:    ${LOG_DIR}"
    echo "strategies: ${STRATEGIES}"
    echo "unpause:    ${UNPAUSE}"
    echo "python:     $("${PY[@]}" --version 2>&1)"
    echo "launched-by: ${AGENT_ID:-UNDECLARED}"
    echo "launcher-pid: $$   parent-pid: ${PPID}"
    echo "D-363 R2: these six are sentinel-PAUSED in source and un-paused for"
    echo "this process only. Expect losses; that is not a new finding."
    echo "==="
} | tee "$LOG"

echo "run_polymarket_shadow_realmc: log=${LOG}"

# Process substitution rather than a plain pipe so ${CHILD} is PYTHON's pid and
# not tee's. A trap that kills the wrong end of a pipeline leaves the loop
# running and reports a clean shutdown.
"${PY[@]}" -u -m engine.polymarket.shadow_loop \
    --db "${DB}" \
    --strategies "${STRATEGIES}" \
    --unpause "${UNPAUSE}" \
    --log-dir "${LOG_DIR}" \
    > >(tee -a "$LOG") 2>&1 &
CHILD=$!

echo "run_polymarket_shadow_realmc: python pid=${CHILD}"

trap 'echo "run_polymarket_shadow_realmc: forwarding stop to pid ${CHILD}" >&2; kill -TERM "${CHILD}" 2>/dev/null || true' INT TERM

# `wait` returns as soon as a trapped signal arrives, BEFORE the child has
# finished flushing. Wait again until the pid is genuinely gone (convention 18:
# wait on a PID with `kill -0`, never on a `pgrep -f` pattern this very shell's
# command line would match).
set +e
wait "${CHILD}"
STATUS=$?
while kill -0 "${CHILD}" 2>/dev/null; do
    wait "${CHILD}"
    STATUS=$?
done
set -e

echo "run_polymarket_shadow_realmc: exited with status ${STATUS}"
exit "${STATUS}"
