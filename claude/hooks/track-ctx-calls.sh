#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# track-ctx-calls.sh — PostToolUse hook: counts calls per context-mode tool in
# $OMT_TMP/ctx-call-counts-<project hash>.json. Silent, never blocks, always exits 0 (also without Python).
# Its matcher only fires on context-mode tools (plugin form mcp__plugin_context-mode_context-mode__*
# and MCP-server form mcp__context-mode__*), so it needs no "is context-mode registered" check.
# Installed by setup/install_hooks.py.

OMT_INPUT=$(cat)
TOOL=$(omt_json_get tool_name)
[ -z "$TOOL" ] && exit 0

source "${BASH_SOURCE[0]%/*}/lib/project-root.sh"
[ -z "$PROJECT_HASH" ] && exit 0

omt_py -c '
import json, os, sys
tool, path = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError
except (OSError, ValueError):
    data = {}
data["total_calls"] = data.get("total_calls", 0) + 1
by = data.setdefault("by_tool", {})
by[tool] = by.get(tool, 0) + 1
tmp = path + ".%d.tmp" % os.getpid()
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
os.replace(tmp, path)
' "$TOOL" "$OMT_TMP/ctx-call-counts-${PROJECT_HASH}.json" >/dev/null 2>&1

exit 0
