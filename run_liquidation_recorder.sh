#!/usr/bin/env bash
#
# run_liquidation_recorder.sh - start the 24/7 perp liquidation recorder.
#
# Wraps `python3 -m engine.feeds.liquidation_recorder`. That module subscribes
# to Bybit's `allLiquidation.<SYMBOL>` public websocket and appends rows to the
# `liquidations` table in db/trading.db.
#
# BYBIT ONLY, as of 2026-08-18. Binance was removed (geoblocked: HTTP 451, and
# its socket CONNECTS while delivering nothing, so it logged uptime and recorded
# zero). Hyperliquid has no public venue-wide liquidation feed to add at all -
# measured, see research/hyperliquid/liquidation_source_probe.md. The module
# refuses both by name with the measurement attached, so this default and the
# module default cannot drift apart silently (convention 23: a fix at one site
# is not a fix - EXCHANGES below is the second site).
#
# THIS IS NOT A TRADING PROCESS. It is a tape recorder. It holds no key, imports
# no wallet, no signer and no order API, and issues no verb but INSERT OR IGNORE
# on one table. There is deliberately NO mode gate here, unlike run_shadow.sh
# and run_polymarket_shadow.sh: a paper/live distinction is meaningless for a
# read-only public data feed, and adding a gate that cannot fail would teach an
# operator that the gates in those two scripts are decorative. They are not.
#
# What IS gated is the database, because another process owns it.
#
# Usage:
#   ./run_liquidation_recorder.sh                 # foreground, Ctrl-C to stop
#   nohup ./run_liquidation_recorder.sh > logs/liquidation_recorder.out 2>&1 &
#
#   SYMBOLS='BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT' ./run_liquidation_recorder.sh
#   DRY_RUN=1 DURATION_SEC=60 ./run_liquidation_recorder.sh   # smoke test
#
# Stop:
#   Ctrl-C, or `kill <pid in logs/liquidation_recorder.pid>`. Both are graceful:
#   the signal is forwarded to python, which drains the in-memory batch to
#   sqlite, prints a FINAL stats line per exchange and asserts its accounting
#   identities before exiting.
#
# NOTE the kill switch does NOT apply. `engine/halt.py` blocks ENTRIES. This
# process takes no entries, and halting the tape during an incident is the exact
# moment you most want the tape. So a HALT is reported at startup for the
# operator's awareness and is explicitly NOT a reason to refuse to start.
#
set -euo pipefail

# Run from the repo root regardless of where the operator invoked this from:
# the db and log paths below are relative.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# `env -u PYTHONPATH` everywhere (convention 14): Hermes leaks its 3.11 venv
# onto PYTHONPATH and numpy then fails to import in a way that reads as a broken
# install.
PY=(env -u PYTHONPATH python3)

SYMBOLS="${SYMBOLS:-BTCUSDT,ETHUSDT,SOLUSDT}"
DB_PATH="${DB_PATH:-db/trading.db}"
LOG_LEVEL="${LOG_LEVEL:-INFO}"
STATS_INTERVAL="${STATS_INTERVAL:-60}"
# The module's SUPPORTED_EXCHANGES is the source of truth; this must agree with
# it. Setting EXCHANGES=binance or =hyperliquid here does NOT resurrect them -
# the module exits 2 with the reason. That is deliberate: a venue that records
# nothing while looking connected is worse than one that refuses to start.
EXCHANGES="${EXCHANGES:-bybit}"
PID_FILE="${PID_FILE:-logs/liquidation_recorder.pid}"

die() {
    echo "run_liquidation_recorder: REFUSING TO START: $*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Gate 1: one recorder at a time.
#
# Two recorders on the same stream is not a correctness bug - the deterministic
# id and INSERT OR IGNORE make the second one's rows collapse into the first's -
# but it doubles the socket count, doubles the write contention on a db the
# shadow loop is using, and makes the `duplicates` counter meaningless as a
# reconnect diagnostic. Convention 25: a PID in a file is a claim, so this
# CHECKS it with kill -0 rather than trusting the file's existence.
# ---------------------------------------------------------------------------
mkdir -p "$(dirname "$PID_FILE")" logs
if [ -f "$PID_FILE" ]; then
    OLD_PID="$(cat "$PID_FILE" 2>/dev/null || echo '')"
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        die "already running as pid ${OLD_PID} (per ${PID_FILE}). Stop it first: kill ${OLD_PID}"
    fi
    echo "run_liquidation_recorder: stale pid file (${OLD_PID:-empty} is not running); removing" >&2
    rm -f "$PID_FILE"
fi

# ---------------------------------------------------------------------------
# Gate 2: the websocket client must be importable.
#
# Checked HERE rather than discovered 30 seconds into a nohup'd background run,
# where the traceback lands in a file nobody is watching.
# ---------------------------------------------------------------------------
"${PY[@]}" -c 'import websockets, sys; sys.stdout.write("run_liquidation_recorder: websockets %s\n" % websockets.__version__)' \
    || die "the 'websockets' package is not importable by this python"

# ---------------------------------------------------------------------------
# Gate 3: the database must be in WAL, because another process is writing it.
#
# db/trading.db is held open by the Polymarket shadow loop. WAL is what lets a
# second writer and the dashboard's read-only connection coexist. The recorder
# will NOT switch the journal mode of an existing db (that is an exclusive,
# global operation on a file somebody else has open) - so if it is somehow not
# WAL, that is an operator problem to resolve deliberately, not something to
# paper over at 3am. A db that does not exist yet is fine: the recorder creates
# it and sets WAL itself, which is safe precisely because nobody else has it.
# ---------------------------------------------------------------------------
if [ -f "$DB_PATH" ]; then
    JOURNAL="$("${PY[@]}" - "$DB_PATH" <<'PYEOF'
import sqlite3
import sys
# `PRAGMA journal_mode` with no `=` is a READ. It does not modify the file and
# it does not take a write lock, so this is safe against the live writer.
conn = sqlite3.connect(sys.argv[1], timeout=5.0)
conn.execute('PRAGMA busy_timeout=5000;')
row = conn.execute('PRAGMA journal_mode;').fetchone()
conn.close()
sys.stdout.write((row[0] if row else '') or '')
PYEOF
)"
    if [ "$JOURNAL" != "wal" ]; then
        die "${DB_PATH} journal_mode is '${JOURNAL}', not 'wal'. Another process writes this db and the recorder will not switch modes underneath it. Resolve deliberately before starting."
    fi
    echo "run_liquidation_recorder: ${DB_PATH} journal_mode=${JOURNAL} (ok, concurrent writers supported)"
else
    echo "run_liquidation_recorder: ${DB_PATH} does not exist; it will be created in WAL mode"
fi

# ---------------------------------------------------------------------------
# Launch.
# ---------------------------------------------------------------------------
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/liquidation_recorder_run_${STAMP}.log"

ARGS=(--symbols "$SYMBOLS" --db "$DB_PATH" --log-level "$LOG_LEVEL"
      --stats-interval "$STATS_INTERVAL" --exchanges "$EXCHANGES")
[ -n "${DRY_RUN:-}" ] && ARGS+=(--dry-run)
[ -n "${DURATION_SEC:-}" ] && ARGS+=(--duration-sec "$DURATION_SEC")

{
    echo "=== liquidation recorder session ${STAMP} (UTC) ==="
    echo "repo:      ${REPO_ROOT}"
    echo "commit:    $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "exchanges: ${EXCHANGES}"
    echo "binance:   DEAD - geoblocked (HTTP 451; socket connects, delivers 0)"
    echo "hyperliq:  DEAD - no public venue-wide liquidation feed exists"
    echo "bybit:     wss://stream.bybit.com/v5/public/linear"
    echo "           topic allLiquidation.<SYMBOL>; v5 has NO wildcard, so the"
    echo "           symbol list below is the entire Bybit coverage"
    echo "symbols:   ${SYMBOLS}"
    echo "db:        ${DB_PATH} -> table 'liquidations' (append-only, INSERT OR IGNORE)"
    echo "side col:  the side that was LIQUIDATED, i.e. the exchange ORDER side INVERTED"
    echo "dry run:   ${DRY_RUN:-0}"
    echo "python:    $("${PY[@]}" --version 2>&1)"
    echo "halted:    $("${PY[@]}" -c 'from engine.halt import is_halted; print(is_halted())' 2>/dev/null || echo unknown) (informational; a HALT does NOT stop the tape)"
    echo "==="
} | tee "$LOG"

echo "run_liquidation_recorder: log=${LOG} (module also writes logs/liquidation_recorder_<date>.log)"

# Process substitution rather than a plain pipe so ${CHILD} is PYTHON's pid and
# not tee's. A trap that kills the wrong end of a pipeline leaves the recorder
# running and reports a clean shutdown.
"${PY[@]}" -u -m engine.feeds.liquidation_recorder "${ARGS[@]}" \
    > >(tee -a "$LOG") 2>&1 &
CHILD=$!
echo "$CHILD" > "$PID_FILE"

echo "run_liquidation_recorder: python pid=${CHILD} (pid file ${PID_FILE})"

trap 'echo "run_liquidation_recorder: forwarding stop to pid ${CHILD}" >&2; kill -TERM "${CHILD}" 2>/dev/null || true' INT TERM

# `wait` returns as soon as a trapped signal arrives, BEFORE the child has
# finished draining its batch to sqlite. So wait again until the pid is
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

rm -f "$PID_FILE"
echo "run_liquidation_recorder: exited with status ${STATUS}"
exit "${STATUS}"
