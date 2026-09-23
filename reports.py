"""Per-task message log shared by report.py (the agent side) and server.py (the board side).

agent_reports/<task_id>.json is a list of {at, status, message, from, images?}; it is
kept out of git (.gitignore) because it is run chatter, not task data.

Images are stored by content hash: task images (pasted into a card's notes) in
images/, committed with tasks.json; images on log entries (Claude's screenshots,
images pasted into a reply) in agent_reports/images/.
"""
import fcntl
import hashlib
import json
import os
import re
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
DIR = os.path.join(HERE, "agent_reports")
TASK_IMAGES = os.path.join(HERE, "images")
LOG_IMAGES = os.path.join(DIR, "images")
IMAGE_EXTS = {b"\x89PNG": ".png", b"\xff\xd8\xff": ".jpg", b"GIF8": ".gif"}
MAX_IMAGE = 25 * 1024 * 1024


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


def append(task_id, status, message, sender, session=None, images=None):
    os.makedirs(DIR, exist_ok=True)
    entry = {"at": datetime.now(timezone.utc).isoformat(), "status": status, "message": message, "from": sender}
    if images:
        entry["images"] = images  # names in LOG_IMAGES
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
