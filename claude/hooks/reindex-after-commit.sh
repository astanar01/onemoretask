#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# reindex-after-commit.sh — PostToolUse:Bash hook (marks CMM sentinel stale after git commit)
# Detects successful git commit calls and writes "stale" to the CMM sentinel file, prompting
# a stale-index advisory in session-gate.sh until the user refreshes the index.
# Silent without Python.
#
# Installed by setup/install_hooks.py. Matcher: PostToolUse:Bash

# --- Pre-Traversal Early Exit ---
# Read stdin once (can only be consumed once), then check if this is a git commit command.
# Exit immediately for non-commit Bash calls — avoids the expensive git traversal for ~95% of calls.
INPUT=$(cat)
echo "$INPUT" | grep -q '"git commit\|"git  *commit' || exit 0

# --- Stable Sentinel Path Computation (lib/project-root.sh: submodules, worktrees) ---
. "${BASH_SOURCE[0]%/*}/lib/project-root.sh"

# --- Input Parsing ---
# stdin already consumed into INPUT above — pipe from variable for Python parsing
CMD=$(echo "$INPUT" | omt_py -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")
STDOUT=$(echo "$INPUT" | omt_py -c "import sys,json; d=json.load(sys.stdin); r=d.get('tool_response') or d.get('tool_output') or {}; print(r.get('stdout','') if isinstance(r,dict) else r)" 2>/dev/null || echo "")

# --- Commit Detection ---
# Only act on git commit commands; exit immediately for all other Bash calls
case "$CMD" in
  *"git commit"*) ;;
  *) exit 0 ;;
esac

# --- False-Positive Guard ---
# If git reported no actual commit (nothing staged), skip marking stale
case "$STDOUT" in
  *"nothing to commit"*|*"no changes"*) exit 0 ;;
esac

# --- Write Stale Marker ---
echo "stale" > "$CMM_SENTINEL"

# --- Nudge CMM Watcher via touch_project ---
# Resolve the project name from the superproject root path.
# PROJECT_ROOT is already resolved to the outermost superproject root by the sentinel logic above.
# CMM derives project names from the full absolute path: strip leading /, replace / with -.
# e.g. /Users/ahby/Sources/my-project -> Users-ahby-Sources-my-project
_CMM_PROJECT_NAME="${PROJECT_ROOT#/}"
_CMM_PROJECT_NAME="${_CMM_PROJECT_NAME//\//-}"

# Capture output silently; touch_project is fire-and-forget.
# Use the CLI interface (not MCP tool name) since this runs in a shell hook, not Claude's agent runtime.
_CMM_TOUCH_JSON=$(omt_py -c "import json; print(json.dumps({'project': '$_CMM_PROJECT_NAME'.replace(chr(39), '')}))" 2>/dev/null || echo '{"project":"'"$_CMM_PROJECT_NAME"'"}')
_CMM_TOUCH_OUTPUT=$(codebase-memory-mcp cli touch_project "$_CMM_TOUCH_JSON" 2>/dev/null) && _CMM_TOUCH_OK=1 || _CMM_TOUCH_OK=0

# --- Informational Message ---
if [ "$_CMM_TOUCH_OK" -eq 1 ]; then
  cat <<'EOF' >&2
CMM note: Commit detected. touch_project nudged the CMM watcher — reindex expected in 5–60s.
Graph queries will return pre-commit results until then. To reindex immediately:
  mcp__codebase-memory-mcp__index_repository
EOF
else
  cat <<'EOF' >&2
CMM note: Commit detected. Sentinel marked stale (touch_project unavailable — CMM CLI not in PATH or server not running).
Graph queries may return pre-commit results. To reindex:
  mcp__codebase-memory-mcp__index_repository
EOF
fi

exit 0
