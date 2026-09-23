#!/usr/bin/env python3
"""Task board: a local kanban with subtasks.

Run from anywhere:  python3 tools/task_board/server.py [--port 8765]
Then open http://127.0.0.1:8765 in a browser.

Tasks save to tools/task_board/tasks.json (commit it like any other file).
Opening index.html directly (file://) also works, but then tasks live only in
that browser's localStorage.

"Send to Claude" (task panel) starts `claude --bg` in the repo root with the
task's title, notes, subtasks and parent chain as the prompt. The session shows
in `claude agents`; `claude attach <id>` opens it. Permission mode defaults to
auto (--permission-mode to change).

The agent posts progress / question / done onto its card with report.py. A
watcher thread polls `claude agents --json --all` every few seconds and turns
session state + the latest report into a phase: working, needs_you (a question,
a permission prompt, or it stopped without reporting) or finished. Entering
needs_you or finished raises a macOS notification. A reply typed on the card
stops the idle session and resumes it with the reply as its next message
(`claude --bg --resume` on a live session would start a copy instead).

Images pasted into a card's notes save to images/ (listed on the task as
"images", committed with tasks.json); images pasted into a reply, or attached by
the agent with `report.py --image`, save to agent_reports/images/. The prompt
and replies hand Claude the absolute paths so it can open them with Read.
"""
import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import reports

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(HERE, "tasks.json")
CLAUDE = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PERMISSION_MODE = "auto"


def load_tasks():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


def image_lines(names, folder, indent=""):
    return [f"{indent}- {reports.image_path(folder, n)}" for n in names]


def build_prompt(state, t):
    by_id = {x["id"]: x for x in state["tasks"]}
    cols = {c["id"]: c for c in state["columns"]}
    chain = []
    p = by_id.get(t.get("parent"))
    while p:
        chain.insert(0, p)
        p = by_id.get(p.get("parent"))

    def subtree(pid, depth):
        out = []
        for k in sorted((x for x in state["tasks"] if x.get("parent") == pid), key=lambda x: x["order"]):
            done = "x" if cols.get(k["column"], {}).get("done") else " "
            out.append(f"{'  ' * depth}- [{done}] {k['title']}")
            if k.get("notes"):
                out.append(f"{'  ' * depth}  notes: {k['notes']}")
            if k.get("images"):
                out.append(f"{'  ' * depth}  images:")
                out += image_lines(k["images"], reports.TASK_IMAGES, "  " * depth + "    ")
            out += subtree(k["id"], depth + 1)
        return out

    lines = ["Work on this task from the project task board (tools/task_board).", "", f"Task: {t['title']}",
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
                  *image_lines(t["images"], reports.TASK_IMAGES)]
    subs = subtree(t["id"], 0)
    if subs:
        lines += ["", "Subtasks ([x] = already done):", *subs]
    report = f"python3 tools/task_board/report.py {t['id']}"
    lines += ["", "Follow CLAUDE.md. Commit your work; never push. Do not edit tools/task_board/tasks.json "
              "(the board owns it).", "",
              "Keep the card current with the report script (it shows on the board and alerts the user):",
              "- Format every message for reading on the card: short paragraphs and `- ` bullet lists separated by "
              "blank lines (real newlines inside the quoted argument), never one run-on block. A progress line may be "
              "a single sentence.",
              f'- `{report} progress "<one line>"` at each milestone.',
              f'- `{report} question "<the question, with options>"` when you need a decision, then END YOUR TURN; '
              "the answer arrives as your next message. Do not guess on decisions the user should make.",
              f'- `{report} done "<what you did, commits, what is left>"` as your last action when the task is finished.',
              "- Screenshots: any report about something you captured (a game frame, a UI shot, a render, a "
              "before/after) MUST attach the image files with `--image <file>` (repeatable), e.g. "
              f'`{report} progress "new HUD layout" --image /path/shot.png`. They show on the card.']
    return "\n".join(lines)


def notify(title, subtitle, message):
    esc = lambda x: x.replace("\\", "\\\\").replace('"', '\\"')[:200]
    subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}" '
                    f'subtitle "{esc(subtitle)}" sound name "Glass"'], capture_output=True, timeout=10)


def launch(task_id):
    state = load_tasks()
    t = next((x for x in state["tasks"] if x["id"] == task_id), None)
    if not t:
        raise LookupError("no such task (save first?)")
    name = "task: " + t["title"][:60]
    r = subprocess.run([CLAUDE, "--bg", "-n", name, "--permission-mode", PERMISSION_MODE, build_prompt(state, t)],
                       cwd=REPO, capture_output=True, text=True, timeout=60)
    out = ANSI.sub("", r.stdout + r.stderr)
    m = re.search(r"backgrounded\s*·\s*([0-9a-f]+)", out)
    if r.returncode != 0 or not m:
        raise RuntimeError(out.strip() or f"claude exited {r.returncode}")
    reports.append(task_id, "launch", f"Sent to Claude (session {m.group(1)})", "you", session=m.group(1))
    WATCH.expect(task_id, m.group(1))
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


class Watcher:
    """Polls session state for every task that has an agent; the board reads the cached result."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}       # task_id -> {id, sessionId, state, phase, reason, log}
        self.launched = {}    # agent id -> launch time, for sessions not listed yet
        self.primed = False   # first pass only records, so a restart doesn't re-alert old sessions

    def expect(self, task_id, agent_id):
        with self.lock:
            self.launched[agent_id] = time.time()
            self.cache[task_id] = {"id": agent_id, "sessionId": None, "state": "starting", "phase": "working",
                                   "reason": "", "log": reports.read(task_id)}

    def poll(self):
        try:
            tasks = [t for t in load_tasks()["tasks"] if t.get("agent")]
        except (OSError, ValueError):
            return
        r = subprocess.run([CLAUDE, "agents", "--json", "--all"], cwd=REPO, capture_output=True, text=True, timeout=30)
        sessions = {s["id"]: s for s in json.loads(r.stdout or "[]")}
        fresh = {}
        for t in tasks:
            log = reports.read(t["id"])
            aid = next((e["session"] for e in reversed(log) if e.get("session")), t["agent"]["id"])
            s = sessions.get(aid)
            phase, reason = phase_of(s, log, self.launched.get(aid, 0))
            fresh[t["id"]] = {"id": aid, "sessionId": s and s.get("sessionId"), "state": s and s.get("state"),
                              "phase": phase, "reason": reason, "log": log}
        with self.lock:
            old, self.cache = self.cache, fresh
            primed, self.primed = self.primed, True
        if not primed:
            return
        for t in tasks:
            was, now_ = (old.get(t["id"]) or {}).get("phase"), fresh[t["id"]]["phase"]
            if now_ == was or (was is None and fresh[t["id"]]["id"] not in self.launched):
                continue
            if now_ == "needs_you":
                notify("Claude needs you", t["title"], fresh[t["id"]]["reason"])
            elif now_ == "finished":
                notify("Claude finished", t["title"], fresh[t["id"]]["reason"])

    def run(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # noqa: BLE001 — keep watching through a bad poll
                print("watcher:", e)
            time.sleep(4)

    def snapshot(self):
        with self.lock:
            return dict(self.cache)


WATCH = Watcher()


def reply(task_id, text, images=()):
    info = WATCH.snapshot().get(task_id)
    if not info or not info.get("sessionId"):
        raise LookupError("no Claude session for this task")
    if info["phase"] == "working":
        raise PermissionError("Claude is still working — wait until it stops")
    # A live idle session must be stopped first, and fully (its pid gone): --resume on a
    # running one starts a copy instead of waking it.
    subprocess.run([CLAUDE, "stop", info["id"]], cwd=REPO, capture_output=True, text=True, timeout=30)
    for _ in range(40):
        r = subprocess.run([CLAUDE, "agents", "--json", "--all"], cwd=REPO, capture_output=True, text=True, timeout=30)
        s = next((x for x in json.loads(r.stdout or "[]") if x["id"] == info["id"]), None)
        if not s or "pid" not in s:
            break
        time.sleep(0.25)
    time.sleep(1)  # resuming the instant the pid goes has still produced a copy; the board follows either way
    message = text
    if images:
        message += "\n\nImages attached to this reply (open each with the Read tool):\n" + "\n".join(
            image_lines(images, reports.LOG_IMAGES))
    r = subprocess.run([CLAUDE, "--bg", "--resume", info["sessionId"], message],
                       cwd=REPO, capture_output=True, text=True, timeout=60)
    out = ANSI.sub("", r.stdout + r.stderr)
    m = re.search(r"backgrounded\s*·\s*([0-9a-f]+)", out)
    if r.returncode != 0 or not m:
        raise RuntimeError(out.strip() or f"claude exited {r.returncode}")
    reports.append(task_id, "reply", text, "you", session=m.group(1), images=list(images))
    WATCH.expect(task_id, m.group(1))


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
        # Any web page can POST to localhost; only the board itself may launch agents.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        return (self.headers.get("Content-Type", "").startswith("application/json")
                and origin in (f"http://{host}", None))

    def do_GET(self):
        if self.path == "/api/tasks":
            if os.path.exists(DATA):
                with open(DATA, "rb") as f:
                    self._send(200, f.read())
            else:
                self._send(200, b"null")
            return
        if self.path == "/api/agents":
            self._json(200, WATCH.snapshot())
            return
        super().do_GET()

    def do_POST(self):
        if self.path not in ("/api/agent", "/api/agent/reply", "/api/image"):
            self._send(404)
            return
        if not self._same_origin():
            self._json(403, {"error": "cross-origin request refused"})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            if self.path == "/api/agent":
                self._json(200, {"id": launch(body["taskId"])})
            elif self.path == "/api/image":
                folder = reports.TASK_IMAGES if body.get("kind") == "task" else reports.LOG_IMAGES
                self._json(200, {"name": reports.store_image(base64.b64decode(body["data"]), folder)})
            else:
                images = [n for n in body.get("images", []) if os.path.exists(reports.image_path(reports.LOG_IMAGES, n))]
                reply(body["taskId"], body["text"].strip() or ("See the attached images." if images else "Go ahead."), images)
                self._json(200, {"ok": True})
        except LookupError as e:
            self._json(404, {"error": str(e)})
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except PermissionError as e:
            self._json(409, {"error": str(e)})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": str(e)})

    def do_PUT(self):
        if self.path != "/api/tasks":
            self._send(404)
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            data = json.loads(raw)
            if not isinstance(data.get("columns"), list) or not isinstance(data.get("tasks"), list):
                raise ValueError("need columns and tasks lists")
        except (ValueError, AttributeError) as e:
            self._send(400, json.dumps({"error": str(e)}).encode())
            return
        # Write-then-rename so a crash mid-write never leaves a truncated tasks.json.
        fd, tmp = tempfile.mkstemp(dir=HERE, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, DATA)
        self._send(204)

    def log_message(self, fmt, *args):
        if "/api/" not in self.path:
            super().log_message(fmt, *args)


def main():
    global PERMISSION_MODE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--permission-mode", default=PERMISSION_MODE,
                    help="permission mode for agents started with Send to Claude (default: auto)")
    args = ap.parse_args()
    PERMISSION_MODE = args.permission_mode
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=WATCH.run, daemon=True).start()
    print(f"Task board: http://127.0.0.1:{args.port}  (saving to {DATA})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
