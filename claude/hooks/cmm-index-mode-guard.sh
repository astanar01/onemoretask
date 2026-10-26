#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# cmm-index-mode-guard.sh — PreToolUse:mcp__codebase-memory-mcp__index_repository hook
# BLOCKING: exits 2 when index_repository is called with mode "fast" or "moderate", or on a
# git worktree.
#
# Why: both modes skip whole source dirs (scripts/, tools/, docs/, assets/) and still
# report "indexed"; later incremental runs keep the gap. Measured 2026-10-25: 749 of 914
# index calls passed mode "fast" unasked, and a game repo's graph held 452 of ~2,800
# functions under scripts/. Call it with no mode (full).
# A worktree indexed on its own is a second full graph (30-126 MB each) that nothing deletes:
# 242 orphaned worktree graphs held ~10 GB. Worktree sessions use the main checkout's index.
#
# Installed by setup/install_hooks.py.

INPUT=$(cat)

# Fail open if Python is unavailable — never wedge the index call.
omt_has_py || exit 0

FIELDS=$(omt_py - "$INPUT" <<'PY' 2>/dev/null
import sys, json
try:
    ti = json.loads(sys.argv[1]).get("tool_input", {}) or {}
except Exception:
    sys.exit(0)
print(ti.get("mode", "") or "")
print(ti.get("repo_path", "") or "")
PY
)
MODE=$(printf '%s\n' "$FIELDS" | sed -n '1p')
REPO=$(printf '%s\n' "$FIELDS" | sed -n '2p')

case "$MODE" in
  fast|moderate)
    cat >&2 <<EOF
BLOCKED: index_repository with mode "$MODE" skips whole source folders (scripts/, tools/,
docs/, assets/) and still reports "indexed", so later searches miss real code.
Call index_repository again with NO mode argument (full). It is incremental and quick
when the index is already current.
EOF
    exit 2
    ;;
esac

# A linked worktree: its git dir differs from the shared common dir.
if [ -n "$REPO" ] && [ -d "$REPO" ]; then
  GD=$(cd "$REPO" 2>/dev/null && cd "$(git rev-parse --git-dir 2>/dev/null)" 2>/dev/null && pwd -P)
  GC=$(cd "$REPO" 2>/dev/null && cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)
  if [ -n "$GD" ] && [ -n "$GC" ] && [ "$GD" != "$GC" ]; then
    MAIN=$(cd "$GC/.." 2>/dev/null && pwd -P)
    cat >&2 <<EOF
BLOCKED: $REPO is a git worktree. Worktrees get no code index of their own (each would be a
full extra copy that is never deleted). Call index_repository with repo_path "$MAIN"
(the main checkout) and query that project. It holds the main branch, not this branch's
edits: Read the worktree's own files before editing them.
EOF
    exit 2
  fi
fi

exit 0
