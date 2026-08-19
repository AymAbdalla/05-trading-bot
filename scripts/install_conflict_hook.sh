#!/bin/bash
# Install (or remove) the pre-commit conflict check for this repository.
#
# WHAT THIS INSTALLS
#   A ~10 line shim at <hooks-dir>/pre-commit AND one at <hooks-dir>/commit-msg,
#   each doing nothing except exec scripts/pre-commit-conflict-check, forwarding
#   its arguments. That script picks its job from argv: no argument means the
#   staged-hash and provenance checks, a message-file path means the D-335
#   Agent-Id trailer check. git only hands the composed commit message to a
#   commit-msg hook, so WITHOUT THE SECOND SHIM THE TRAILER IS NEVER CHECKED.
#
#   The real logic stays in the tree, under version control, reviewable and
#   diffable. .git/hooks is none of those things, so putting logic there means
#   nobody ever reads it again.
#
# WHAT IT WILL NOT TOUCH
#   Nothing outside <hooks-dir>/pre-commit. It does not stage anything, does not
#   commit, does not run git add, does not modify engine/, tests/, db/ or any
#   source file, and does not touch a running process. It reads db/trading.db
#   only when the hook itself runs, and then only for SELECT.
#
# IT WILL NOT SILENTLY DESTROY AN EXISTING HOOK
#   If <hooks-dir>/<name> exists and is not ours, it is moved to
#   <name>.backup.<UTC stamp> and the move is printed. If it is already ours,
#   it is simply re-installed (idempotent: run it as often as you like). This
#   holds for both hooks, independently.
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
HOOK_NAMES=(pre-commit commit-msg)
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

REAL_HOOK="$REPO_ROOT/$REAL_HOOK_REL"

echo "repo:      $REPO_ROOT"
echo "hooks dir: $HOOKS_DIR"
echo "hooks:     ${HOOK_NAMES[*]}"
echo "logic:     $REAL_HOOK_REL"
echo

is_ours() { [ -f "$1" ] && grep -qF "$MARKER" "$1" 2>/dev/null; }

list_backups() {
  local found=0 name
  for name in "${HOOK_NAMES[@]}"; do
    for b in "$HOOKS_DIR/$name".backup.*; do
      [ -e "$b" ] || continue
      [ "$found" -eq 0 ] && echo "existing backups (left alone, never auto-restored):"
      found=1
      echo "  $b"
    done
  done
  [ "$found" -eq 0 ] && echo "no backups present."
  return 0
}

# ---------------------------------------------------------------------------
# --status
# ---------------------------------------------------------------------------
if [ "$MODE" = "status" ]; then
  for HOOK_NAME in "${HOOK_NAMES[@]}"; do
    HOOK_PATH="$HOOKS_DIR/$HOOK_NAME"
    if [ ! -e "$HOOK_PATH" ]; then
      echo "status: $HOOK_NAME: NOT INSTALLED."
      if [ "$HOOK_NAME" = "commit-msg" ]; then
        echo "        The D-335 Agent-Id trailer is therefore NEVER CHECKED."
      fi
    elif is_ours "$HOOK_PATH"; then
      echo "status: $HOOK_NAME: INSTALLED (ours, marker found)."
    else
      echo "status: $HOOK_NAME: exists but is NOT ours. Installing would"
      echo "        back it up first."
    fi
  done
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
  UNINSTALL_RC=0
  for HOOK_NAME in "${HOOK_NAMES[@]}"; do
    HOOK_PATH="$HOOKS_DIR/$HOOK_NAME"
    if [ ! -e "$HOOK_PATH" ]; then
      echo "uninstall: nothing to do, no $HOOK_NAME hook present."
    elif is_ours "$HOOK_PATH"; then
      rm -f "$HOOK_PATH"
      echo "uninstall: removed our $HOOK_NAME hook."
    else
      echo "uninstall: REFUSING $HOOK_NAME. $HOOK_PATH exists but is NOT ours"
      echo "           (no marker). Somebody else's hook is not this script's"
      echo "           to delete. The other hooks were still processed."
      UNINSTALL_RC=1
    fi
  done
  if [ "$UNINSTALL_RC" -ne 0 ]; then exit 1; fi
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

echo
for HOOK_NAME in "${HOOK_NAMES[@]}"; do
  HOOK_PATH="$HOOKS_DIR/$HOOK_NAME"

  if [ -e "$HOOK_PATH" ]; then
    if is_ours "$HOOK_PATH"; then
      echo "$HOOK_NAME: an existing hook is OURS -- re-installing over it."
    else
      STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
      BACKUP="$HOOK_PATH.backup.$STAMP"
      mv "$HOOK_PATH" "$BACKUP"
      echo "$HOOK_NAME: found an existing hook that is NOT ours."
      echo "BACKED IT UP: $BACKUP"
      echo "It was NOT deleted and it is NOT chained. If you need both hooks to"
      echo "run, call the backup from the new hook by hand."
    fi
  fi

  cat >"$HOOK_PATH" <<EOF
#!/bin/bash
# $MARKER
#
# GENERATED by scripts/install_conflict_hook.sh as the $HOOK_NAME hook. Do not
# edit this file: it is outside version control and will be overwritten on the
# next install. The real logic lives in $REAL_HOOK_REL, which picks its job
# from the arguments forwarded below -- none for pre-commit, the composed
# message file for commit-msg.
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
  echo "installed: $HOOK_PATH  (shim, generated)"
done

echo
echo "  $REAL_HOOK_REL  (logic, version-controlled)"
echo
echo "It WARNS on active checkouts. It REFUSES when a staged file's hash does"
echo "not match the last write recorded in file_coordination, when a staged"
echo "file belongs to another agent and no sweep was declared, or when a"
echo "resolved identity commits without a matching Agent-Id trailer (D-335)."
echo
echo "Try it without committing:"
echo "  $REAL_HOOK_REL                       # staged hashes + provenance"
echo "  $REAL_HOOK_REL <message-file>        # Agent-Id trailer"
echo "Bypass:"
echo "  SKIP_CONFLICT_CHECK=1 git commit ...   or   git commit --no-verify"
echo "Remove:"
echo "  scripts/install_conflict_hook.sh --uninstall"
