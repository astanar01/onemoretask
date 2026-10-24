#!/usr/bin/env python3
"""Post a status update from a Claude session onto its task-board card.

    python3 report.py [--board DIR] <task_id> progress "what I'm doing now"
    python3 report.py [--board DIR] <task_id> question "what I need you to decide" [--option "A. ..." --option "B. ..."]
    python3 report.py [--board DIR] <task_id> done "what I did, what is left"
    python3 report.py [--board DIR] <task_id> decision -   < decision.json   (or a JSON string instead of -)

A message of `-` is read from stdin, for every status. `--file <path>` in place of the message reads it
from that file: the form an answer-only card's sandbox lets through when the text holds a backtick, `$(`,
a `<` or a heredoc.

A decision is a choice between options; the card draws all of a task's decisions as one horizontal
tree, each hanging off the option chosen before it, and redraws it as new ones arrive. Its JSON:
{"query": "what was asked", "options": [{"label": "...", "why": "..."}, ...], "chosen": 0, "by": "agent", "why": "..."}
"chosen" is the option's index (null while still open); "by" is "agent" or "you"; 2 to 6 options.
Posting a decision again with the same "query" replaces the earlier one (an open one becomes decided).

A question can list its choices with `--option "<short label>"` (repeatable, 2 to 6, all different);
the card shows one button per choice that sends it as the answer. Only for `question`.
A question with several parts gives each part as `--part "<its question>"` followed by that part's
`--option`s (1 to 8 parts, 2 to 6 options each); the card shows a row of choices per part and one
send button, and the answer arrives as one line per part: "<part> → <option>".

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
import os
import shlex
import sys

import reports

STATUSES = ("progress", "question", "done")

args, files, board, lang, options, parts, msg_file = [], [], None, None, [], [], None
it = iter(sys.argv[1:])
for a in it:
    if a == "--image":
        files.append(next(it, None) or sys.exit("--image needs a file"))
    elif a == "--option":
        o = next(it, None)
        (parts[-1]["options"] if parts else options).append(o if o is not None else sys.exit("--option needs a label"))
    elif a == "--part":
        q = next(it, None)
        parts.append({"q": q if q is not None else sys.exit("--part needs its question"), "options": []})
    elif a == "--board":
        board = next(it, None) or sys.exit("--board needs a folder")
    elif a == "--lang":
        lang = next(it, None) or sys.exit("--lang needs a language")
    elif a == "--file":
        msg_file = next(it, None) or sys.exit("--file needs a file")
    else:
        args.append(a)
if len(args) != (2 if msg_file else 3) or args[1] not in STATUSES + ("decision",):
    sys.exit(__doc__)
if msg_file:
    try:
        with open(msg_file, encoding="utf-8") as f:
            args.append(f.read())
    except (OSError, UnicodeDecodeError) as e:
        sys.exit(f"--file {msg_file}: {e}")
task_id, status, message = args
asked = status
if message == "-" and not msg_file:
    message = sys.stdin.read()
try:
    reports._path("", task_id)
except ValueError as e:
    sys.exit(str(e))
if status != "decision" and not message.strip():
    sys.exit(f"{status} needs a message")
if options:
    if status != "question":
        sys.exit("--option only goes with question")
    try:
        options = reports.check_options(options)
    except ValueError as e:
        sys.exit(f"question: {e}")
if parts:
    if status != "question":
        sys.exit("--part only goes with question")
    if options:
        sys.exit("question: put every --option after its --part")
    try:
        parts = reports.check_parts(parts)
    except ValueError as e:
        sys.exit(f"question: {e}")
decision = None
if status == "decision":
    try:
        decision = reports.check_decision(json.loads(message))
    except (ValueError, TypeError) as e:
        sys.exit(f"decision: {e}")
    message, status = reports.decision_message(decision), "progress"
board = board or reports.board_dir(reports.project_root())
mismatch = None if lang else reports.language_mismatch(board, task_id, message)
if mismatch:
    sys.exit(f"Not posted: this report is in {mismatch[0]}, but the user's latest message on the card is in "
             f"{mismatch[1]}. Write it again in {mismatch[1]}. A task ABOUT {mismatch[0]} is not a request to "
             f"answer in it. Only if the user asked for {mismatch[0]} answers, add --lang {mismatch[0]}.")


def refused(e):
    """The board folder is read-only for this shell: Claude Code's sandbox kept the command inside it (see
    --file above), so say how to post the same text instead of a traceback."""
    retry = " ".join(shlex.quote(a) for a in ("python3", os.path.abspath(__file__), "--board", board, task_id, asked))
    sys.exit(f"Not posted: the board refused the write ({e}). This shell runs in a sandbox where the project is "
             "read-only. The report command only runs outside it when its text has no backtick, no `$(`, no `<` "
             "and no heredoc. Write the message to a file outside the project with the Write tool, then post it "
             f"with: {retry} --file <that file>")


images = []
for p in files:
    try:
        with open(p, "rb") as f:
            data = f.read()
    except OSError as e:
        sys.exit(f"{p}: {e}")
    try:
        name = reports.store_image(data, reports.log_images(board))
    except PermissionError as e:
        refused(e)
    except (OSError, ValueError) as e:
        sys.exit(f"{p}: {e}")
    if name not in images:
        images.append(name)
try:
    reports.append(board, task_id, status, message.strip(), "claude", images=images, decision=decision,
                   options=options, parts=parts)
except PermissionError as e:
    refused(e)
print(f"reported {'decision' if decision else status} on task {task_id}" + (f" with {len(images)} image(s)" if images else ""))
