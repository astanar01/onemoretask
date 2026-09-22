#!/usr/bin/env python3
"""Post a status update from a Claude session onto its task-board card.

    python3 tools/task_board/report.py <task_id> progress "what I'm doing now"
    python3 tools/task_board/report.py <task_id> question "what I need you to decide"
    python3 tools/task_board/report.py <task_id> done "what I did, what is left"

Works whether or not server.py is running: it appends to
agent_reports/<task_id>.json, which the board reads. After `question`, end your
turn; the answer arrives as your next message.
"""
import sys

import reports

STATUSES = ("progress", "question", "done")

if len(sys.argv) != 4 or sys.argv[2] not in STATUSES:
    sys.exit(__doc__)
task_id, status, message = sys.argv[1:]
reports.append(task_id, status, message.strip(), "claude")
print(f"reported {status} on task {task_id}")
