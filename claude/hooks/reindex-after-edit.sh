#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# reindex-after-edit.sh — PostToolUse:Write|Edit hook (CMM reindex reminder)
# NON-BLOCKING: always exits 0 (advisory only, never blocks). Silent without jq or Python.
# Debounce: 60 seconds between prompts
#
# Installed by setup/install_hooks.py. Matcher: PostToolUse:Write|Edit

INPUT=$(cat)

# Try jq first, fall back to Python
if command -v jq >/dev/null 2>&1; then
  FILE=$(echo "$INPUT" | jq -r '.tool_input.file_path // .tool_input.path // empty' 2>/dev/null)
else
  FILE=$(echo "$INPUT" | omt_py -c "
import sys, json
d = json.load(sys.stdin)
ti = d.get('tool_input', {})
print(ti.get('file_path', '') or ti.get('path', ''))
" 2>/dev/null)
fi

# Skip if no file path
[ -z "$FILE" ] && exit 0

# Skip planning artifacts and config paths (don't need reindex)
case "$FILE" in
  */.claude/*|*/.git/*) exit 0 ;;
esac

# Only trigger for CMM-indexed file types (built-in extensions/filenames covering CMM's 155-language grammar set + user-defined)
source "${BASH_SOURCE[0]%/*}/../lib/is-cmm-ext.sh" 2>/dev/null \
  || source "${BASH_SOURCE[0]%/*}/lib/is-cmm-ext.sh" 2>/dev/null \
  || { exit 0; }

is_cmm_ext "$FILE" || exit 0

# Debounce: skip if we prompted within the last 60 seconds
STAMP="$OMT_TMP/cmm-reindex-stamp-$(id -u 2>/dev/null || echo 0)"
# find -mmin works on BSD, GNU and Git Bash; older macOS `date -r` takes seconds, not a file.
if [ -f "$STAMP" ] && [ -n "$(find "$STAMP" -mmin -1 2>/dev/null)" ]; then
  exit 0
fi
touch "$STAMP"

# Advisory output — extract just the filename for readability
BASENAME=$(basename "$FILE")
echo "Source file modified: $BASENAME"
echo "Consider refreshing the CMM index: index_repository"
echo "(Auto-sync may handle this automatically if enabled)"

exit 0
