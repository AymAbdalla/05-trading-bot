#!/usr/bin/env bash
#
# run_polymarket_shadow_envb.sh - start the ENVIRONMENT B (survivors) Polymarket
# shadow loop.
#
# WHY THIS FILE EXISTS
# --------------------
# Env B's roster used to live ONLY in a tmux invocation typed by hand. A reboot,
# or a tmux server restart, silently lost it: the roster is the entire
# definition of the A/B, so losing it does not crash anything, it just quietly
# turns env B into a different experiment. Main had `run_polymarket_shadow.sh`;
# env B had nothing. This is the mirror (Raven brief 2026-08-20 item 6,
# CLAUDE.md open item 7).
#
# WHAT ENV B IS
# -------------
# A second, independent shadow book on its own database (`db/trading-survivors.db`)
# running a STRICT SUBSET of the strategy registry. Same code, same config, same
# venue - only the roster differs. That is what makes it an A/B rather than two
# unrelated runs. `--strategies` is the ONLY per-environment mechanism there is
# (see the ROSTER note below).
#
# Usage:
#   ./run_polymarket_shadow_envb.sh              # foreground, Ctrl-C to stop
#   tmux new-session -d -s shadow-survivors -c "$PWD" ./run_polymarket_shadow_envb.sh
#
# Stop: Ctrl-C or `kill <pid of this script>`; the signal is forwarded to python,
# which resolves open positions, flushes a final equity snapshot, then exits.
#
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# `env -u PYTHONPATH` (convention 14): Hermes leaks its 3.11 venv onto
# PYTHONPATH and numpy then fails to import in a way that reads as a broken
# install.
PY=(env -u PYTHONPATH python3)

DB="${DB:-db/trading-survivors.db}"
LOG_DIR="${LOG_DIR:-research/polymarket_paper_survivors}"

# ---------------------------------------------------------------------------
# ROSTER - the definition of env B. Read this before changing it.
#
# THE SPLIT IS APPLIED (D-362 R5/R7/R8, 2026-08-20). This is the R4 roster of
# NINE plus the two fair_value members that were still in main, making ELEVEN.
# Env B is the fair_value ISOLATION book now; main
# (`run_polymarket_shadow.sh`) carries the explicit complementary roster and
# runs no fair_value at all.
#
# `--strategies` filters the ROUTED sets AFTER construction. A strategy whose
# `supported_market_types` is the `('smart_money',)` D-322 sentinel is never
# routed anywhere, so naming it here matches NOTHING - which is why Gate 4
# below refuses on one rather than letting the book silently shrink.
#
# ADDED by D-362 over the R4 nine:
#   + PM_fair_value_arb        the split's headline move, out of main
#   + PM_fair_value_arb_wide   D-362 R7. UNACCOUNTED for in the D-361 brief
#                              (113 closes in main) - without this line the
#                              split would have killed it outright.
# ALREADY PRESENT, no change: PM_fair_value_arb_patient,
#   PM_fair_value_settlement_exit. Both are fair_value family and both were
#   already in the nine, so the union is 11, not 13.
#
# DELIBERATELY NOT ADDED (D-362 R6 - D-322 STANDS):
#   PM_fair_value_arb_hft, PM_fair_value_arb_inverse. Paused for bleed and
#   they stay paused. Both are also sentinel-killed, so Gate 4 would refuse
#   them anyway.
#
# BOTH HALVES LANDED TOGETHER. Enacting env B's half alone would run
# fair_value in BOTH books and DOUBLE the contention the split exists to
# remove. If you ever revert one launcher, revert the other in the same edit.
# ---------------------------------------------------------------------------
STRATEGIES="${STRATEGIES:-PM_temporal_arbitrage,PM_fair_value_arb_patient,PM_longshot_fade_hold_to_resolution,PM_weather_bracket_width_matched,PM_fair_value_settlement_exit,PM_weather_arb,PM_streak_snapper,PM_small_liq_continuation,PM_corridor_collector,PM_fair_value_arb,PM_fair_value_arb_wide}"

die() {
    echo "run_polymarket_shadow_envb: REFUSING TO START: $*" >&2
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
sys.stdout.write('run_polymarket_shadow_envb: paper-mode assertions OK\n')
PYEOF

# ---------------------------------------------------------------------------
# Gate 4: every name in the roster must match a REAL, ROUTED strategy.
#
# This gate does not exist in the main launcher because main has no roster. Env
# B is defined BY its roster, so a typo or a sentinel-killed name is a silent
# corruption of the experiment: the loop warns and carries on with a smaller
# book, and the A/B is quietly no longer the A/B anyone signed off on. Refuse
# instead, and name the offenders.
# ---------------------------------------------------------------------------
STRATEGIES="$STRATEGIES" "${PY[@]}" - <<'PYEOF' || die "roster check failed (see above)"
import os
import sys
from strategies.polymarket import build_strategies

CRYPTO_DEFAULT = ('crypto_updown',)
SENTINEL = ('smart_money',)

wanted = [n.strip() for n in os.environ['STRATEGIES'].split(',') if n.strip()]
routed, dead = {}, {}
for s in build_strategies():
    declared = tuple(getattr(s, 'supported_market_types', CRYPTO_DEFAULT))
    (dead if declared == SENTINEL else routed)[s.strategy_name] = declared

unknown = [n for n in wanted if n not in routed and n not in dead]
inert = [n for n in wanted if n in dead]

if unknown:
    sys.stderr.write('ROSTER: no such strategy: %s\n' % ', '.join(unknown))
if inert:
    sys.stderr.write(
        'ROSTER: sentinel-killed, would match nothing once routed: %s\n'
        % ', '.join(inert))
if unknown or inert:
    sys.exit(1)

dupes = sorted({n for n in wanted if wanted.count(n) > 1})
if dupes:
    sys.stderr.write('ROSTER: duplicate names: %s\n' % ', '.join(dupes))
    sys.exit(1)

sys.stdout.write('run_polymarket_shadow_envb: roster OK, %d strategies\n'
                 % len(wanted))
PYEOF

# ---------------------------------------------------------------------------
# Gate 5: the kill switch must be clear.
#
# engine.halt is process-wide, not per-database: a HALT blocks entries in BOTH
# books. Import it rather than testing for a file so this script can never
# disagree with the engine about where the HALT file lives.
# ---------------------------------------------------------------------------
if "${PY[@]}" -c 'import sys
from engine.halt import is_halted
sys.exit(0 if is_halted() else 1)'; then
    echo "run_polymarket_shadow_envb: HALT file is present:" >&2
    "${PY[@]}" botctl.py status >&2 || true
    die "clear it first: env -u PYTHONPATH python3 botctl.py resume --ack <halt_id>"
fi

# ---------------------------------------------------------------------------
# Launch.
# ---------------------------------------------------------------------------
mkdir -p logs "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/polymarket_shadow_envb_${STAMP}.log"

# Launch provenance (D-332): who started this, and which process tree it became.
# `launched-by` is the durable answer (AGENT_ID, D-331); the pids corroborate it
# but the parent is usually gone by the time anyone asks.
{
    echo "=== polymarket shadow session ENV B (survivors) ${STAMP} (UTC) ==="
    echo "repo:       ${REPO_ROOT}"
    echo "commit:     $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "mode:       ${MODE}  (PAPER ONLY - no wallet, no signer, no order API)"
    echo "db:         ${DB}"
    echo "log-dir:    ${LOG_DIR}"
    echo "strategies: ${STRATEGIES}"
    echo "python:     $("${PY[@]}" --version 2>&1)"
    echo "launched-by: ${AGENT_ID:-UNDECLARED}"
    echo "launcher-pid: $$   parent-pid: ${PPID}"
    echo "HALT here blocks ENTRIES only. It cannot flatten a binary in paper mode."
    echo "==="
} | tee "$LOG"

echo "run_polymarket_shadow_envb: log=${LOG}"

# Process substitution rather than a plain pipe so ${CHILD} is PYTHON's pid and
# not tee's. A trap that kills the wrong end of a pipeline leaves the loop
# running and reports a clean shutdown.
"${PY[@]}" -u -m engine.polymarket.shadow_loop \
    --db "${DB}" \
    --strategies "${STRATEGIES}" \
    --log-dir "${LOG_DIR}" \
    > >(tee -a "$LOG") 2>&1 &
CHILD=$!

echo "run_polymarket_shadow_envb: python pid=${CHILD}"

trap 'echo "run_polymarket_shadow_envb: forwarding stop to pid ${CHILD}" >&2; kill -TERM "${CHILD}" 2>/dev/null || true' INT TERM

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

echo "run_polymarket_shadow_envb: exited with status ${STATUS}"
exit "${STATUS}"
