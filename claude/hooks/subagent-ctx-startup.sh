#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# subagent-ctx-startup.sh — SubagentStart hook: when context-mode is registered, tells the subagent
# that context-mode is active. Silent no-op when it is not. Needs no Python. Always exits 0.
# Installed by setup/install_hooks.py (SubagentStart, matcher "*").

OMT_INPUT=$(cat 2>/dev/null)
[ -z "$OMT_INPUT" ] && exit 0
CWD=$(omt_json_get cwd)
[ -n "$CWD" ] || CWD=$PWD
REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] || REPO_ROOT=$CWD

# Registered = plugin form (enabled in settings.json, or present in the plugin cache)
# or MCP-server form (an mcpServers "context-mode" key in .mcp.json / .claude.json).
ctx_registered() {
  _cfg=${CLAUDE_CONFIG_DIR:-$HOME/.claude}
  [ -d "$_cfg/plugins/cache/context-mode" ] && return 0
  grep -q '"context-mode@context-mode"[[:space:]]*:[[:space:]]*true' "$_cfg/settings.json" 2>/dev/null && return 0
  for _f in "$REPO_ROOT/.mcp.json" "$_cfg/.claude.json" "$HOME/.claude.json"; do
    grep -q '"context-mode"[[:space:]]*:' "$_f" 2>/dev/null && return 0
  done
  return 1
}
ctx_registered || exit 0

# Marker [ctx-startup] kept for test assertions. The text is fixed, so it needs no JSON escaping.
printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":"[ctx-startup] Context Mode is active. Use ctx_search before re-running commands this session."}}'
exit 0
