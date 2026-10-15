"""Per-project board storage shared by report.py (the agent side) and server.py (the board side).

A project's board lives in `board_dir(root)`: <root>/.task_board/ (or, for a repo that
already keeps one there, <root>/tools/task_board/). It holds
- tasks.json            the board itself
- images/               images pasted into a card's notes
- agent_reports/        <task_id>.json message logs + images/ from replies and
                        report.py --image; run chatter
                        (<task_id>.recap.md: the recap written when a card moves to done;
                        <task_id>.close: moved to done, sessions not closed yet)

None of it is committed: a new board folder ignores itself (ensure_board).

A log is a list of {at, status, message, from, images?, decision?, options?, parts?, session?}. Images are
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
import tempfile
import time
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
    """Create the board folder; a new one gets a .gitignore that keeps all of it (itself too) out of git."""
    os.makedirs(board, exist_ok=True)
    ignore = os.path.join(board, ".gitignore")
    if not os.path.exists(ignore) and os.path.basename(board) == ".task_board":
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("*\n")


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
        # Write aside and swap in whole: a cut-off file would pass the exists() check forever.
        fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
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


def recap_path(board, task_id):
    """What the task's closed sessions did, written when its card moved to done; a reply starts a new session from it."""
    return _path(board, task_id)[:-len(".json")] + ".recap.md"


def close_path(board, task_id):
    """Marks a card moved to done whose sessions still have to be recapped and closed."""
    return _path(board, task_id)[:-len(".json")] + ".close"


def read_recap(board, task_id):
    try:
        with open(recap_path(board, task_id), encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, UnicodeDecodeError):
        return ""


def _load(path):
    with open(path, encoding="utf-8") as f:
        log = json.load(f)
    if not isinstance(log, list):
        raise ValueError("not a list")
    return log


def read(board, task_id):
    try:
        return _load(_path(board, task_id))
    except (FileNotFoundError, ValueError):  # ValueError covers bad JSON and bad UTF-8
        return []


def _set_aside(path):
    """Move a log that does not parse to <path>.bad (or .bad2, ...) so the next write does not wipe it."""
    bad, n = path + ".bad", 1
    while os.path.exists(bad):
        n += 1
        bad = f"{path}.bad{n}"
    os.replace(path, bad)


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


class _Locked:
    """Holds an exclusive lock on <path>.lock. The data file itself is only ever swapped in whole by
    _write_json, so a reader without the lock (the board's 4 s poll) never sees it empty or half-written."""

    def __init__(self, path):
        self.path = path + ".lock"

    def __enter__(self):
        self.f = open(self.path, "a+")
        _lock(self.f)
        return self

    def __exit__(self, *exc):
        try:
            _unlock(self.f)
        finally:
            self.f.close()


def _write_json(path, obj):
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, ensure_ascii=False)
    for tries in range(40 if fcntl is None else 1, 0, -1):
        try:
            return os.replace(tmp, path)
        except PermissionError:  # Windows: a reader has `path` open
            if tries == 1:
                os.unlink(tmp)
                raise
            time.sleep(0.05)


def check_decision(d):
    """A decision report's JSON as stored: the query, the options (label, why), the index chosen (None = still open), who chose."""
    if not isinstance(d, dict) or not str(d.get("query") or "").strip():
        raise ValueError('a decision needs a "query"')
    opts = d.get("options")
    if not isinstance(opts, list) or not 2 <= len(opts) <= 6:
        raise ValueError('"options" needs 2 to 6 entries')
    clean = []
    for o in opts:
        label = str(o.get("label") or "").strip() if isinstance(o, dict) else ""
        if not label:
            raise ValueError('every option needs a "label"')
        clean.append({"label": label, "why": str(o.get("why") or "").strip()})
    chosen = d.get("chosen")
    if chosen is not None and (not isinstance(chosen, int) or isinstance(chosen, bool) or not 0 <= chosen < len(clean)):
        raise ValueError('"chosen" is an option index (0 to %d), or null while open' % (len(clean) - 1))
    by = d.get("by") or "agent"
    if by not in ("agent", "you"):
        raise ValueError('"by" is "agent" or "you"')
    return {"query": str(d["query"]).strip(), "options": clean, "chosen": chosen, "by": by,
            "why": str(d.get("why") or "").strip()}


def check_options(labels):
    """A question's answer choices as stored: 2 to 6 trimmed, non-empty, distinct labels."""
    clean = [str(o).strip() for o in labels]
    if not 2 <= len(clean) <= 6:
        raise ValueError("a question needs 2 to 6 --option choices")
    if not all(clean):
        raise ValueError("every --option needs a label")
    if len(set(clean)) != len(clean):
        raise ValueError("every --option must be different")
    return clean


def check_parts(parts):
    """A many-part question as stored: 1 to 8 parts of {"q": the part's question, "options": check_options()}."""
    if not 1 <= len(parts) <= 8:
        raise ValueError("a question takes 1 to 8 --part")
    clean = []
    for p in parts:
        q = str(p.get("q") or "").strip()
        if not q:
            raise ValueError("every --part needs its question")
        try:
            opts = check_options(p.get("options") or [])
        except ValueError as e:
            raise ValueError(f'part "{q}": {e}') from None
        clean.append({"q": q, "options": opts})
    if len({p["q"] for p in clean}) != len(clean):
        raise ValueError("every --part must be different")
    return clean


def decision_message(d):
    """The plain line a decision also carries (what older readers show)."""
    if d["chosen"] is None:
        return f"Decision open: {d['query']}"
    return f"Decision: {d['query']} -> {d['options'][d['chosen']]['label']}"


def append(board, task_id, status, message, sender, session=None, images=None, decision=None, options=None,
           parts=None):
    path = _path(board, task_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {"at": datetime.now(timezone.utc).isoformat(), "status": status, "message": message, "from": sender}
    if images:
        entry["images"] = images  # names in log_images(board)
    if decision:
        entry["decision"] = decision  # check_decision(); the card draws it as a tree
    if options:
        entry["options"] = options  # check_options(); the card shows a button per choice
    if parts:
        entry["parts"] = parts  # check_parts(); the card shows a row of choices per part and one send button
    if session:
        entry["session"] = session  # the board follows the newest one (a resume can land in a new session)
    # report.py and a board reply can land together; the lock keeps both entries.
    with _Locked(path):
        try:
            log = _load(path)
        except FileNotFoundError:
            log = []
        except ValueError:
            _set_aside(path)
            log = []
        log.append(entry)
        _write_json(path, log)
    return entry


def _inbox_path(board, task_id):
    return _path(board, task_id)[:-len(".json")] + ".inbox.json"


def delivered(board, task_id):
    """The `at` of the last note handed to Claude ('' if none)."""
    try:
        with open(_inbox_path(board, task_id), encoding="utf-8") as f:
            return json.loads(f.read() or "{}").get("delivered") or ""
    except (FileNotFoundError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
        return ""


def pending_notes(log, since):
    # Same-format UTC isoformat strings sort in time order.
    return [e for e in log if e.get("status") == "note" and e.get("from") == "you" and e.get("at", "") > since]


def take_notes(board, task_id):
    """Mark every note not handed over yet as delivered and return (notes, previous marker). The lock makes one
    note go to one taker: the hook and the board's fallback can race at a turn's end."""
    path = _inbox_path(board, task_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with _Locked(path):
        since = delivered(board, task_id)
        notes = pending_notes(read(board, task_id), since)
        if notes:
            _write_json(path, {"delivered": notes[-1]["at"]})
    return notes, since


def untake_notes(board, task_id, notes, since):
    """Undo take_notes after a failed hand-over, unless a later take already moved the marker on."""
    path = _inbox_path(board, task_id)
    with _Locked(path):
        if notes and delivered(board, task_id) == notes[-1]["at"]:
            _write_json(path, {"delivered": since})


LANGUAGE_RULE = ("Answer in the language the user wrote their latest message in (the task notes, or their newest "
                 "reply), even when the app or project you work on uses another language. A task ABOUT a "
                 "language (translating, adding French) is not a request to answer in it: check the language "
                 "of the user's latest message each time you write a report.")


# Function words only one of these languages uses; a guess needs several hits and a clear lead.
LANG_WORDS = {
    "English": set("the and is are of that this with for was were not be have has what when from but which will "
                   "can does should would there they we my your it's don't i'm".split()),
    "French": set("le les des du est et une un pas pour dans avec sur ce cette je tu il nous vous sont mais ou au aux "
                  "c'est j'ai n'est ne plus fait être très ça qu'il aussi".split()),
    "Spanish": set("el los las es y por para con del pero como está muy yo también una".split()),
    "German": set("der die das und ist nicht ein eine mit für auf ich sie wir auch nur wenn aber oder".split()),
}
_NOT_PROSE = re.compile(r"```.*?```|`[^`]*`|https?://\S+|\S*[/\\]\S*", re.S)


def guess_language(text, need=4):
    """The language `text` is written in, or None when it is too short or mixed to tell. Code, URLs and paths
    are ignored, so a report quoting French UI strings in backticks still reads as English."""
    words = re.findall(r"[^\W\d_]+(?:'[^\W\d_]+)?", _NOT_PROSE.sub(" ", text.lower().replace("’", "'")))
    hits = sorted(((sum(w in ws for w in words), lang) for lang, ws in LANG_WORDS.items()), reverse=True)
    (best, lang), (second, _) = hits[0], hits[1]
    return lang if best >= need and best >= 3 * second else None


_QUOTED = re.compile(r'"[^"\n]*"|“[^”]*”|«[^»]*»|^>.*$', re.M)
_PASTE = re.compile(r":[ \t]*\n")


def user_language(text):
    """The language of the user's own words in `text`. Quoted text is left out. Text pasted after a line ending
    in ':' ("Translate this to English:" + a French paragraph) only counts when the line before it agrees, and
    paragraphs in two languages mean unsure."""
    text = _QUOTED.sub(" ", text)
    if len({guess_language(p) for p in re.split(r"\n\s*\n|:[ \t]*\n", text)} - {None}) > 1:
        return None
    lang = guess_language(text)
    head = _PASTE.split(text, maxsplit=1)
    return None if lang and len(head) > 1 and guess_language(head[0], need=1) != lang else lang


USER_SAYS = ("reply", "note")
# server.handback writes the user's word on which findings to fix behind this board line, or HANDBACK_ASK when
# they typed nothing.
HANDBACK = "Sent the review findings back to {}: "
HANDBACK_ASK = "Fix the findings you agree with; say which ones you skip and why."
_HANDBACK = re.compile("^" + re.escape(HANDBACK).replace(r"\{\}", r"\w+"))


def latest_user_text(board, task_id):
    """The user's newest message to this task: their last reply or note on the card, else the card's title and
    notes. Board-written text is left out."""
    for e in reversed(read(board, task_id)):
        if e.get("from") == "you" and e.get("status") in USER_SAYS:
            said = _HANDBACK.sub("", e.get("message", ""), count=1).strip()
            if said and said != HANDBACK_ASK:
                return said
    try:
        with open(os.path.join(board, "tasks.json"), encoding="utf-8") as f:
            tasks = json.load(f).get("tasks", [])
    except (OSError, ValueError, AttributeError):
        return ""
    t = next((t for t in tasks if isinstance(t, dict) and t.get("id") == task_id), {})
    return f"{t.get('title', '')}\n\n{t.get('notes', '')}".strip()


def language_mismatch(board, task_id, message):
    """(report language, user language) when a report is plainly in another language than the user's latest
    message, else None. Unsure on either side means no mismatch: a false refusal costs more than a miss."""
    mine, theirs = guess_language(message), user_language(latest_user_text(board, task_id))
    return (mine, theirs) if mine and theirs and mine != theirs else None


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
