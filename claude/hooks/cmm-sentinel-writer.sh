#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# cmm-sentinel-writer.sh — PostToolUse hook (index_repository OR index_status)
# NON-BLOCKING: always exits 0 (writes sentinel to unblock session gate)
#
# Purpose: Writes the session sentinel file after index_repository or index_status
#          completes, unblocking all tools gated by session-gate.sh.
#
# Installed by setup/install_hooks.py.
# Matcher: mcp__codebase-memory-mcp__index_repository|mcp__codebase-memory-mcp__index_status

# --- Project root detection (lib/project-root.sh, cached) ---
. "${BASH_SOURCE[0]%/*}/lib/project-root.sh"

# --- Path Integrity Check ---
_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P 2>/dev/null)"
{ [ "$_SCRIPT_ROOT" = "$HOME" ] || [ "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)" = "$(cd "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" 2>/dev/null && pwd -P)" ]; } && _SCRIPT_ROOT="$PROJECT_ROOT"  # global-install (incl. CLAUDE_CONFIG_DIR): suppress path-mismatch advisory
if [ -n "$_SCRIPT_ROOT" ] && [ -n "$PROJECT_ROOT" ] && [ "$(pr_norm_path "$_SCRIPT_ROOT")" != "$(pr_norm_path "$PROJECT_ROOT")" ]; then  # loose: case, C:/ vs /c/, backslashes
    echo "cmm-hooks: path mismatch — hooks registered for '$_SCRIPT_ROOT' but git root is '$PROJECT_ROOT'." >&2
    echo "Project was moved or cloned. Re-run: python3 setup/install_hooks.py from the onemoretask folder" >&2
fi

SENTINEL="$CMM_SENTINEL"

# Write sentinel to unblock session gate.
# index_status only confirms the server is running — it does not trigger a reindex.
# If the sentinel is already stale (written after a commit), preserve the stale marker
# so the advisory remains until the user calls index_repository to actually reindex.
INPUT=$(cat 2>/dev/null)
TOOL=$(echo "$INPUT" | omt_py -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null || echo "")
if [ "$TOOL" = "mcp__codebase-memory-mcp__index_status" ] && grep -q '^stale$' "$SENTINEL" 2>/dev/null; then
  # index_status called while stale — do not clear; reindex hasn't happened yet
  exit 0
fi
echo "ready" > "$SENTINEL"

# Index refresh completed for this session — clear the fail-open-while-indexing
# marker written by cmm-session-start.sh so session-gate.sh resumes normal
# (ready-sentinel) behavior instead of the advisory fail-open path. Reached only
# on a real reindex/confirm (the stale index_status case returned above).
rm -f "$OMT_TMP/cmm-indexing-${PROJECT_HASH}"

exit 0
