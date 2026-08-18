#!/bin/bash
# Install (or remove) the pre-commit conflict check for this repository.
#
# WHAT THIS INSTALLS
#   A ~10 line shim at <hooks-dir>/pre-commit that does nothing except exec
#   scripts/pre-commit-conflict-check. The real logic stays in the tree, under
#   version control, reviewable and diffable. .git/hooks is none of those things,
#   so putting logic there means nobody ever reads it again.
#
# WHAT IT WILL NOT TOUCH
#   Nothing outside <hooks-dir>/pre-commit. It does not stage anything, does not
#   commit, does not run git add, does not modify engine/, tests/, db/ or any
#   source file, and does not touch a running process. It reads db/trading.db
#   only when the hook itself runs, and then only for SELECT.
#
# IT WILL NOT SILENTLY DESTROY AN EXISTING HOOK
#   If <hooks-dir>/pre-commit exists and is not ours, it is moved to
#   pre-commit.backup.<UTC stamp> and the move is printed. If it is already
#   ours, it is simply re-installed (idempotent: run it as often as you like).
#
# USAGE
#   scripts/install_conflict_hook.sh              install or re-install
#   scripts/install_conflict_hook.sh --uninstall  remove ours, list backups
#   scripts/install_conflict_hook.sh --status     report, change nothing
#
# Convention 14 (`env -u PYTHONPATH python3`) is enforced inside the hook, not
# here; this script runs no python.
set -euo pipefail

MARKER="aym-trading-bot conflict-check hook v1"
HOOK_NAME="pre-commit"
REAL_HOOK_REL="scripts/pre-commit-conflict-check"

usage() {
  echo "usage: $(basename "$0") [--uninstall | --status | --help]"
}

MODE="install"
case "${1:-}" in
  "")            MODE="install" ;;
  --uninstall)   MODE="uninstall" ;;
  --status)      MODE="status" ;;
  -h|--help)     usage; exit 0 ;;
  *)             echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
esac

# ---------------------------------------------------------------------------
# Locate the repo and its hooks directory.
#
# `git rev-parse --git-path hooks` rather than a hardcoded .git/hooks: in a
# worktree or a submodule .git is a FILE, not a directory, and the hardcoded
# path would install a hook that git never runs. core.hooksPath wins over both
# when it is set, so it is checked first for the same reason.
# ---------------------------------------------------------------------------
if ! REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "ERROR: not inside a git work tree." >&2
  exit 1
fi
cd "$REPO_ROOT"

HOOKS_DIR="$(git config --get core.hooksPath 2>/dev/null || true)"
if [ -n "$HOOKS_DIR" ]; then
  echo "note: core.hooksPath is set, installing into it rather than .git/hooks"
else
  HOOKS_DIR="$(git rev-parse --git-path hooks)"
fi
case "$HOOKS_DIR" in
  /*) ;;
  *) HOOKS_DIR="$REPO_ROOT/$HOOKS_DIR" ;;
esac

HOOK_PATH="$HOOKS_DIR/$HOOK_NAME"
REAL_HOOK="$REPO_ROOT/$REAL_HOOK_REL"

echo "repo:      $REPO_ROOT"
echo "hooks dir: $HOOKS_DIR"
echo "hook:      $HOOK_PATH"
echo "logic:     $REAL_HOOK_REL"
echo

is_ours() { [ -f "$1" ] && grep -qF "$MARKER" "$1" 2>/dev/null; }

list_backups() {
  local found=0
  for b in "$HOOKS_DIR/$HOOK_NAME".backup.*; do
    [ -e "$b" ] || continue
    [ "$found" -eq 0 ] && echo "existing backups (left alone, never auto-restored):"
    found=1
    echo "  $b"
  done
  [ "$found" -eq 0 ] && echo "no backups present."
  return 0
}

# ---------------------------------------------------------------------------
# --status
# ---------------------------------------------------------------------------
if [ "$MODE" = "status" ]; then
  if [ ! -e "$HOOK_PATH" ]; then
    echo "status: NOT INSTALLED (no $HOOK_NAME hook)."
  elif is_ours "$HOOK_PATH"; then
    echo "status: INSTALLED (ours, marker found)."
  else
    echo "status: a $HOOK_NAME hook exists but is NOT ours. Installing would"
    echo "        back it up first."
  fi
  if [ -x "$REAL_HOOK" ]; then
    echo "logic:  present and executable."
  elif [ -f "$REAL_HOOK" ]; then
    echo "logic:  present but NOT executable -- run chmod +x $REAL_HOOK_REL"
  else
    echo "logic:  MISSING at $REAL_HOOK_REL"
  fi
  echo
  list_backups
  exit 0
fi

# ---------------------------------------------------------------------------
# --uninstall
# ---------------------------------------------------------------------------
if [ "$MODE" = "uninstall" ]; then
  if [ ! -e "$HOOK_PATH" ]; then
    echo "uninstall: nothing to do, no $HOOK_NAME hook present."
  elif is_ours "$HOOK_PATH"; then
    rm -f "$HOOK_PATH"
    echo "uninstall: removed our $HOOK_NAME hook."
  else
    echo "uninstall: REFUSING. $HOOK_PATH exists but is NOT ours (no marker)."
    echo "           Somebody else's hook is not this script's to delete."
    exit 1
  fi
  echo "uninstall: $REAL_HOOK_REL is left in the tree; it is version-controlled."
  echo
  list_backups
  echo
  echo "If you want a backup back, move it into place yourself:"
  echo "  mv $HOOKS_DIR/$HOOK_NAME.backup.<stamp> $HOOK_PATH"
  exit 0
fi

# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------
if [ ! -f "$REAL_HOOK" ]; then
  echo "ERROR: $REAL_HOOK_REL is missing. The shim would exec nothing." >&2
  exit 1
fi

mkdir -p "$HOOKS_DIR"
chmod +x "$REAL_HOOK"

if [ -e "$HOOK_PATH" ]; then
  if is_ours "$HOOK_PATH"; then
    echo "found an existing hook and it is OURS -- re-installing over it."
  else
    STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
    BACKUP="$HOOK_PATH.backup.$STAMP"
    mv "$HOOK_PATH" "$BACKUP"
    echo "found an existing $HOOK_NAME hook that is NOT ours."
    echo "BACKED IT UP: $BACKUP"
    echo "It was NOT deleted and it is NOT chained. If you need both hooks to"
    echo "run, call the backup from the new hook by hand."
  fi
fi

cat >"$HOOK_PATH" <<EOF
#!/bin/bash
# $MARKER
#
# GENERATED by scripts/install_conflict_hook.sh. Do not edit this file: it is
# outside version control and will be overwritten on the next install. The real
# logic lives in $REAL_HOOK_REL.
#
# Bypass: SKIP_CONFLICT_CHECK=1 git commit ...   or   git commit --no-verify
set -euo pipefail

ROOT="\$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "\$ROOT" ]; then
  echo "conflict-check: WARNING -- cannot find the repo root; check COULD NOT RUN. Allowing."
  exit 0
fi

CHECK="\$ROOT/$REAL_HOOK_REL"
if [ ! -x "\$CHECK" ]; then
  echo "conflict-check: WARNING -- \$CHECK is missing or not executable."
  echo "conflict-check: the check COULD NOT RUN, so NOTHING was verified. Allowing."
  echo "conflict-check: reinstall with scripts/install_conflict_hook.sh"
  exit 0
fi

exec "\$CHECK" "\$@"
EOF

chmod +x "$HOOK_PATH"

echo
echo "installed."
echo "  $HOOK_PATH  (shim, generated)"
echo "  $REAL_HOOK_REL  (logic, version-controlled)"
echo
echo "It WARNS on active checkouts and REFUSES only when a staged file's hash"
echo "does not match the last write recorded in file_coordination."
echo
echo "Try it without committing:"
echo "  $REAL_HOOK_REL"
echo "Bypass:"
echo "  SKIP_CONFLICT_CHECK=1 git commit ...   or   git commit --no-verify"
echo "Remove:"
echo "  scripts/install_conflict_hook.sh --uninstall"
