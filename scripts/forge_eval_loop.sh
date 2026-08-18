#!/bin/bash
# Run Forge against the live shadow-trading results and log the run.
#
# `env -u PYTHONPATH` is convention 14: Hermes leaks its 3.11 venv onto
# PYTHONPATH and numpy then fails in a way that looks like a broken install.
#
# This writes proposals under strategies/proposals/ and appends one record to
# strategies/proposals/forge_runs.jsonl. It never touches strategies/builtin/,
# engine/, or backtest/, and it never runs a sweep.
set -euo pipefail

PROJECT_DIR="/Users/aympulse/aym/projects/05-trading-bot"
cd "$PROJECT_DIR"

DB_PATH="${1:-db/trading.db}"

mkdir -p logs
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/forge_eval_${TS}.log"

{
  echo "=== forge_eval_loop ${TS} ==="
  echo "db: ${DB_PATH}"
  echo
} >"$LOG"

# Both streams into the log AND to the terminal. `set -o pipefail` above means
# a Forge failure is still the exit status of this script rather than tee's.
env -u PYTHONPATH python3 agents/forge.py --shadow-results "$DB_PATH" 2>&1 \
  | tee -a "$LOG"

echo "log: ${PROJECT_DIR}/${LOG}"
