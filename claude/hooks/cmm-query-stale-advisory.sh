#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# cmm-query-stale-advisory.sh — PostToolUse:CMM query tools (stale index advisory)
# Non-blocking: prints advisory to stderr if CMM index is stale after a recent commit.
#
# Installed by setup/install_hooks.py. Matcher: PostToolUse on the CMM query tools
#   (search_graph|get_code_snippet|trace_path|query_graph)

# --- Project root detection (lib/project-root.sh, cached) ---
. "${BASH_SOURCE[0]%/*}/lib/project-root.sh"

# --- Stale Check ---
# If the sentinel exists and contains exactly "stale" (anchored), print advisory to stderr.
# If sentinel is absent or contains "ready", exit silently.
# Always exit 0 — this hook is advisory only, never blocking.
if [ -f "$CMM_SENTINEL" ] && grep -q '^stale$' "$CMM_SENTINEL"; then
    echo "⚠ CMM index may be stale — run index_repository for up-to-date results." >&2
fi

exit 0
