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

# ---------------------------------------------------------------------------
# ROSTER - the DIVERSIFIED book. D-362 R5/R8, 2026-08-20. Read before changing.
#
# Main used to run the whole registry with no `--strategies` at all. It now runs
# an EXPLICIT roster that EXCLUDES the fair_value family, because that family
# moved to env B (`run_polymarket_shadow_envb.sh`). That is the D-361/D-362
# split: one book isolates fair_value, the other runs everything else, and the
# two rosters together must cover the registry with no overlap and no orphan.
#
# WHY THE SPLIT IS A ROSTER EDIT AND NOT A CODE OVERRIDE: `supported_market_types`
# is a CLASS attribute. Both books import the same classes, so a D-322-style
# per-book override would remove fair_value from BOTH - emptying the isolation
# book of the very thing it isolates. `--strategies` is the only per-environment
# mechanism there is.
#
# EXCLUDED, and why each one:
#   PM_fair_value_arb            -> env B (the split)
#   PM_fair_value_arb_patient    -> env B (the split)
#   PM_fair_value_arb_wide       -> env B (the split). It was UNACCOUNTED for in
#                                   the original D-361 brief - 113 closes in
#                                   main - and would have been silently killed.
#   PM_fair_value_settlement_exit-> env B (the split)
#   PM_fair_value_arb_hft        -> STAYS PAUSED (D-322 stands, D-362 R6). Also
#                                   sentinel-killed, so naming it would match
#                                   nothing and Gate 5 below would refuse.
#   PM_fair_value_arb_inverse    -> same as _hft.
#   PM_fair_value_mirror_fade    -> sentinel-killed (D-322).
#   PM_box_builder, PM_grid_hedge, PM_dip_arb -> sentinel-killed. The D-361
#                                   brief named box_builder and grid_hedge as
#                                   part of a "diversified main"; they are dead
#                                   and cannot be part of anything.
#
# D-363 R2/R5 UPDATE (2026-08-20). MAIN'S ROSTER IS UNCHANGED - the 16 names
# below are exactly what D-362 set. What changed is around it:
#
#   * The six "sentinel-killed, cannot be part of anything" names above are now
#     part of something. REALM C (`run_polymarket_shadow_realmc.sh`) un-pauses
#     them for its own process and measures them. The line above stays true of
#     THIS book and is no longer true of the registry.
#   * Env B narrowed from 11 to 4. The seven diversified survivors it used to
#     share with main (temporal_arbitrage, streak_snapper, small_liq_continuation,
#     corridor_collector, weather_arb, weather_bracket_width_matched,
#     longshot_fade_hold_to_resolution) now run ONLY here. Main is their sole
#     book, which makes main's numbers for them a clean read for the first time.
#
# The three rosters are now a true partition of the registry:
#
#   main    16  THIS FILE
#   env B    4  run_polymarket_shadow_envb.sh
#   realm C  6  run_polymarket_shadow_realmc.sh
#   -----------
#   total   26  = len(build_strategies())
#
# `tests/test_realm_partition.py` asserts it. Adding a strategy to the registry
# without giving it a realm FAILS THE SUITE.
#
# WHAT THIS COSTS, recorded because it is a real cost: 71.6% of main's closed
# book (3003 of 4192) is the fair_value family. Nothing is deleted, but main
# accrues no FURTHER fair_value closes, which ENDS the active measurement
# proposals 043 and 046 run against main. Aym ruled that acceptable (D-362).
# ---------------------------------------------------------------------------
STRATEGIES="${STRATEGIES:-PM_streak_snapper,PM_mid_price_continuation,PM_corridor_collector,PM_temporal_arbitrage,PM_corridor_pair,PM_spread_harvest_taker,PM_liq_cascade_chaser,PM_small_liq_continuation,PM_near_liq_trigger,PM_smart_money_copy,PM_weather_arb,PM_maker_rebate_quote_ladder,PM_smart_money_callers,PM_status_quo_collector,PM_longshot_fade_hold_to_resolution,PM_weather_bracket_width_matched}"

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
# Gate 4: every name in the roster must match a REAL, ROUTED strategy.
#
# New with D-362 R5: main HAS a roster now, so it needs env B's gate. Copied
# from `run_polymarket_shadow_envb.sh` deliberately rather than shared - two
# launchers that can drift apart in what they accept is a smaller risk than a
# sourced helper that silently changes both books at once.
#
# `--strategies` filters the ROUTED sets AFTER construction, so a
# sentinel-killed name matches NOTHING and the loop merely warns. On a book
# DEFINED by its roster that is a silent corruption of the experiment: the
# session runs a smaller book and nothing says so. Refuse, and name the
# offenders.
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

sys.stdout.write('run_polymarket_shadow: roster OK, %d strategies\n'
                 % len(wanted))
PYEOF

# ---------------------------------------------------------------------------
# Gate 5: the kill switch must be clear.
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

# Launch provenance (D-332). Twice now, "who restarted the loop?" has been
# unanswerable from in-repo evidence: `ps` parentage is not walkable after the
# fact, tmux listing is not permitted from a sandboxed session, and shell
# history lives outside the repo. The missing measurement was here, so it is
# taken here. Three fields, each answering a different question:
#
#   launched-by   WHO. Spawned agent sessions export AGENT_ID=cody-<topic> for
#                 the whole session (D-331), the same variable
#                 engine/concurrency.py resolves DEFAULT_AGENT_ID from, so the
#                 loop's launcher and the launcher's file edits carry one
#                 identity. UNDECLARED means a human shell or an agent that did
#                 not declare -- an honest gap, not a silent default.
#   launcher-pid  $$ -- THIS script. It is one of the wrapper pair that shows up
#                 around the python child in `ps`, so it joins the log file to a
#                 process tree seen later. `python pid=` below is the other end.
#   parent-pid    ${PPID} -- what invoked the script: the tmux pane, the nohup
#                 shell, the agent's bash. This is the trace that actually
#                 answers "who", but it is the perishable one: the parent is
#                 usually gone by the time anyone asks. That is exactly why
#                 launched-by carries the durable answer and the pids only
#                 corroborate it.
#
# ${PPID} is fixed at shell startup and $$ is the script's own pid, not the
# pipeline subshell's, so both survive the `{ ... } | tee` below unchanged.
# tests/test_launcher_banner.py pins that by comparing them to the real pids.

{
    echo "=== polymarket shadow session ${STAMP} (UTC) ==="
    echo "repo:     ${REPO_ROOT}"
    echo "commit:   $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "mode:     ${MODE}  (PAPER ONLY - no wallet, no signer, no order API)"
    echo "equity:   \$${STARTING_EQUITY}.00 starting paper balance"
    echo "poll:     ${POLL_SEC}s"
    echo "db:       db/trading.db (WAL; dashboard reads it mode=ro)"
    echo "strategies: ${STRATEGIES}"
    echo "csv:      research/polymarket_paper/polymarket_paper_log.csv"
    echo "python:   $("${PY[@]}" --version 2>&1)"
    echo "launched-by: ${AGENT_ID:-UNDECLARED}"
    echo "launcher-pid: $$   parent-pid: ${PPID}"
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
    --strategies "${STRATEGIES}" \
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
