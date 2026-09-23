"""Per-project board storage shared by report.py (the agent side) and server.py (the board side).

A project's board lives in `board_dir(root)`: <root>/.task_board/ (or, for a repo that
already keeps one there, <root>/tools/task_board/). It holds
- tasks.json            the board itself (commit it)
- images/               images pasted into a card's notes (commit them)
- agent_reports/        <task_id>.json message logs + images/ from replies and
                        report.py --image; run chatter, kept out of git

A log is a list of {at, status, message, from, images?, session?}. Images are
stored by content hash.
"""
import fcntl
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone

IMAGE_EXTS = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"GIF8": ".gif"}
MAX_IMAGE = 25 * 1024 * 1024


def board_dir(root):
    legacy = os.path.join(root, "tools", "task_board")
    if os.path.exists(os.path.join(legacy, "tasks.json")):
        return legacy
    return os.path.join(root, ".task_board")


def ensure_board(board):
    """Create the board folder; a new one gets a .gitignore for the run chatter."""
    os.makedirs(board, exist_ok=True)
    ignore = os.path.join(board, ".gitignore")
    if not os.path.exists(ignore) and os.path.basename(board) == ".task_board":
        with open(ignore, "w") as f:
            f.write("agent_reports/\n")


def project_root(path="."):
    """The git top level containing `path`, else `path` itself."""
    r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else os.path.abspath(path)


def task_images(board):
    return os.path.join(board, "images")


def log_images(board):
    return os.path.join(board, "agent_reports", "images")


def image_ext(data):
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return next((ext for magic, ext in IMAGE_EXTS.items() if data.startswith(magic)), None)


def store_image(data, folder):
    """Save image bytes under `folder`; returns the file name. Refuses non-images."""
    ext = image_ext(data)
    if not ext:
        raise ValueError("not a PNG, JPEG, GIF or WebP image")
    if len(data) > MAX_IMAGE:
        raise ValueError("image over 25 MB")
    os.makedirs(folder, exist_ok=True)
    name = hashlib.sha256(data).hexdigest()[:16] + ext
    path = os.path.join(folder, name)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(data)
    return name


def image_path(folder, name):
    if not re.fullmatch(r"[0-9a-f]{16}\.(png|jpg|gif|webp)", name):
        raise ValueError(f"bad image name {name!r}")
    return os.path.join(folder, name)


def _path(board, task_id):
    if not re.fullmatch(r"[0-9a-z]+", task_id):
        raise ValueError(f"bad task id {task_id!r}")
    return os.path.join(board, "agent_reports", task_id + ".json")


def read(board, task_id):
    try:
        with open(_path(board, task_id), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def append(board, task_id, status, message, sender, session=None, images=None):
    path = _path(board, task_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {"at": datetime.now(timezone.utc).isoformat(), "status": status, "message": message, "from": sender}
    if images:
        entry["images"] = images  # names in log_images(board)
    if session:
        entry["session"] = session  # the board follows the newest one (a resume can land in a new session)
    # report.py and a board reply can land together; the lock keeps both entries.
    with open(path, "a+", encoding="utf-8") as f:
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
