#!/bin/bash
# UserPromptSubmit hook — fires at the start of every turn with a combined
# Muninn nudge: (1) recall prior context before exploring/implementing, and
# (2) scan the prior assistant turn for durable learnings worth storing.
#
# Merges what used to be two separate hooks:
#   - muninn-prompt-reminder.sh (recall, was registered at project level)
#   - muninn-stop-reminder.sh (remember, was originally a Stop hook — moved
#     here after confirming Stop events don't deliver hookSpecificOutput.
#     additionalContext to the model in this Claude Code version).

# Diagnostic: record every invocation. Useful for confirming the hook fires
# and for session-level auditing of how often Muninn nudges are triggered.
echo "$(date -u +%FT%TZ) pid=$$ ppid=$PPID" >> "${TMPDIR:-/tmp}/muninn-stop-fired.log" 2>/dev/null

cat <<'PROMPT'
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
