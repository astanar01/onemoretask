"""CHANGELOG.md at the project root, written by the board when a task moves into a done column.

server.py calls record() on every board save with the tasks.json it replaces, so the entry lands no matter how the
card got there (the Move to done button, a drag, a ticked subtask). Only top-level tasks get an entry; a parent's
entry lists its subtasks. Each entry carries a <!-- task:<id> --> marker, so a task moved out of done and back
is not logged twice. The file is written, not committed: it goes into the next commit that takes it.
"""
import os
import re
import tempfile
import threading
from datetime import datetime

import reports

NAME = "CHANGELOG.md"
HEADER = "# Changelog\n\nWritten by the task board each time a task moves to done. Newest first.\n"
MAX_SUMMARY = 300
_LOCK = threading.Lock()


def newly_done(old, new):
    """Top-level tasks of `new` that sit in a done column and did not in `old` (a card move, not a column toggle)."""
    done = {c["id"] for c in new.get("columns", []) if c.get("done")}
    was_done = {c["id"] for c in old.get("columns", []) if c.get("done")}
    before = {t.get("id"): t.get("column") for t in old.get("tasks", []) if isinstance(t, dict)}
    return [t for t in new.get("tasks", []) if isinstance(t, dict) and not t.get("parent")
            and t.get("column") in done and t.get("id") in before
            and before[t["id"]] != t["column"] and before[t["id"]] not in was_done]


def summary(board, task_id):
    """First paragraph of the task's own final report on the card (not a reviewer's), on one line."""
    log = reports.read(board, task_id)
    first_review = next((i for i, e in enumerate(log) if e.get("status") == "review"), len(log))
    msg = next((e.get("message", "") for e in reversed(log[:first_review])
                if e.get("from") == "claude" and e.get("status") in ("done", "answer")), "")
    text = " ".join(re.split(r"\n\s*\n", msg.strip(), 1)[0].split())
    return text if len(text) <= MAX_SUMMARY else text[:MAX_SUMMARY - 1].rstrip() + "…"


def entry(board, task, subtasks):
    one_line = lambda s: " ".join(str(s).split())
    lines = [f"- **{one_line(task.get('title') or '(untitled)')}** <!-- task:{task['id']} -->"]
    text = summary(board, task["id"])
    if text:
        lines.append(f"  {text}")
    lines += [f"  - {one_line(k.get('title') or '(untitled)')}" for k in subtasks]
    return "\n".join(lines)


def add(root, board, tasks, all_tasks, today=None):
    """Insert entries for `tasks` under today's heading at the top of CHANGELOG.md. Returns the ids written."""
    path = os.path.join(root, NAME)
    today = today or datetime.now().strftime("%Y-%m-%d")
    with _LOCK:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except FileNotFoundError:
            text = HEADER
        todo = [t for t in tasks if f"<!-- task:{t['id']} -->" not in text]
        if not todo:
            return []
        kids = lambda t: sorted((k for k in all_tasks if isinstance(k, dict) and k.get("parent") == t["id"]),
                                key=lambda k: k.get("order", 0))
        block = "\n".join(entry(board, t, kids(t)) for t in todo)
        heading = f"## {today}\n"
        m = re.search(r"^## .*$", text, re.M)
        if m and m.group(0) + "\n" == heading:  # today's section is on top: add to its start
            at = m.end() + 1
            text = text[:at] + "\n" + block + "\n" + text[at:].lstrip("\n")
        else:
            at = m.start() if m else len(text)
            text = text[:at].rstrip("\n") + "\n\n" + heading + "\n" + block + "\n\n" + text[at:]
        text = text.rstrip("\n") + "\n"
        fd, tmp = tempfile.mkstemp(dir=root, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
        return [t["id"] for t in todo]


def record(root, board, old, new):
    """Log the tasks this save moved into done. Never raises: a changelog problem must not fail a board save."""
    try:
        moved = newly_done(old, new)
        return add(root, board, moved, new.get("tasks", [])) if moved else []
    except Exception as e:  # noqa: BLE001
        print(f"changelog: not updated ({e})")
        return []
