#!/usr/bin/env bash
#
# run_shadow.sh - start a shadow (paper) trading session.
#
# Wraps `python3 -m engine.main`, which is the only entrypoint that runs the
# collector -> scanner -> executor loop. botctl.py has no run subcommand; it is
# the control surface (status / halt / resume), not the launcher.
#
# This script refuses to start unless the config resolves to paper mode. The
# engine refuses too (engine/main.py exits 1 on mode != 'paper'), so this is the
# outer of two independent checks rather than the only one. A mode gate that
# only one reader enforces is a mode gate with a single point of failure.
#
# Preflight checklist for a human operator: docs/SHADOW-PREFLIGHT.md
#
# Usage:
#   ./run_shadow.sh
#
# Stop:
#   Ctrl-C, or from another terminal:
#     env -u PYTHONPATH python3 botctl.py halt "reason"
#
set -euo pipefail

# Run from the repo root regardless of where the operator invoked this from:
# engine/main.py loads config.yaml relative to the repo root, and the log
# directory below is a relative path.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

# `env -u PYTHONPATH` everywhere (convention 14): Hermes leaks its 3.11 venv
# onto PYTHONPATH and numpy then fails to import in a way that reads as a broken
# install.
PY=(env -u PYTHONPATH python3)

die() {
    echo "run_shadow: REFUSING TO START: $*" >&2
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
# Gate 3: the kill switch must be clear.
#
# Starting while halted is not dangerous (the executor closes everything and
# drains the queue) but it produces a session that looks running and trades
# nothing, which is the most confusing possible shadow run. Resume explicitly.
# The check imports engine.halt rather than testing for a file, so this script
# can never disagree with the engine about where the HALT file lives.
# ---------------------------------------------------------------------------
if "${PY[@]}" -c 'import sys
from engine.halt import is_halted
sys.exit(0 if is_halted() else 1)'; then
    echo "run_shadow: HALT file is present:" >&2
    "${PY[@]}" botctl.py status >&2 || true
    die "clear it first: env -u PYTHONPATH python3 botctl.py resume --ack <halt_id>"
fi

# ---------------------------------------------------------------------------
# Launch.
# ---------------------------------------------------------------------------
mkdir -p logs
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="logs/shadow_${STAMP}.log"

echo "run_shadow: mode=paper, log=${LOG}"
echo "run_shadow: stop with Ctrl-C, or: env -u PYTHONPATH python3 botctl.py halt \"reason\""

{
    echo "=== shadow session ${STAMP} (UTC) ==="
    echo "repo:   ${REPO_ROOT}"
    echo "commit: $(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
    echo "mode:   ${MODE}"
    echo "python: $("${PY[@]}" --version 2>&1)"
    echo "==="
} | tee "$LOG"

# Crypto only. The Polymarket paper adapter is not wired into engine.main and
# writes its own decision log to research/polymarket_paper/. See
# docs/SHADOW-PREFLIGHT.md before assuming a shadow run covers both venues.
"${PY[@]}" -m engine.main 2>&1 | tee -a "$LOG"
