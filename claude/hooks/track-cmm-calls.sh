#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# track-cmm-calls.sh — PostToolUse:mcp__codebase-memory-mcp__* hook (CMM call counter)
# Tracks call counts per CMM tool in ~/.cache/codebase-memory-mcp. Silent, never blocks, always exits 0.
#
# Installed by setup/install_hooks.py. Matcher: mcp__codebase-memory-mcp__* (all CMM tools)

INPUT=$(cat)
TOOL=$(echo "$INPUT" | omt_py -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))" 2>/dev/null)

[ -z "$TOOL" ] && exit 0

# --- Project root detection (lib/project-root.sh, cached) ---
. "${BASH_SOURCE[0]%/*}/lib/project-root.sh"

# Python resolves ~ and the temp file itself: a Git Bash path (/c/Users/...) means nothing to Windows Python.
omt_py -c "
import json, os, sys, tempfile
tool, h = sys.argv[1], sys.argv[2]
d = os.path.join(os.path.expanduser('~'), '.cache', 'codebase-memory-mcp')
os.makedirs(d, exist_ok=True)
counter_file = os.path.join(d, '_call-counts-%s.json' % h)
try:
    with open(counter_file, 'r') as f:
        data = json.load(f)
except (OSError, ValueError):
    data = {'total_calls': 0, 'by_tool': {}}
data['total_calls'] = data.get('total_calls', 0) + 1
data.setdefault('by_tool', {})[tool] = data['by_tool'].get(tool, 0) + 1
fd, tmp = tempfile.mkstemp(dir=d)
with os.fdopen(fd, 'w') as f:
    json.dump(data, f, indent=2)
    f.write('\n')
os.replace(tmp, counter_file)
" "$TOOL" "$PROJECT_HASH" 2>/dev/null

exit 0
