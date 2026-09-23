#!/usr/bin/env python3
"""Post a status update from a Claude session onto its task-board card.

    python3 tools/task_board/report.py <task_id> progress "what I'm doing now"
    python3 tools/task_board/report.py <task_id> question "what I need you to decide"
    python3 tools/task_board/report.py <task_id> done "what I did, what is left"

Add `--image <file>` (repeatable) to attach screenshots; they show on the card.

Works whether or not server.py is running: it appends to
agent_reports/<task_id>.json, which the board reads. After `question`, end your
turn; the answer arrives as your next message.
"""
import sys

import reports

STATUSES = ("progress", "question", "done")

args, files = [], []
it = iter(sys.argv[1:])
for a in it:
    if a == "--image":
        files.append(next(it, None) or sys.exit("--image needs a file"))
    else:
        args.append(a)
if len(args) != 3 or args[1] not in STATUSES:
    sys.exit(__doc__)
task_id, status, message = args
images = []
for p in files:
    try:
        with open(p, "rb") as f:
            name = reports.store_image(f.read(), reports.LOG_IMAGES)
    except (OSError, ValueError) as e:
        sys.exit(f"{p}: {e}")
    if name not in images:
        images.append(name)
reports.append(task_id, status, message.strip(), "claude", images=images)
print(f"reported {status} on task {task_id}" + (f" with {len(images)} image(s)" if images else ""))
