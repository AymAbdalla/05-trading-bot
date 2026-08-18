#!/usr/bin/env bash
#
# run_reasoning_cycle.sh - the cron entry point for the 4-hourly reasoning cycle.
#
# Everything here exists because cron's environment is not a login shell's.
# Three things break under cron and each is fixed explicitly rather than hoped
# for:
#
#   1. PATH. Cron gives you roughly /usr/bin:/bin. `claude` lives in
#      ~/.local/bin, so without this the CLI is not found, every turn is
#      LLMUnavailable, and the cycle exits 3 (NOT_TESTED) four times an hour
#      wondering why. The path is set here, not inherited.
#   2. PYTHONPATH. Convention 14: Hermes leaks its 3.11 venv onto PYTHONPATH
#      and numpy then fails like a broken install. `env -u PYTHONPATH` strips
#      it, and the Hermes venv is deliberately absent from the PATH below.
#   3. cwd. Cron starts in $HOME. Every relative path in the repo (config.yaml,
#      db/trading.db, strategies/proposals/) assumes the repo root.
#
# It writes nothing itself. `scripts/reasoning_cycle.py` owns the lock, the
# timestamped log under logs/, and the run record in
# logs/reasoning_cycle_runs.jsonl.
#
# Exit codes are passed through unchanged:
#   0   the cycle ran
#   1   a stage failed (traceback is in the timestamped log)
#   3   the cycle ran but every model turn COULD NOT RUN (convention 11:
#       NOT_TESTED, which is not "the model had no ideas")
#   75  another cycle holds the lock; this firing did nothing on purpose
#
# Any argument given here is forwarded, so a smoke run is:
#   scripts/run_reasoning_cycle.sh --skip-model --out-dir /tmp/rc
#
set -euo pipefail

PROJECT_DIR="/Users/aympulse/aym/projects/05-trading-bot"
PY="${PROJECT_DIR}/.venv/bin/python"

# Explicit, and WITHOUT ~/.hermes/hermes-agent/venv/bin. A login shell has that
# on PATH; a cron job must not, because it is the 3.11 venv convention 14 is
# about.
export PATH="/Users/aympulse/.local/bin:/opt/homebrew/bin:/opt/homebrew/sbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

cd "$PROJECT_DIR"

echo "=== run_reasoning_cycle $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

if [ ! -x "$PY" ]; then
    echo "run_reasoning_cycle: no interpreter at ${PY}" >&2
    exit 1
fi

# Not fatal. A missing `claude` makes every turn NOT_TESTED, which the cycle
# already reports honestly and exits 3 for; saying so here just makes the cron
# log readable without opening the run record.
if ! command -v claude >/dev/null 2>&1; then
    echo "run_reasoning_cycle: WARNING - 'claude' is not on PATH, so every " \
         "model turn will be NOT_TESTED (convention 11: could not run, not " \
         "'no result')." >&2
fi

set +e
env -u PYTHONPATH "$PY" scripts/reasoning_cycle.py "$@"
STATUS=$?
set -e

echo "run_reasoning_cycle: exit ${STATUS}"
exit "$STATUS"
