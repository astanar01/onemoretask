#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# cmm-session-start.sh — SessionStart hook (CMM index enforcement)
# Deletes stale sentinel and injects mandatory first-action prompt.
# Spawned agents receive a richer prompt explaining the gate, allow-list, and task location.
# Always exits 0.
#
# Installed by setup/install_hooks.py. Matcher: SessionStart (fires on every session start)

# --- Project root detection (lib/project-root.sh, cached) ---
. "${BASH_SOURCE[0]%/*}/lib/project-root.sh"

# --- Path Integrity Check ---
_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P 2>/dev/null)"
{ [ "$_SCRIPT_ROOT" = "$HOME" ] || [ "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P)" = "$(cd "${CLAUDE_CONFIG_DIR:-$HOME/.claude}" 2>/dev/null && pwd -P)" ]; } && _SCRIPT_ROOT="$PROJECT_ROOT"  # global-install (incl. CLAUDE_CONFIG_DIR): suppress path-mismatch advisory
if [ -n "$_SCRIPT_ROOT" ] && [ -n "$PROJECT_ROOT" ] && [ "$(pr_norm_path "$_SCRIPT_ROOT")" != "$(pr_norm_path "$PROJECT_ROOT")" ]; then  # loose: case, C:/ vs /c/, backslashes
    echo "cmm-hooks: path mismatch — hooks registered for '$_SCRIPT_ROOT' but git root is '$PROJECT_ROOT'."
    echo "Project was moved or cloned. Re-run: python3 setup/install_hooks.py from the onemoretask folder"
fi

# --- Sentinel Deletion ---
# Delete both CMM and Context Mode sentinels to force re-initialization each session
rm -f "$CMM_SENTINEL"
rm -f "$CONTEXT_MODE_SENTINEL"

# --- Fail-open-while-indexing marker ---
# Mark that a CMM index refresh is pending for this session. While this marker is
# present and fresh, session-gate.sh fails OPEN with an advisory instead of hard-
# blocking, so a slow first index of a large codebase does not freeze the session.
# cmm-sentinel-writer.sh removes this marker (and writes the ready sentinel) once
# index_repository/index_status completes. `touch` (re)sets the mtime each session
# so the gate's TTL safety valve measures age from this session start, not an older
# orphaned marker.
touch "$OMT_TMP/cmm-indexing-${PROJECT_HASH}"

# --- Context Mode Bootstrap ---
# If Context Mode is installed, write its sentinel at session start.
# This eliminates the manual ctx_stats call that was previously required.
# The sentinel is advisory: if the MCP server is down, ctx_* tool calls will
# fail with a clear MCP error (not a gate block), which is acceptable UX.
#
# ORDERING GUARANTEE: SessionStart hooks run synchronously before any
# PreToolUse hooks fire. This means the sentinel written below at
# $CONTEXT_MODE_SENTINEL is guaranteed to exist before
# ctx-execute-enforcer.sh checks for it on the first tool call. The
# delete-then-recreate pattern (lines 55-56 delete, lines 87-89 recreate)
# is safe because no PreToolUse hook can interleave between them.
CONTEXT_MODE_INSTALLED=0
if omt_py -c "
import json, os, sys
# 1. Project .mcp.json
try:
    with open('${PROJECT_ROOT}/.mcp.json') as f:
        if 'context-mode' in json.load(f).get('mcpServers', {}):
            sys.exit(0)
except Exception: pass
# 2. Global Claude Code settings
for d in [os.environ.get('CLAUDE_CONFIG_DIR',''), os.path.expanduser('~/.config/claude-code'), os.path.expanduser('~/.claude')]:
    if not d: continue
    try:
        with open(os.path.join(d, 'settings.json')) as f:
            if 'context-mode' in json.load(f).get('mcpServers', {}):
                sys.exit(0)
    except Exception: pass
sys.exit(1)
" 2>/dev/null; then
  CONTEXT_MODE_INSTALLED=1
fi
# Also activate if a session DB already exists (context-mode was used here before)
[ -f "${PROJECT_ROOT}/.claude/context-mode.db" ] && CONTEXT_MODE_INSTALLED=1

if [ "$CONTEXT_MODE_INSTALLED" -eq 1 ]; then
  echo "ready" > "$CONTEXT_MODE_SENTINEL"
fi

# --- Session Type Detection ---
if [ -n "${CLAUDE_AGENT_ID:-}" ] || [ -n "${CLAUDE_PARENT_SESSION_ID:-}" ]; then
  IS_AGENT=1
else
  IS_AGENT=0
fi

if [ "$IS_AGENT" -eq 1 ]; then
  # --- Rich initialization prompt for spawned agents ---
  cat <<'PROMPT'
**SPAWNED AGENT — Read this before using any tools:**

## CMM Session Gate

`session-gate.sh` (PreToolUse:*) blocks write/network tools until the CMM index sentinel is created.
CMM tools, Bash, Read, Grep, and Glob bypass the gate — but Edit, Write, WebFetch, etc. are blocked.
Run `mcp__codebase-memory-mcp__index_repository` first to create the sentinel and unblock everything.

If the sentinel is marked STALE (after a recent git commit): tools remain open — you are NOT blocked.
You may see an advisory note after CMM query calls. Call `mcp__codebase-memory-mcp__index_repository` when you need current graph data.

**NOTE — MCP tool names:** call every MCP tool by its FULLY-QUALIFIED
`mcp__<server>__<tool>` name, exactly as ToolSearch reports it, NOT the bare
shorthand this guidance uses — the bare name returns "No such tool available"
even when the server is connected.

**Step 1 — Open the gate:**
- `mcp__codebase-memory-mcp__index_repository` with NO `mode` argument — ensures graph is current (incremental, quick when already indexed); opens gate automatically. `mode` fast/moderate is refused: they skip source folders

**Allow-listed tools (bypass gate before sentinel exists):**
- `mcp__codebase-memory-mcp__*` — ALL CMM tools pass Phase 2 unconditionally
- `Bash`, `Read`, `Grep`, `Glob` — read-only file tools
- `ToolSearch` — schema fetch (catch-22 escape)
- `SendMessage` — inter-agent coordination (never gated)

**If CMM server is unavailable**, create the sentinel manually with the `touch` command
printed in the session-gate BLOCKED message.

## Context Mode Auto-Initialization

If Context Mode is installed, its sentinel is written automatically at session start.
You do NOT need to call `ctx_stats` manually — the gate is already open.

**If the Context Mode MCP server is unavailable** (sentinel missing despite auto-init), use the
`touch` command printed in the session-gate BLOCKED message.

## CMM Tool Decision Table

Prefer CMM tools over Read/Grep for code navigation:

  Unfamiliar area / package map                             -> get_architecture
  Find by name (function, class, module)                    -> search_graph
  Source of a specific symbol                               -> get_code_snippet
  Who calls X / what does X call                            -> trace_path
  Cross-service or graph-wide queries (Cypher)              -> query_graph
  Text search in code (string literals, error msgs, TODOs)  -> search_code

Orient first: `get_architecture` -> `search_graph` -> `get_code_snippet`.
PROMPT
else
  # --- Minimal prompt for human sessions ---
  cat <<'PROMPT'
**MANDATORY FIRST ACTION — Do this alongside your first reply:**

In your very first assistant turn, output exactly one short acknowledgment line
(e.g. "Refreshing CMM index…") **in the same turn** as the `mcp__codebase-memory-mcp__index_repository` tool call.
NEVER emit an empty text block before the tool call — the Anthropic API rejects turns
whose text content is empty, and that 400 error poisons the rest of the session.

**NOTE — MCP tool names:** call every MCP tool by its FULLY-QUALIFIED
`mcp__<server>__<tool>` name, exactly as ToolSearch reports it (e.g.
`mcp__codebase-memory-mcp__index_repository`, `mcp__muninn__muninn_recall`), NOT the
bare shorthand this guidance uses. The bare name returns "No such tool available"
even when the server is connected.

1. Say one short line AND call `mcp__codebase-memory-mcp__index_repository` in the same turn, with only
   `repo_path` — NO `mode` argument (fast/moderate skip source folders and are refused). The index is
   incremental and quick when already indexed.
2. Only after the index is confirmed current, proceed with the user's request.
3. Invoke `Skill('cmm-rules')` and `Skill('ctx-rules')` via the Skill tool so their protocols
   load into context: CMM orient-first navigation (get_architecture → search_graph → get_code_snippet)
   for source code; ctx_stats / ctx_search before re-running commands.
4. If the task-observer skill is installed, invoke `Skill('task-observer')` NOW, before starting the user's task — it is the
   observation layer that captures skill improvements for the weekly review. A session
   that skips it silently loses its observations. (This line is a named imperative on
   purpose: the passive CLAUDE.md mandate alone has been skipped in real sessions.)

Do NOT skip these steps. Until the sentinel is created, write and network tools are blocked (Edit, Write, WebFetch, etc.).
A stale index (after a recent commit) does not block tools — you'll see an advisory note on CMM queries.
PROMPT
fi

# --- Git worktree: no code index of its own ---
# Indexing a worktree builds a second full graph that nobody deletes. PROJECT_ROOT is already the
# main checkout (lib/project-root.sh), so point the index call there; cmm-index-mode-guard.sh refuses
# a worktree repo_path.
_WT_GD="$(cd "$(git rev-parse --git-dir 2>/dev/null)" 2>/dev/null && pwd -P)"
_WT_GC="$(cd "$(git rev-parse --git-common-dir 2>/dev/null)" 2>/dev/null && pwd -P)"
if [ -n "$_WT_GD" ] && [ -n "$_WT_GC" ] && [ "$_WT_GD" != "$_WT_GC" ]; then
  _WT_PROJECT="${PROJECT_ROOT#/}"
  _WT_PROJECT="${_WT_PROJECT//\//-}"
  cat <<EOF

**Git worktree:** this session runs in a worktree of \`$PROJECT_ROOT\`. A worktree gets no code index
of its own: call \`index_repository\` with \`repo_path\` = \`$PROJECT_ROOT\` (the main checkout) and
query project \`$_WT_PROJECT\`. That index holds the main branch, not this branch's edits: Read this
worktree's own files before editing them. Once the branch is merged, the main index picks the work up.
EOF
fi

exit 0
