"""Per-project board storage shared by report.py (the agent side) and server.py (the board side).

A project's board lives in `board_dir(root)`: <root>/.task_board/ (or, for a repo that
already keeps one there, <root>/tools/task_board/). It holds
- tasks.json            the board itself (commit it)
- images/               images pasted into a card's notes (commit them)
- agent_reports/        <task_id>.json message logs + images/ from replies and
                        report.py --image; run chatter, kept out of git

A log is a list of {at, status, message, from, images?, session?}. Images are
stored by content hash.

A message typed on the card while Claude works is logged as status "note". The session's inbox hook
(inbox.py) hands it over; <task_id>.inbox.json records the `at` of the last note handed over.
"""
import errno
import hashlib
import json
import os
import re
import subprocess
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt

IMAGE_EXTS = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"GIF8": ".gif"}
MAX_IMAGE = 25 * 1024 * 1024
# Windows byte locks are mandatory: locking byte 0 would make every reader of the log fail, so lock one past any data.
LOCK_AT = 0x7FFFFFFE


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
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("agent_reports/\n")


def project_root(path="."):
    """The git top level containing `path`, else `path` itself."""
    r = subprocess.run(["git", "-C", path, "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
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


def prompt_path(board, task_id):
    return _path(board, task_id)[:-len(".json")] + ".prompt.txt"


def read(board, task_id):
    try:
        with open(_path(board, task_id), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _lock(f):
    if fcntl:
        fcntl.flock(f, fcntl.LOCK_EX)
        return
    os.lseek(f.fileno(), LOCK_AT, os.SEEK_SET)
    while True:
        try:
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
            return
        except OSError as e:  # LK_LOCK gives up after 10 tries a second apart; keep waiting
            if e.errno != getattr(errno, "EDEADLOCK", errno.EDEADLK):
                raise


def _unlock(f):
    if fcntl:
        return  # close releases the flock
    f.flush()
    os.lseek(f.fileno(), LOCK_AT, os.SEEK_SET)
    msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)


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
        _lock(f)
        try:
            f.seek(0)
            try:
                log = json.loads(f.read() or "[]")
            except json.JSONDecodeError:
                log = []
            log.append(entry)
            f.seek(0)
            f.truncate()
            json.dump(log, f, indent=2, ensure_ascii=False)
        finally:
            _unlock(f)
    return entry


def _inbox_path(board, task_id):
    return _path(board, task_id)[:-len(".json")] + ".inbox.json"


def _read_marker(f):
    f.seek(0)
    try:
        return json.loads(f.read() or "{}").get("delivered") or ""
    except (json.JSONDecodeError, AttributeError):
        return ""


def delivered(board, task_id):
    """The `at` of the last note handed to Claude ('' if none)."""
    try:
        with open(_inbox_path(board, task_id), encoding="utf-8") as f:
            return _read_marker(f)
    except FileNotFoundError:
        return ""


def pending_notes(log, since):
    # Same-format UTC isoformat strings sort in time order.
    return [e for e in log if e.get("status") == "note" and e.get("from") == "you" and e.get("at", "") > since]


def take_notes(board, task_id):
    """Mark every note not handed over yet as delivered and return (notes, previous marker). The lock makes one
    note go to one taker: the hook and the board's fallback can race at a turn's end."""
    path = _inbox_path(board, task_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        _lock(f)
        try:
            since = _read_marker(f)
            notes = pending_notes(read(board, task_id), since)
            if notes:
                f.seek(0)
                f.truncate()
                json.dump({"delivered": notes[-1]["at"]}, f)
        finally:
            _unlock(f)
    return notes, since


def untake_notes(board, task_id, notes, since):
    """Undo take_notes after a failed hand-over, unless a later take already moved the marker on."""
    path = _inbox_path(board, task_id)
    with open(path, "a+", encoding="utf-8") as f:
        _lock(f)
        try:
            if notes and _read_marker(f) == notes[-1]["at"]:
                f.seek(0)
                f.truncate()
                json.dump({"delivered": since}, f)
        finally:
            _unlock(f)


LANGUAGE_RULE = ("Answer in the language the user wrote their latest message in (the task notes, or their newest "
                 "reply), even when the app or project you work on uses another language.")


def notes_message(board, notes):
    """The text Claude gets for notes typed on the card while it worked."""
    parts = ["Message from the user on the task board, sent while you were working:" if len(notes) == 1 else
             f"{len(notes)} messages from the user on the task board, sent while you were working:"]
    for e in notes:
        parts.append(e["message"])
        if e.get("images"):
            parts.append("Images attached (open each with the Read tool):\n" + "\n".join(
                f"- {image_path(log_images(board), n)}" for n in e["images"]))
    parts.append("Take this into account in the work you are doing now. The user reads only the card: if it asks "
                 "you something, answer with report.py (progress, or done when you finish). " + LANGUAGE_RULE)
    return "\n\n".join(parts)
