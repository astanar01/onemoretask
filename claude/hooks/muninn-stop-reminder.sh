#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# UserPromptSubmit hook: every turn, nudge the model to recall from Muninn first and to store
# durable learnings from the prior turn. A nudge only: prints its JSON even when Muninn is absent.
# Needs no Python. Installed by setup/install_hooks.py.

# Diagnostic: one line per invocation (date %F/%T work on BSD and GNU date).
echo "$(date -u +%FT%TZ) pid=$$ ppid=$PPID" >> "$OMT_TMP/muninn-stop-fired.log" 2>/dev/null

TEXT=$(cat <<'PROMPT'
**Muninn: recall first, then consider what to remember.**

RECALL — before exploring the codebase, launching agents, or reading files:
1. Call `mcp__muninn__muninn_recall` with a query matching the user's request, `mode: "semantic"` and `threshold: 0.5` (an empty result means nothing relevant is stored)
2. Review what Muninn returns — it may already have the context you need
3. Only THEN proceed with the task

If the user's message is a simple question, clarification, or follow-up that doesn't require codebase exploration, skip the recall. If you're about to search code, read files, or start implementing — recall FIRST. Skipping this wastes tokens re-discovering what Muninn already knows.

REMEMBER — scan the PRIOR assistant turn for durable learnings that should be stored in Muninn beyond what commit messages already capture. Prefer `muninn_remember` (or `muninn_remember_batch` for 2+ items) for:
  - Newly-learned infra or system facts (not re-derivable from git)
  - Rejected alternatives with the reasoning for choosing the winner
  - Bug root causes (separate from the fix)
  - User-corrected ground truth (domain, workflow, deploy quirks)
If none of those apply, skip silently — do not narrate the check.
PROMPT
)

# JSON-escape in pure bash (the text has no tabs or control characters besides newlines).
TEXT=${TEXT//\\/\\\\}
TEXT=${TEXT//\"/\\\"}
TEXT=${TEXT//$'\n'/\\n}
printf '{"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":"%s"}}\n' "$TEXT"
