#!/usr/bin/env python3
"""Claude Code hook that hands a session the messages typed on its card while it works.

    python3 inbox.py --board DIR <task_id>      (reads the hook event JSON on stdin)

server.py installs it with --settings on every session it starts, for two events:
- PostToolUse: the notes go in as additional context after the tool call that just ran.
- Stop: a note that arrives during the last step blocks the stop, so Claude reads it instead of ending.
Subagents' tool calls fire PostToolUse too (with an agent_id); those are skipped so the note reaches
the main session, which is the one that talks to the card.
"""
import json
import sys

import reports

args = sys.argv[1:]
if len(args) != 3 or args[0] != "--board":
    sys.exit(__doc__)
board, task_id = args[1], args[2]
try:
    event = json.load(sys.stdin)
except ValueError:
    event = {}
name = event.get("hook_event_name")
if event.get("agent_id") or name not in ("PostToolUse", "Stop"):
    sys.exit(0)
notes, _ = reports.take_notes(board, task_id)
if not notes:
    sys.exit(0)
text = reports.notes_message(board, notes)
if name == "Stop":
    out = {"decision": "block", "reason": text}
else:
    out = {"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}
sys.stdout.write(json.dumps(out))
