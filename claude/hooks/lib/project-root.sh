#!/bin/bash
# project-root.sh — Shared project-root detection with a temp-dir cache (source, don't execute)
# Exports: PROJECT_ROOT, PROJECT_HASH, CMM_SENTINEL, CONTEXT_MODE_SENTINEL
# PROJECT_HASH = omt_md5 "$PROJECT_ROOT" (printf, no newline). Every hook keying a sentinel must use it.
#
# Caches PROJECT_ROOT and PROJECT_HASH to $OMT_TMP/cmm-project-root-v2-<md5(pwd)> on first
# source. Subsequent sources read the cache file (zero git subprocesses). Safe to
# source multiple times — idempotent guard returns immediately on re-source.
#
# Installed by setup/install_hooks.py. Hooks source via: source "${BASH_SOURCE[0]%/*}/lib/project-root.sh"
. "${BASH_SOURCE[0]%/*}/compat.sh"

# pr_norm_path P — fold case, backslashes and Git Bash /c/... vs git's C:/... to one form, for loose compares.
pr_norm_path() {
  _p=$(printf '%s' "$1" | tr '\\A-Z' '/a-z')
  case "$_p" in [a-z]:/*) _p="/${_p%%:*}${_p#?:}" ;; esac
  printf '%s' "${_p%/}"
}

# --- Idempotent guard ---
[ -n "$_PROJECT_ROOT_LOADED" ] && return 0

# --- Cache key: md5 of $(pwd) ---
_PR_CWD="$(pwd)"
_PR_CACHE_KEY=$(omt_md5 "$_PR_CWD")
# v2: the hash moved from `echo` (trailing newline) to printf; a v1 cache would hand out the old hash.
_PR_CACHE_FILE="$OMT_TMP/cmm-project-root-v2-${_PR_CACHE_KEY}"

# --- Try cache first ---
if [ -f "$_PR_CACHE_FILE" ]; then
  _PR_CACHED_ROOT=$(sed -n '1p' "$_PR_CACHE_FILE" 2>/dev/null)
  _PR_CACHED_HASH=$(sed -n '2p' "$_PR_CACHE_FILE" 2>/dev/null)
  if [ -n "$_PR_CACHED_ROOT" ] && [ -d "$_PR_CACHED_ROOT" ] && [ -n "$_PR_CACHED_HASH" ]; then
    PROJECT_ROOT="$_PR_CACHED_ROOT"
    PROJECT_HASH="$_PR_CACHED_HASH"
    CMM_SENTINEL="$OMT_TMP/cmm-session-ready-${PROJECT_HASH}"
    CONTEXT_MODE_SENTINEL="$OMT_TMP/context-mode-ready-${PROJECT_HASH}"
    export PROJECT_ROOT PROJECT_HASH CMM_SENTINEL CONTEXT_MODE_SENTINEL
    _PROJECT_ROOT_LOADED=1
    return 0
  fi
fi

# --- Cache miss or stale: full git superproject chain walk ---
# Walk the git superproject chain to find the outermost project root.
# Handles arbitrarily nested submodules — each iteration climbs one level until there is
# no further superproject. Falls back to BASH_SOURCE traversal for non-git environments.
# Git worktrees are handled separately below (they are not submodules).
PROJECT_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"
if [ -n "$PROJECT_ROOT" ]; then
    _WALK="$PROJECT_ROOT"
    while true; do
        _PARENT="$(git -C "$_WALK" rev-parse --show-superproject-working-tree 2>/dev/null)"
        [ -z "$_PARENT" ] && break
        _WALK="$_PARENT"
    done
    PROJECT_ROOT="$_WALK"
fi
if [ -z "$PROJECT_ROOT" ]; then
    # Fallback: derive from BASH_SOURCE (non-git environments)
    # This file lives at .claude/hooks/lib/project-root.sh — 3 levels up to project root
    _PR_SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P)"
    if [ -n "$_PR_SCRIPT_DIR" ]; then
        PROJECT_ROOT="$(cd "$_PR_SCRIPT_DIR/../../.." 2>/dev/null && pwd -P)"
    else
        PROJECT_ROOT="$_PR_CWD"
    fi
fi

# --- Git Worktree Detection ---
# git worktrees share the main repo but show-superproject-working-tree returns empty
# (worktrees are not submodules). Detect via git-common-dir: in a worktree it points
# to the main .git dir, while git-dir points into .git/worktrees/<name>.
# Use the main project root so sentinel hashes are stable across worktree sessions.
if [ -n "$PROJECT_ROOT" ]; then
    _GIT_DIR="$(git -C "$PROJECT_ROOT" rev-parse --git-dir 2>/dev/null)"
    _GIT_COMMON="$(git -C "$PROJECT_ROOT" rev-parse --git-common-dir 2>/dev/null)"
    # Resolve relative paths (git may return relative paths in the main working tree)
    # (Git for Windows prints absolute paths as C:/...)
    case "$_GIT_DIR" in ""|/*|[A-Za-z]:*) ;; *) _GIT_DIR="$PROJECT_ROOT/$_GIT_DIR" ;; esac
    case "$_GIT_COMMON" in ""|/*|[A-Za-z]:*) ;; *) _GIT_COMMON="$PROJECT_ROOT/$_GIT_COMMON" ;; esac
    if [ -n "$_GIT_DIR" ] && [ -n "$_GIT_COMMON" ] && [ "$_GIT_DIR" != "$_GIT_COMMON" ]; then
        _MAIN_ROOT="$(cd "$_GIT_COMMON/.." 2>/dev/null && pwd -P)"
        [ -n "$_MAIN_ROOT" ] && PROJECT_ROOT="$_MAIN_ROOT"
    fi
fi

# --- Legacy orphaned .git/modules/<name>/ tree recovery ---
# If PROJECT_ROOT still resolves into a path containing /.git/ (e.g. a worktree
# whose .git file points into .git/modules/<submodule>/worktrees/<name>/ from a
# deinit'd submodule), walk up to the owning project root and re-run toplevel.
#
# NOTE: the */.git/* match is an intentional over-match — it also fires for
# legitimate paths that contain `.git` as a directory component (e.g.
# /home/x/mirrors/.git/project-foo). The inner guards keep that harmless:
# empty _MODULES_OWNER short-circuits; any _CANDIDATE that is empty or still
# contains /.git/ is rejected and PROJECT_ROOT is left unchanged. Worst case
# on such a path is one extra cd + git rev-parse subprocess.
if [ -n "$PROJECT_ROOT" ]; then
    case "$PROJECT_ROOT" in
      */.git/*)
        _MODULES_OWNER="${PROJECT_ROOT%%/.git/*}"
        if [ -n "$_MODULES_OWNER" ] && [ -d "$_MODULES_OWNER" ]; then
            _CANDIDATE="$(cd "$_MODULES_OWNER" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)"
            case "$_CANDIDATE" in
              */.git/*|"") : ;;  # still inside .git or empty — do not replace
              *) PROJECT_ROOT="$_CANDIDATE" ;;
            esac
        fi
        ;;
    esac
fi

# --- Compute PROJECT_HASH ---
PROJECT_HASH=$(omt_md5 "$PROJECT_ROOT")

# --- Write cache ---
echo "$PROJECT_ROOT" > "$_PR_CACHE_FILE" 2>/dev/null
echo "$PROJECT_HASH" >> "$_PR_CACHE_FILE" 2>/dev/null

# --- Export sentinel paths ---
CMM_SENTINEL="$OMT_TMP/cmm-session-ready-${PROJECT_HASH}"
CONTEXT_MODE_SENTINEL="$OMT_TMP/context-mode-ready-${PROJECT_HASH}"
export PROJECT_ROOT PROJECT_HASH CMM_SENTINEL CONTEXT_MODE_SENTINEL

_PROJECT_ROOT_LOADED=1
