#!/usr/bin/env bash
#
# run_polymarket_shadow.sh - start the Polymarket paper/shadow decision loop.
#
# Wraps `python3 -m engine.polymarket.shadow_loop`. That module is the only
# entrypoint for the Polymarket path; `run_shadow.sh` runs the CRYPTO engine
# (collector -> scanner -> executor) and does not touch Polymarket at all. The
# two are separate processes on purpose: they poll different venues on different
# cadences, and one crashing must not take the other with it.
#
# PAPER ONLY. There is no live execution path in engine/polymarket/. This script
# is the OUTER of four independent refusals:
#   1. this script (config mode, TRADING_LIVE_ACK, and an imported PAPER_MODE
#      assertion, below)
#   2. shadow_loop.main() re-checks the mode before constructing anything
#   3. PolymarketPaperAdapter refuses to build if PAPER_MODE is not True
#   4. PolymarketRiskGate.check_order blocks any mode other than 'paper'
# A mode gate that only one reader enforces is a mode gate with a single point
# of failure.
#
# Usage:
#   ./run_polymarket_shadow.sh                 # foreground, Ctrl-C to stop
#   nohup ./run_polymarket_shadow.sh > logs/polymarket_shadow.out 2>&1 &
#
# Stop:
#   Ctrl-C, or `kill <pid of this script>`. Both are graceful: the signal is
#   forwarded to python, the loop resolves open positions, flushes a final
#   equity snapshot and a stats line, then exits.
#   From another terminal you can also halt:
#     env -u PYTHONPATH python3 botctl.py halt "reason"
#
#   NOTE what a halt does here. It blocks NEW ENTRIES only. It cannot flatten:
#   a binary held to resolution has no sell path in paper mode, so open exposure
#   survives the halt. That is the documented asymmetry with the crypto path,
#   where HALT also closes positions and drains the queue.
#
set -euo pipefail

# Run from the repo root regardless of where the operator invoked this from:
# the module loads config.yaml relative to the repo root, and the log and db
# paths below are relative.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# `env -u PYTHONPATH` everywhere (convention 14): Hermes leaks its 3.11 venv
# onto PYTHONPATH and numpy then fails to import in a way that reads as a broken
# install.
PY=(env -u PYTHONPATH python3)

POLL_SEC="${POLL_SEC:-5}"
STARTING_EQUITY="${STARTING_EQUITY:-1000}"

die() {
    echo "run_polymarket_shadow: REFUSING TO START: $*" >&2
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
# Print the RAW value, not a default. A missing `mode:` key must fail the check
# below rather than quietly resolve to 'paper' here: a mode nobody wrote down is
# a mode nobody reviewed.
sys.stdout.write(str(cfg.get('mode')))
PYEOF
)"

if [ "$MODE" != "paper" ]; then
    die "config.yaml has mode='${MODE}', not 'paper'. This build has no live trading authority. Set 'mode: paper' in config.yaml."
fi

# ---------------------------------------------------------------------------
# Gate 2: the live acknowledgment must not be set.
#
# TRADING_LIVE_ACK is half of the two-key live interlock (.env.example). The
# config says paper, so this variable has no legitimate reason to be in the
# environment of a shadow run. Its presence means somebody was part-way through
# arming live, and a shadow session is not the place to discover that.
# ---------------------------------------------------------------------------
if [ -n "${TRADING_LIVE_ACK:-}" ]; then
    die "TRADING_LIVE_ACK is set. Unset it before a shadow run: unset TRADING_LIVE_ACK"
fi

# ---------------------------------------------------------------------------
# Gate 3: the CODE must still be in paper mode, not just the config.
#
# Reading the constants rather than trusting this file's own comments: a claim
# in a docstring is not a wiring test (convention 22). This imports the modules
# and asserts shadow_loop.PAPER_MODE, paper_adapter.PAPER_MODE, and every
# strategy's paper_mode flag in one shot.
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
sys.stdout.write('run_polymarket_shadow: paper-mode assertions OK\n')
PYEOF

# ---------------------------------------------------------------------------
# Gate 4: the kill switch must be clear.
#
# Starting while halted produces a session that looks running and enters
# nothing, which is the most confusing possible shadow run. Resume explicitly.
# The check imports engine.halt rather than testing for a file, so this script
# can never disagree with the engine about where the HALT file lives.
# ---------------------------------------------------------------------------
if "${PY[@]}" -c 'import sys
from engine.halt import is_halted
sys.exit(0 if is_halted() else 1)'; then
    echo "run_polymarket_shadow: HALT file is present:" >&2
    "${PY[@]}" botctl.py status >&2 || true
    die "clear it first: env -u PYTHONPATH python3 botctl.py resume --ack <halt_id>"
fi

# ---------------------------------------------------------------------------
# Launch.
# ---------------------------------------------------------------------------
mkdir -p logs
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/polymarket_shadow_${STAMP}.log"

{
    echo "=== polymarket shadow session ${STAMP} (UTC) ==="
    echo "repo:     ${REPO_ROOT}"
    echo "commit:   $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "mode:     ${MODE}  (PAPER ONLY - no wallet, no signer, no order API)"
    echo "equity:   \$${STARTING_EQUITY}.00 starting paper balance"
    echo "poll:     ${POLL_SEC}s"
    echo "db:       db/trading.db (WAL; dashboard reads it mode=ro)"
    echo "csv:      research/polymarket_paper/polymarket_paper_log.csv"
    echo "python:   $("${PY[@]}" --version 2>&1)"
    echo "HALT here blocks ENTRIES only. It cannot flatten a binary in paper mode."
    echo "==="
} | tee "$LOG"

echo "run_polymarket_shadow: log=${LOG}"

# Process substitution rather than a plain pipe so ${CHILD} is PYTHON's pid and
# not tee's. A trap that kills the wrong end of a pipeline leaves the loop
# running and reports a clean shutdown.
"${PY[@]}" -u -m engine.polymarket.shadow_loop \
    --poll "${POLL_SEC}" \
    --equity "${STARTING_EQUITY}" \
    > >(tee -a "$LOG") 2>&1 &
CHILD=$!

echo "run_polymarket_shadow: python pid=${CHILD}"

trap 'echo "run_polymarket_shadow: forwarding stop to pid ${CHILD}" >&2; kill -TERM "${CHILD}" 2>/dev/null || true' INT TERM

# `wait` returns as soon as a trapped signal arrives, BEFORE the child has
# finished flushing its final equity snapshot. So wait again until the pid is
# genuinely gone. Convention 18: wait on a PID with `kill -0`, never on a
# `pgrep -f` pattern that this very shell's own command line would match.
set +e
wait "${CHILD}"
STATUS=$?
while kill -0 "${CHILD}" 2>/dev/null; do
    wait "${CHILD}"
    STATUS=$?
done
set -e

echo "run_polymarket_shadow: exited with status ${STATUS}"
exit "${STATUS}"
