#!/usr/bin/env bash
#
# install_reasoning_cron.sh - add the 4-hourly reasoning cycle to the crontab.
#
# WHY THIS IS A SCRIPT AND NOT A COMMAND SOMEBODY RAN
#
# On this machine `crontab <file>` HANGS when it is run from a non-interactive
# session. `crontab -l` works (reading is fine), the binary is setuid root, and
# /var/at/tabs is a normal empty root-owned directory, so this is not a
# permissions bug in the repo: it is macOS TCC waiting on a GUI approval that a
# headless session cannot answer. Two attempts were left hanging in `S` state
# and killed. So the append is packaged here for Aym to run once from
# Terminal.app, where the prompt can be approved.
#
# WHAT IT DOES
#
#   1. Reads the existing crontab. `crontab -l` exits 1 with "no crontab for
#      <user>" when empty, and that text must NOT end up in the new crontab, so
#      the empty case is handled explicitly rather than with `|| true`.
#   2. Refuses to add a second copy if the marker is already there. Running it
#      twice must not schedule the cycle twice; two firings would collide, and
#      although the lock would make the second exit 75, a job that is designed
#      to be blocked is a job somebody will misread.
#   3. Prints the before and the after.
#
#   --dry-run   print the crontab that WOULD be installed and install nothing.
#   CRONTAB_BIN overrides the `crontab` binary, which is how the test drives
#               this without touching the real one.
#
set -euo pipefail

CRONTAB_BIN="${CRONTAB_BIN:-crontab}"
PROJECT_DIR="/Users/aympulse/aym/projects/05-trading-bot"
MARKER="scripts/run_reasoning_cycle.sh"

DRY_RUN=0
if [ "${1:-}" = "--dry-run" ]; then
    DRY_RUN=1
fi

BLOCK="$(cat <<EOF
# Reasoning cycle: Forge (Opus proposals) + the critic (post-mortem, kill
# recommendations, hypothesis_graph). Every 4 hours at :20.
# The wrapper sets PATH and cwd itself; cron's environment is not a shell's.
# Exit 3 = the cycle ran but every model turn COULD NOT RUN (NOT_TESTED).
# Exit 75 = another cycle held the lock, so this firing did nothing on purpose.
20 */4 * * * ${PROJECT_DIR}/scripts/run_reasoning_cycle.sh >> ${PROJECT_DIR}/logs/reasoning_cycle_cron.log 2>&1
EOF
)"

CURRENT="$(mktemp /tmp/crontab-current.XXXXXX)"
NEXT="$(mktemp /tmp/crontab-next.XXXXXX)"
trap 'rm -f "$CURRENT" "$NEXT"' EXIT

echo "===== CRONTAB BEFORE ====="
if "$CRONTAB_BIN" -l > "$CURRENT" 2>/dev/null; then
    cat "$CURRENT"
else
    : > "$CURRENT"
    echo "(no crontab for $(whoami))"
fi
echo "===== /BEFORE ====="

if grep -qF "$MARKER" "$CURRENT"; then
    echo "already scheduled (${MARKER} is in the crontab); nothing to do"
    exit 0
fi

cp "$CURRENT" "$NEXT"
# A crontab whose last line has no newline loses that line on some cron
# implementations. Guarantee the separator.
if [ -s "$NEXT" ] && [ "$(tail -c 1 "$NEXT" | wc -l)" -eq 0 ]; then
    echo "" >> "$NEXT"
fi
printf '%s\n' "$BLOCK" >> "$NEXT"

if [ "$DRY_RUN" -eq 1 ]; then
    echo "===== CRONTAB THAT WOULD BE INSTALLED (--dry-run) ====="
    cat "$NEXT"
    echo "===== /DRY RUN, nothing installed ====="
    exit 0
fi

"$CRONTAB_BIN" "$NEXT"

echo "===== CRONTAB AFTER ====="
"$CRONTAB_BIN" -l
echo "===== /AFTER ====="
