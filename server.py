#!/usr/bin/env python3
"""Task board: a local kanban with subtasks, for any project folder.

Run from anywhere:  python3 server.py [--port 8765] [--project PATH]
Then open http://127.0.0.1:8765 in a browser.

The header's project menu switches boards: "Open folder…" picks any folder
(a native dialog, or a pasted path) and remembers it in
~/.config/task_board/projects.json. Each project keeps its own board in
<project>/.task_board/ (see reports.py; a repo that already has
tools/task_board/tasks.json keeps using that). Commit tasks.json and images/.
With no projects remembered yet, the git repo containing the current directory
(or --project) is opened. Opening index.html directly (file://) also works, but
then tasks live only in that browser's localStorage.

"Send to Claude" (task panel) starts `claude --bg` in the project folder with
the task's title, notes, subtasks and parent chain as the prompt. The session
shows in `claude agents`; `claude attach <id>` opens it. Permission mode
defaults to auto (--permission-mode to change).

The agent posts progress / question / done onto its card with report.py. A
watcher thread polls `claude agents --json --all` every few seconds and, for
every remembered project, turns session state + the latest report into a phase:
working, needs_you (a question, a permission prompt, or it stopped without
reporting) or finished. Entering needs_you or finished raises a macOS
notification. A reply typed on the card stops the idle session and resumes it
with the reply as its next message (`claude --bg --resume` on a live session
would start a copy instead).

Images pasted into a card's notes save to <board>/images/ (listed on the task as
"images"); images pasted into a reply, or attached by the agent with
`report.py --image`, save to <board>/agent_reports/images/. The prompt and
replies hand Claude the absolute paths so it can open them with Read.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import reports

HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.expanduser("~/.config/task_board/projects.json")
CLAUDE = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PERMISSION_MODE = "auto"


# ---------------------------------------------------------------- projects
class Project:
    def __init__(self, path):
        self.path = os.path.realpath(path)
        self.id = hashlib.sha1(self.path.encode()).hexdigest()[:10]
        self.name = os.path.basename(self.path) or self.path
        self.board = reports.board_dir(self.path)
        self.data = os.path.join(self.board, "tasks.json")

    def info(self):
        return {"id": self.id, "path": self.path, "name": self.name, "board": self.board}


REG_LOCK = threading.Lock()


def _read_registry():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            return json.load(f).get("projects", [])
    except (OSError, ValueError):
        return []


def _write_registry(entries):
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(REGISTRY), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"projects": entries}, f, indent=2)
    os.replace(tmp, REGISTRY)


def projects():
    """Remembered projects whose folder still exists, most recently opened first."""
    entries = sorted(_read_registry(), key=lambda e: e.get("opened", ""), reverse=True)
    return [Project(e["path"]) for e in entries if os.path.isdir(e["path"])]


def project(pid):
    p = next((x for x in projects() if x.id == pid), None)
    if not p:
        raise LookupError("unknown project (open its folder again)")
    return p


def open_project(path):
    path = os.path.expanduser(path.strip())
    if not os.path.isdir(path):
        raise ValueError(f"not a folder: {path}")
    p = Project(path)
    with REG_LOCK:
        entries = [e for e in _read_registry() if os.path.realpath(e["path"]) != p.path]
        entries.append({"path": p.path, "opened": datetime.now(timezone.utc).isoformat()})
        _write_registry(entries)
    return p


def forget_project(pid):
    with REG_LOCK:
        _write_registry([e for e in _read_registry() if Project(e["path"]).id != pid])


def pick_folder():
    """Native folder dialog; None when cancelled."""
    if shutil.which("osascript") is None:
        raise RuntimeError("no folder dialog on this system — paste the folder's path instead")
    r = subprocess.run(["osascript", "-e", "activate", "-e",
                        'POSIX path of (choose folder with prompt "Choose a project folder for the task board")'],
                       capture_output=True, text=True, timeout=600)
    if r.returncode != 0:
        if "-128" in r.stderr:  # user cancelled
            return None
        raise RuntimeError(r.stderr.strip() or "folder dialog failed")
    return r.stdout.strip()


# ---------------------------------------------------------------- prompt
def load_tasks(p):
    with open(p.data, encoding="utf-8") as f:
        return json.load(f)


def image_lines(names, folder, indent=""):
    return [f"{indent}- {reports.image_path(folder, n)}" for n in names]


def build_prompt(p, state, t):
    by_id = {x["id"]: x for x in state["tasks"]}
    cols = {c["id"]: c for c in state["columns"]}
    chain = []
    parent = by_id.get(t.get("parent"))
    while parent:
        chain.insert(0, parent)
        parent = by_id.get(parent.get("parent"))

    def subtree(pid, depth):
        out = []
        for k in sorted((x for x in state["tasks"] if x.get("parent") == pid), key=lambda x: x["order"]):
            done = "x" if cols.get(k["column"], {}).get("done") else " "
            out.append(f"{'  ' * depth}- [{done}] {k['title']}")
            if k.get("notes"):
                out.append(f"{'  ' * depth}  notes: {k['notes']}")
            if k.get("images"):
                out.append(f"{'  ' * depth}  images:")
                out += image_lines(k["images"], reports.task_images(p.board), "  " * depth + "    ")
            out += subtree(k["id"], depth + 1)
        return out

    board_rel = os.path.relpath(p.board, p.path)
    lines = [f"Work on this task from the project task board ({board_rel}).", "", f"Task: {t['title']}",
             f"Task id: {t['id']}"]
    if chain:
        lines.append("Part of: " + " > ".join(x["title"] for x in chain))
        if chain[-1].get("notes"):
            lines.append(f"Parent notes: {chain[-1]['notes']}")
    if t.get("priority"):
        lines.append(f"Priority: {t['priority']}")
    if t.get("tags"):
        lines.append("Tags: " + ", ".join(t["tags"]))
    if t.get("notes"):
        lines += ["", "Notes:", t["notes"]]
    if t.get("images"):
        lines += ["", "Images pasted on this task (open each with the Read tool; they are part of the brief):",
                  *image_lines(t["images"], reports.task_images(p.board))]
    subs = subtree(t["id"], 0)
    if subs:
        lines += ["", "Subtasks ([x] = already done):", *subs]
    # The board may live outside this project's tree, so the command carries absolute paths.
    report = f"python3 {shlex.quote(os.path.join(HERE, 'report.py'))} --board {shlex.quote(p.board)} {t['id']}"
    lines += ["", f"Follow CLAUDE.md if the project has one. Commit your work; never push. Do not edit "
              f"{os.path.join(board_rel, 'tasks.json')} (the board owns it).", "",
              "Keep the card current with the report script (it shows on the board and alerts the user):",
              "- Format every message for reading on the card: short paragraphs and `- ` bullet lists separated by "
              "blank lines (real newlines inside the quoted argument), never one run-on block. A progress line may be "
              "a single sentence.",
              f'- `{report} progress "<one line>"` at each milestone.',
              f'- `{report} question "<the question, with options>"` when you need a decision, then END YOUR TURN; '
              "the answer arrives as your next message. Do not guess on decisions the user should make.",
              f'- `{report} done "<what you did, commits, what is left>"` as your last action when the task is finished.',
              "- Screenshots: any report about something you captured (an app or game frame, a UI shot, a render, a "
              "before/after) MUST attach the image files with `--image <file>` (repeatable), e.g. "
              f'`{report} progress "new HUD layout" --image /path/shot.png`. They show on the card.']
    return "\n".join(lines)


def notify(title, subtitle, message):
    esc = lambda x: x.replace("\\", "\\\\").replace('"', '\\"')[:200]
    subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}" '
                    f'subtitle "{esc(subtitle)}" sound name "Glass"'], capture_output=True, timeout=10)


# ---------------------------------------------------------------- agents
def launch(p, task_id):
    state = load_tasks(p)
    t = next((x for x in state["tasks"] if x["id"] == task_id), None)
    if not t:
        raise LookupError("no such task (save first?)")
    name = "task: " + t["title"][:60]
    r = subprocess.run([CLAUDE, "--bg", "-n", name, "--permission-mode", PERMISSION_MODE, build_prompt(p, state, t)],
                       cwd=p.path, capture_output=True, text=True, timeout=60)
    out = ANSI.sub("", r.stdout + r.stderr)
    m = re.search(r"backgrounded\s*·\s*([0-9a-f]+)", out)
    if r.returncode != 0 or not m:
        raise RuntimeError(out.strip() or f"claude exited {r.returncode}")
    reports.append(p.board, task_id, "launch", f"Sent to Claude (session {m.group(1)})", "you", session=m.group(1))
    WATCH.expect(p, task_id, m.group(1))
    return m.group(1)


def phase_of(session, log, launched_at):
    """(phase, reason) from the CLI's session record and the card's message log."""
    last = log[-1] if log else {}
    st = session and session.get("state")
    # Just after a launch or reply the session is still starting (absent, or stopped from the reply's stop).
    if st == "working" or (last.get("from") == "you" and time.time() - launched_at < 45):
        return "working", ""
    if session is None:
        return "gone", "Session was removed"
    # An explicit report beats the CLI's own guess (it marks some finished sessions "blocked").
    if last.get("status") == "question":
        return "needs_you", last["message"]
    if last.get("status") == "done":
        return "finished", last["message"]
    if st == "blocked":
        return "needs_you", "Waiting on a permission prompt or a question in the session — attach to answer"
    return "needs_you", "Stopped without reporting — attach or reply to check on it"


def list_sessions(cwd):
    r = subprocess.run([CLAUDE, "agents", "--json", "--all"], cwd=cwd, capture_output=True, text=True, timeout=30)
    return json.loads(r.stdout or "[]")


class Watcher:
    """Polls session state for every task with an agent, in every remembered project; the board reads the cache."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}       # (project id, task id) -> {id, sessionId, state, phase, reason, log}
        self.launched = {}    # agent id -> launch time, for sessions not listed yet
        self.primed = False   # first pass only records, so a restart doesn't re-alert old sessions

    def expect(self, p, task_id, agent_id):
        with self.lock:
            self.launched[agent_id] = time.time()
            self.cache[(p.id, task_id)] = {"id": agent_id, "sessionId": None, "state": "starting", "phase": "working",
                                           "reason": "", "log": reports.read(p.board, task_id)}

    def poll(self):
        work = []
        for p in projects():
            try:
                work += [(p, t) for t in load_tasks(p)["tasks"] if t.get("agent")]
            except (OSError, ValueError):
                continue
        sessions = {s["id"]: s for s in list_sessions(HERE)} if work else {}
        fresh = {}
        for p, t in work:
            log = reports.read(p.board, t["id"])
            aid = next((e["session"] for e in reversed(log) if e.get("session")), t["agent"]["id"])
            s = sessions.get(aid)
            phase, reason = phase_of(s, log, self.launched.get(aid, 0))
            fresh[(p.id, t["id"])] = {"id": aid, "sessionId": s and s.get("sessionId"), "state": s and s.get("state"),
                                      "phase": phase, "reason": reason, "log": log}
        with self.lock:
            old, self.cache = self.cache, fresh
            primed, self.primed = self.primed, True
        if not primed:
            return
        for p, t in work:
            key = (p.id, t["id"])
            was, now_ = (old.get(key) or {}).get("phase"), fresh[key]["phase"]
            if now_ == was or (was is None and fresh[key]["id"] not in self.launched):
                continue
            if now_ == "needs_you":
                notify("Claude needs you", f"{p.name}: {t['title']}", fresh[key]["reason"])
            elif now_ == "finished":
                notify("Claude finished", f"{p.name}: {t['title']}", fresh[key]["reason"])

    def run(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # noqa: BLE001 — keep watching through a bad poll
                print("watcher:", e)
            time.sleep(4)

    def snapshot(self, pid):
        with self.lock:
            return {tid: v for (p, tid), v in self.cache.items() if p == pid}


WATCH = Watcher()


def reply(p, task_id, text, images=()):
    info = WATCH.snapshot(p.id).get(task_id)
    if not info or not info.get("sessionId"):
        raise LookupError("no Claude session for this task")
    if info["phase"] == "working":
        raise PermissionError("Claude is still working — wait until it stops")
    # A live idle session must be stopped first, and fully (its pid gone): --resume on a
    # running one starts a copy instead of waking it.
    subprocess.run([CLAUDE, "stop", info["id"]], cwd=p.path, capture_output=True, text=True, timeout=30)
    for _ in range(40):
        s = next((x for x in list_sessions(p.path) if x["id"] == info["id"]), None)
        if not s or "pid" not in s:
            break
        time.sleep(0.25)
    time.sleep(1)  # resuming the instant the pid goes has still produced a copy; the board follows either way
    message = text
    if images:
        message += "\n\nImages attached to this reply (open each with the Read tool):\n" + "\n".join(
            image_lines(images, reports.log_images(p.board)))
    r = subprocess.run([CLAUDE, "--bg", "--resume", info["sessionId"], message],
                       cwd=p.path, capture_output=True, text=True, timeout=60)
    out = ANSI.sub("", r.stdout + r.stderr)
    m = re.search(r"backgrounded\s*·\s*([0-9a-f]+)", out)
    if r.returncode != 0 or not m:
        raise RuntimeError(out.strip() or f"claude exited {r.returncode}")
    reports.append(p.board, task_id, "reply", text, "you", session=m.group(1), images=list(images))
    WATCH.expect(p, task_id, m.group(1))


# ---------------------------------------------------------------- http
FILE_ROUTE = re.compile(r"/files/([0-9a-f]{10})/(images|agent_reports/images)/([^/]+)")


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def _same_origin(self):
        # Any web page can POST to localhost; only the board itself may launch agents or write files.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        return (self.headers.get("Content-Type", "").startswith("application/json")
                and origin in (f"http://{host}", None))

    def _route(self):
        u = urlsplit(self.path)
        return u.path, parse_qs(u.query).get("p", [""])[0]

    def _errors(self, fn):
        try:
            fn()
        except LookupError as e:
            self._json(404, {"error": str(e)})
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except PermissionError as e:
            self._json(409, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": str(e)})

    def do_GET(self):
        path, pid = self._route()
        m = FILE_ROUTE.fullmatch(path)
        if m:
            def serve():
                p = project(m.group(1))
                folder = reports.task_images(p.board) if m.group(2) == "images" else reports.log_images(p.board)
                f = reports.image_path(folder, m.group(3))
                with open(f, "rb") as fh:
                    ext = f.rsplit(".", 1)[1]
                    self._send(200, fh.read(), "image/" + ("jpeg" if ext == "jpg" else ext))
            self._errors(serve)
        elif path == "/api/projects":
            self._json(200, [p.info() for p in projects()])
        elif path == "/api/tasks":
            def tasks():
                p = project(pid)
                if os.path.exists(p.data):
                    with open(p.data, "rb") as f:
                        self._send(200, f.read())
                else:
                    self._send(200, b"null")
            self._errors(tasks)
        elif path == "/api/agents":
            self._errors(lambda: self._json(200, WATCH.snapshot(project(pid).id)))
        else:
            super().do_GET()

    def do_POST(self):
        path, _ = self._route()
        routes = ("/api/agent", "/api/agent/reply", "/api/image", "/api/projects", "/api/projects/pick",
                  "/api/projects/forget")
        if path not in routes:
            self._send(404)
            return
        if not self._same_origin():
            self._json(403, {"error": "cross-origin request refused"})
            return

        def handle():
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if path == "/api/projects":
                self._json(200, open_project(body["path"]).info())
            elif path == "/api/projects/pick":
                folder = pick_folder()
                self._json(200, open_project(folder).info() if folder else {"cancelled": True})
            elif path == "/api/projects/forget":
                forget_project(body["p"])
                self._json(200, {"ok": True})
            elif path == "/api/agent":
                self._json(200, {"id": launch(project(body["p"]), body["taskId"])})
            elif path == "/api/image":
                p = project(body["p"])
                folder = reports.task_images(p.board) if body.get("kind") == "task" else reports.log_images(p.board)
                self._json(200, {"name": reports.store_image(base64.b64decode(body["data"]), folder)})
            else:
                p = project(body["p"])
                images = [n for n in body.get("images", [])
                          if os.path.exists(reports.image_path(reports.log_images(p.board), n))]
                reply(p, body["taskId"], body["text"].strip() or ("See the attached images." if images else "Go ahead."),
                      images)
                self._json(200, {"ok": True})
        self._errors(handle)

    def do_PUT(self):
        path, pid = self._route()
        if path != "/api/tasks":
            self._send(404)
            return
        if not self._same_origin():
            self._json(403, {"error": "cross-origin request refused"})
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))

        def write():
            p = project(pid)
            try:
                data = json.loads(raw)
                ok = isinstance(data.get("columns"), list) and isinstance(data.get("tasks"), list)
            except (ValueError, AttributeError):
                ok = False
            if not ok:
                raise ValueError("need columns and tasks lists")
            reports.ensure_board(p.board)
            # Write-then-rename so a crash mid-write never leaves a truncated tasks.json.
            fd, tmp = tempfile.mkstemp(dir=p.board, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            os.replace(tmp, p.data)
            self._send(204)
        self._errors(write)

    def log_message(self, fmt, *args):
        if "/api/" not in self.path and "/files/" not in self.path:
            super().log_message(fmt, *args)


def main():
    global PERMISSION_MODE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--project", help="open this folder's board (default with none remembered: the current git repo)")
    ap.add_argument("--permission-mode", default=PERMISSION_MODE,
                    help="permission mode for agents started with Send to Claude (default: auto)")
    args = ap.parse_args()
    PERMISSION_MODE = args.permission_mode
    if args.project:
        open_project(args.project)
    elif not projects():
        open_project(reports.project_root())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=WATCH.run, daemon=True).start()
    print(f"Task board: http://127.0.0.1:{args.port}  (projects: {', '.join(p.name for p in projects())})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
