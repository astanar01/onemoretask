#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# cmm-index-mode-guard.sh — PreToolUse:mcp__codebase-memory-mcp__index_repository hook
# BLOCKING: exits 2 when index_repository is called with mode "fast" or "moderate".
#
# Why: both modes skip whole source dirs (scripts/, tools/, docs/, assets/) and still
# report "indexed"; later incremental runs keep the gap. Measured 2026-10-25: 749 of 914
# index calls passed mode "fast" unasked, and a game repo's graph held 452 of ~2,800
# functions under scripts/. Call it with no mode (full).
#
# Installed by setup/install_hooks.py.

INPUT=$(cat)

# Fail open if Python is unavailable — never wedge the index call.
omt_has_py || exit 0

MODE=$(omt_py - "$INPUT" <<'PY' 2>/dev/null
import sys, json
try:
    ti = json.loads(sys.argv[1]).get("tool_input", {}) or {}
except Exception:
    sys.exit(0)
print(ti.get("mode", "") or "")
PY
)

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

exit 0
