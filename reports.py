"""Per-task message log shared by report.py (the agent side) and server.py (the board side).

agent_reports/<task_id>.json is a list of {at, status, message, from}; it is kept
out of git (.gitignore) because it is run chatter, not task data.
"""
import fcntl
import json
import os
import re
from datetime import datetime, timezone

DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_reports")


def _path(task_id):
    if not re.fullmatch(r"[0-9a-z]+", task_id):
        raise ValueError(f"bad task id {task_id!r}")
    return os.path.join(DIR, task_id + ".json")


def read(task_id):
    try:
        with open(_path(task_id), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def append(task_id, status, message, sender, session=None):
    os.makedirs(DIR, exist_ok=True)
    entry = {"at": datetime.now(timezone.utc).isoformat(), "status": status, "message": message, "from": sender}
    if session:
        entry["session"] = session  # the board follows the newest one (a resume can land in a new session)
    # report.py and a board reply can land together; the lock keeps both entries.
    with open(_path(task_id), "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        try:
            log = json.loads(f.read() or "[]")
        except json.JSONDecodeError:
            log = []
        log.append(entry)
        f.seek(0)
        f.truncate()
        json.dump(log, f, indent=2, ensure_ascii=False)
    return entry
