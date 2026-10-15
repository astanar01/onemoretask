#!/usr/bin/env python3
"""Post a status update from a Claude session onto its task-board card.

    python3 report.py [--board DIR] <task_id> progress "what I'm doing now"
    python3 report.py [--board DIR] <task_id> question "what I need you to decide" [--option "A. ..." --option "B. ..."]
    python3 report.py [--board DIR] <task_id> done "what I did, what is left"
    python3 report.py [--board DIR] <task_id> decision -   < decision.json   (or a JSON string instead of -)

A decision is a choice between options; the card draws it as a tree. Its JSON:
{"query": "what was asked", "options": [{"label": "...", "why": "..."}, ...], "chosen": 0, "by": "agent", "why": "..."}
"chosen" is the option's index (null while still open); "by" is "agent" or "you"; 2 to 6 options.

A question can list its choices with `--option "<short label>"` (repeatable, 2 to 6, all different);
the card shows one button per choice that sends it as the answer. Only for `question`.

Add `--image <file>` (repeatable) to attach screenshots; they show on the card.
--board is the project's board folder; without it, the board of the git repo
containing the current directory.

Works whether or not server.py is running: it appends to
<board>/agent_reports/<task_id>.json, which the board reads. After `question`,
end your turn; the answer arrives as your next message.

A report plainly written in another language than the user's latest message on
the card is refused. When the user asked for that language, pass
`--lang <language>` to post it anyway.
"""
import json
import sys

import reports

STATUSES = ("progress", "question", "done")

args, files, board, lang, options = [], [], None, None, []
it = iter(sys.argv[1:])
for a in it:
    if a == "--image":
        files.append(next(it, None) or sys.exit("--image needs a file"))
    elif a == "--option":
        o = next(it, None)
        options.append(o if o is not None else sys.exit("--option needs a label"))
    elif a == "--board":
        board = next(it, None) or sys.exit("--board needs a folder")
    elif a == "--lang":
        lang = next(it, None) or sys.exit("--lang needs a language")
    else:
        args.append(a)
if len(args) != 3 or args[1] not in STATUSES + ("decision",):
    sys.exit(__doc__)
task_id, status, message = args
if options:
    if status != "question":
        sys.exit("--option only goes with question")
    try:
        options = reports.check_options(options)
    except ValueError as e:
        sys.exit(f"question: {e}")
decision = None
if status == "decision":
    raw = sys.stdin.read() if message == "-" else message
    try:
        decision = reports.check_decision(json.loads(raw))
    except (ValueError, TypeError) as e:
        sys.exit(f"decision: {e}")
    message, status = reports.decision_message(decision), "progress"
board = board or reports.board_dir(reports.project_root())
mismatch = None if lang else reports.language_mismatch(board, task_id, message)
if mismatch:
    sys.exit(f"Not posted: this report is in {mismatch[0]}, but the user's latest message on the card is in "
             f"{mismatch[1]}. Write it again in {mismatch[1]}. A task ABOUT {mismatch[0]} is not a request to "
             f"answer in it. Only if the user asked for {mismatch[0]} answers, add --lang {mismatch[0]}.")
images = []
for p in files:
    try:
        with open(p, "rb") as f:
            name = reports.store_image(f.read(), reports.log_images(board))
    except (OSError, ValueError) as e:
        sys.exit(f"{p}: {e}")
    if name not in images:
        images.append(name)
reports.append(board, task_id, status, message.strip(), "claude", images=images, decision=decision,
               options=options)
print(f"reported {'decision' if decision else status} on task {task_id}" + (f" with {len(images)} image(s)" if images else ""))
