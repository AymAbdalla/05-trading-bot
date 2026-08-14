#!/bin/bash
# Queued experiment chain, 2026-08-13 evening (D-238..D-241).
# Runs detached via nohup so it survives any Claude Code session.
#
# Order (each stage waits for the previous):
#   1. wait for the main graveyard sweep (PID arg or process match) to exit
#   2. incremental graveyard pass  - picks up v4 + CPI + v5 combos only
#   3. dispersion gate full run    - Lab v5 P3 (research/graveyard/dispersion_gate.json)
#   4. horizon ladder full run     - Lab v5 P1 (research/cross_sectional/horizon_ladder_full.json)
#   5. PLR full run                - Lab v3 #5 (research/cross_sectional/plr_full.json)
#
# Everything niced so an interactive session stays usable.
# Progress: tail -f logs/queued_chain.log

cd "$(dirname "$0")/.." || exit 1
LOG=logs/queued_chain.log

echo "$(date '+%F %T') chain armed; waiting for main graveyard sweep to exit" >> "$LOG"
while pgrep -f "backtest/run_incremental_graveyard.py" >/dev/null; do sleep 300; done

echo "$(date '+%F %T') main sweep done -> incremental pass (v4+CPI+v5)" >> "$LOG"
nice -n 10 python3 backtest/run_incremental_graveyard.py > logs/graveyard_v4_cpi_pass.log 2>&1
echo "$(date '+%F %T') incremental pass exit=$? -> dispersion gate" >> "$LOG"

nice -n 10 python3 backtest/dispersion_gate.py > logs/dispersion_gate.log 2>&1
echo "$(date '+%F %T') dispersion gate exit=$? -> horizon ladder" >> "$LOG"

mkdir -p research/cross_sectional
nice -n 10 python3 backtest/run_horizon_ladder.py --cost-model both \
    --out research/cross_sectional/horizon_ladder_full.json > logs/horizon_ladder.log 2>&1
echo "$(date '+%F %T') horizon ladder exit=$? -> PLR" >> "$LOG"

nice -n 10 python3 backtest/cross_sectional.py --strategy plr \
    --out research/cross_sectional/plr_full.json > logs/plr_full.log 2>&1
echo "$(date '+%F %T') PLR exit=$? - CHAIN COMPLETE" >> "$LOG"
