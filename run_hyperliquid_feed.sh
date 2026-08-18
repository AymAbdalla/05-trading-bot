#!/usr/bin/env bash
#
# run_hyperliquid_feed.sh - start the Hyperliquid whale-position feed.
#
# Wraps `python3 -m engine.feeds.hyperliquid_client`. This is a READ-ONLY DATA
# FEED, not a trading path. It polls public Hyperliquid endpoints for large
# (>$100k) open perp positions and appends snapshots to `hyperliquid_positions`
# in db/trading.db, to feed a future `near_liq_trigger` strategy.
#
# It is NOT a shadow/paper session and it is not comparable to run_shadow.sh or
# run_polymarket_shadow.sh:
#   * it has no wallet, no signer, no private key and no order endpoint
#   * it never reads config.yaml `mode`, because it cannot trade in any mode
#   * a HALT does not stop it, and should not: halting trading is not a reason
#     to stop RECORDING what the market is doing. If you want it stopped, stop
#     this process. `botctl.py halt` will not do it, by design.
#
# Because it cannot place an order, the paper-mode gates that run_shadow.sh and
# run_polymarket_shadow.sh enforce would be theatre here. The refusal that DOES
# matter for this script is the read-only assertion in Gate 2 below.
#
# Usage:
#   ./run_hyperliquid_feed.sh --once              # single poll, then exit
#   ./run_hyperliquid_feed.sh                     # foreground loop, Ctrl-C to stop
#   nohup ./run_hyperliquid_feed.sh > logs/hyperliquid_feed.out 2>&1 &
#
# Any flag is forwarded straight through to the module, so:
#   ./run_hyperliquid_feed.sh --once --dry-run --top-n 5
#   ./run_hyperliquid_feed.sh --wallets my_wallets.txt --interval 60
#
# Stop:
#   Ctrl-C, or `kill <pid>` (the PID file below holds the PYTHON pid).
#
set -euo pipefail

# Run from the repo root regardless of where the operator invoked this from:
# the db and log paths the module uses are relative.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# `env -u PYTHONPATH` everywhere (convention 14): Hermes leaks its 3.11 venv
# onto PYTHONPATH and numpy then fails to import in a way that reads as a
# broken install.
PY=(env -u PYTHONPATH python3)

PIDFILE="${PIDFILE:-logs/hyperliquid_feed.pid}"

die() {
    echo "run_hyperliquid_feed: REFUSING TO START: $*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Gate 1: don't run two copies against the same table.
#
# Convention 25: a PID in a file is a CLAIM, not a fact. Confirm it with
# `kill -0` before believing it, and clear it if the process is gone.
# ---------------------------------------------------------------------------
mkdir -p logs
if [ -f "$PIDFILE" ]; then
    OLD="$(cat "$PIDFILE" 2>/dev/null || true)"
    if [ -n "$OLD" ] && kill -0 "$OLD" 2>/dev/null; then
        die "already running as pid ${OLD} (${PIDFILE}). Stop it first, or set PIDFILE=..."
    fi
    echo "run_hyperliquid_feed: clearing stale pidfile (pid ${OLD:-empty} is gone)"
    rm -f "$PIDFILE"
fi

# ---------------------------------------------------------------------------
# Gate 2: the CODE must still be read-only.
#
# A claim in a docstring is not a wiring test (convention 22). This imports the
# module and asserts the endpoints are the public read-only ones and that no
# signing/order surface has appeared in it.
# ---------------------------------------------------------------------------
"${PY[@]}" - <<'PYEOF' || die "read-only assertion failed (see above)"
import sys
from engine.feeds import hyperliquid_client as hc

problems = []
if hc.INFO_URL != 'https://api.hyperliquid.xyz/info':
    problems.append('INFO_URL is %r' % hc.INFO_URL)
if not hc.LEADERBOARD_URL.startswith('https://stats-data.hyperliquid.xyz/'):
    problems.append('LEADERBOARD_URL is %r' % hc.LEADERBOARD_URL)
src = open(hc.__file__.replace('.pyc', '.py')).read()
for token in ('private_key', 'privateKey', 'eth_account', '/exchange'):
    if token in src:
        problems.append('found %r in the module source' % token)
if problems:
    sys.stderr.write('READ-ONLY VIOLATION: ' + '; '.join(problems) + '\n')
    sys.exit(1)
sys.stdout.write('run_hyperliquid_feed: read-only assertions OK\n')
PYEOF

# ---------------------------------------------------------------------------
# Gate 3: the database must be reachable and must not be exclusively locked.
#
# db/trading.db is written concurrently by the Polymarket shadow loop. We only
# OBSERVE journal_mode here; switching it on a db another process holds open is
# how you corrupt somebody else's session.
# ---------------------------------------------------------------------------
"${PY[@]}" - <<'PYEOF' || die "database preflight failed (see above)"
import os
import sqlite3
import sys

path = os.environ.get('TRADING_DB_PATH', 'db/trading.db')
if not os.path.exists(path):
    sys.stdout.write('run_hyperliquid_feed: %s does not exist yet, it will be created\n' % path)
    sys.exit(0)
try:
    conn = sqlite3.connect('file:%s?mode=ro' % path, uri=True, timeout=5.0)
    mode = conn.execute('PRAGMA journal_mode').fetchone()[0]
    conn.close()
except sqlite3.Error as exc:
    sys.stderr.write('cannot open %s: %s\n' % (path, exc))
    sys.exit(1)
sys.stdout.write('run_hyperliquid_feed: %s journal_mode=%s (observed, not set)\n' % (path, mode))
PYEOF

# ---------------------------------------------------------------------------
# Launch.
# ---------------------------------------------------------------------------
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/hyperliquid_feed_${STAMP}.log"

# NOTE the two log files. This wrapper writes a session log with a full UTC
# TIMESTAMP; the python module additionally writes a per-DAY rolling log at
# logs/hyperliquid_client_<YYYYMMDD>.log. The wrapper log is one session, the
# module log is one day across sessions.
{
    echo "=== hyperliquid feed session ${STAMP} (UTC) ==="
    echo "repo:     ${REPO_ROOT}"
    echo "commit:   $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "mode:     READ-ONLY DATA FEED (no wallet, no signer, no order API)"
    echo "args:     $*"
    echo "db:       db/trading.db -> table hyperliquid_positions (append-only snapshots)"
    echo "module log: logs/hyperliquid_client_$(date -u +%Y%m%d).log"
    echo "python:   $("${PY[@]}" --version 2>&1)"
    echo "NOTE: botctl halt does NOT stop this feed. It records, it does not trade."
    echo "==="
} | tee "$LOG"

echo "run_hyperliquid_feed: log=${LOG}"

# Process substitution rather than a plain pipe so ${CHILD} is PYTHON's pid and
# not tee's. A trap that kills the wrong end of a pipeline leaves the poller
# running and reports a clean shutdown.
"${PY[@]}" -u -m engine.feeds.hyperliquid_client "$@" \
    > >(tee -a "$LOG") 2>&1 &
CHILD=$!
echo "$CHILD" > "$PIDFILE"

echo "run_hyperliquid_feed: python pid=${CHILD} (pidfile ${PIDFILE})"

cleanup() { rm -f "$PIDFILE"; }
trap 'echo "run_hyperliquid_feed: forwarding stop to pid ${CHILD}" >&2; kill -TERM "${CHILD}" 2>/dev/null || true' INT TERM
trap cleanup EXIT

# `wait` returns as soon as a trapped signal arrives, BEFORE the child has
# finished flushing its final stats line. So wait again until the pid is
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

echo "run_hyperliquid_feed: exited with status ${STATUS}"
exit "${STATUS}"
