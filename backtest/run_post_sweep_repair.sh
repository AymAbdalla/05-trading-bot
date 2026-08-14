#!/bin/bash
# Post-sweep repair sequence (D-253/D-254), 2026-08-13 late evening.
#
# Puts the graveyard into a state where its rows all came from current code.
# The 16:01 sweep ran pre-D-249 contract sizing and only 49 of today's 54
# strategies, so its FUTURES rows are wrong and its v4/v5 rows are missing.
#
#   1. wait for the main sweep AND run_queued_chain.sh to finish
#   2. purge every contract row (dry run first, printed for the log)
#   3. incremental rebuild - refills futures under the fixed cost model AND
#      backfills v4/v5, in one pass
#   4. judge.py evidence pack against the repaired graveyard
#
# NOT ARMED. Unlike run_queued_chain.sh this does not launch itself, because
# step 2 deletes 12,936 rows (51 of them PASS) and Aym owes that call. Run it
# deliberately:
#
#   nohup bash backtest/run_post_sweep_repair.sh --confirm > logs/post_sweep_repair.log 2>&1 &
#
# Progress: tail -f logs/post_sweep_repair.log

set -u

cd "$(dirname "$0")/.." || exit 1

if [ "${1:-}" != "--confirm" ]; then
    echo "This deletes every FUTURES/OPTIONS row from the graveyard and rebuilds them."
    echo "Re-run with --confirm once that call is made. Nothing done."
    exit 1
fi

LOG=logs/post_sweep_repair.log
mkdir -p logs
say() { echo "$(date '+%F %T') $*" | tee -a "$LOG"; }

# `python3` alone breaks in agent-spawned sessions: Hermes leaks its 3.11 venv
# onto PYTHONPATH and numpy fails to import (D-257). Clear it explicitly so
# this behaves the same however it was launched.
PY="env -u PYTHONPATH python3"

say "waiting for the main sweep to exit"
while pgrep -f "backtest/run_incremental_graveyard.py" >/dev/null; do sleep 300; done

# The chain runs its own incremental pass plus dispersion/horizon/PLR. Purging
# underneath it would delete rows it is mid-way through reasoning about.
say "main sweep done; waiting for run_queued_chain.sh to finish"
while pgrep -f "backtest/run_queued_chain.sh" >/dev/null; do sleep 300; done

say "chain done -> purge dry run (recorded before anything is deleted)"
nice -n 10 $PY backtest/purge_stale_futures.py >> "$LOG" 2>&1

say "-> purge --apply"
nice -n 10 $PY backtest/purge_stale_futures.py --apply >> "$LOG" 2>&1
rc=$?
if [ $rc -ne 0 ]; then
    say "purge FAILED (exit=$rc) - stopping before the rebuild."
    say "The graveyard is unchanged or restorable from research/graveyard/archive/."
    exit $rc
fi

say "-> incremental rebuild (futures under fixed sizing + v4/v5 backfill)"
nice -n 10 $PY backtest/run_incremental_graveyard.py > logs/graveyard_post_purge.log 2>&1
say "rebuild exit=$?"

say "-> judge evidence pack"
nice -n 10 $PY agents/judge.py \
    --graveyard research/graveyard/v0_graveyard_full.json \
    --out research/judge_evidence_pack.json >> "$LOG" 2>&1
say "judge exit=$?"

say "REPAIR COMPLETE"
say "Check the pack's status field: UNREADABLE or PROVISIONAL means do not cite it."
