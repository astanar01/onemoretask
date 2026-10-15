#!/usr/bin/env python3
"""Post a status update from a Claude session onto its task-board card.

    python3 report.py [--board DIR] <task_id> progress "what I'm doing now"
    python3 report.py [--board DIR] <task_id> question "what I need you to decide"
    python3 report.py [--board DIR] <task_id> done "what I did, what is left"
    python3 report.py [--board DIR] <task_id> decision -   < decision.json   (or a JSON string instead of -)

A decision is a choice between options; the card draws it as a tree. Its JSON:
{"query": "what was asked", "options": [{"label": "...", "why": "..."}, ...], "chosen": 0, "by": "agent", "why": "..."}
"chosen" is the option's index (null while still open); "by" is "agent" or "you"; 2 to 6 options.

Add `--image <file>` (repeatable) to attach screenshots; they show on the card.
--board is the project's board folder; without it, the board of the git repo
containing the current directory.

Works whether or not server.py is running: it appends to
<board>/agent_reports/<task_id>.json, which the board reads. After `question`,
end your turn; the answer arrives as your next message.
"""
import json
import sys

import reports

STATUSES = ("progress", "question", "done")

args, files, board = [], [], None
it = iter(sys.argv[1:])
for a in it:
    if a == "--image":
        files.append(next(it, None) or sys.exit("--image needs a file"))
    elif a == "--board":
        board = next(it, None) or sys.exit("--board needs a folder")
    else:
        args.append(a)
if len(args) != 3 or args[1] not in STATUSES + ("decision",):
    sys.exit(__doc__)
task_id, status, message = args
decision = None
if status == "decision":
    raw = sys.stdin.read() if message == "-" else message
    try:
        decision = reports.check_decision(json.loads(raw))
    except (ValueError, TypeError) as e:
        sys.exit(f"decision: {e}")
    message, status = reports.decision_message(decision), "progress"
board = board or reports.board_dir(reports.project_root())
images = []
for p in files:
    try:
        with open(p, "rb") as f:
            name = reports.store_image(f.read(), reports.log_images(board))
    except (OSError, ValueError) as e:
        sys.exit(f"{p}: {e}")
    if name not in images:
        images.append(name)
reports.append(board, task_id, status, message.strip(), "claude", images=images, decision=decision)
print(f"reported {'decision' if decision else status} on task {task_id}" + (f" with {len(images)} image(s)" if images else ""))
