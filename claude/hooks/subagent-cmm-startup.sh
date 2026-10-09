#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# subagent-cmm-startup.sh — SubagentStart hook (CMM state context injection)
# Fires in the PARENT session when a subagent starts. Injects CMM index state
# into the subagent's context via additionalContext JSON output so the agent knows
# whether to call index_repository before querying the graph. Silent without Python.
#
# Installed by setup/install_hooks.py. Matcher: * (all subagents)

# --- Project root detection (lib/project-root.sh, cached) ---
. "${BASH_SOURCE[0]%/*}/lib/project-root.sh"

# --- Context Injection ---
# Inject CMM index state into the subagent via additionalContext JSON.
# SubagentStart hooks output: {"hookSpecificOutput": {"hookEventName": "SubagentStart", "additionalContext": "..."}}
# Always exit 0 — this hook is advisory only, never blocking.
# Both cmm-rules and ctx-rules are emitted in one block so the model does not
# skip a second Skill() call from a separate hook turn (multi-block skip observed
# in Phase 61 field test: 8/10 cmm-rules activations, 0/10 ctx-rules activations).
if [ ! -f "$CMM_SENTINEL" ] || grep -q '^stale$' "$CMM_SENTINEL"; then
    ADVISORY="⚠ CMM index is stale. Call index_repository before graph tools. Invoke Skill('cmm-rules') via the Skill tool now, then Invoke Skill('ctx-rules') via the Skill tool now."
else
    ADVISORY="CMM index is ready. Invoke Skill('cmm-rules') via the Skill tool now, then Invoke Skill('ctx-rules') via the Skill tool now."
fi

# JSON-encode via Python json.dumps so embedded quotes, backslashes, and
# control characters survive correctly.
omt_py -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"SubagentStart","additionalContext":sys.argv[1]}}))' \
    "$ADVISORY" 2>/dev/null

exit 0
