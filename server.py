#!/usr/bin/env python3
"""Task board: a local kanban with subtasks.

Run from anywhere:  python3 tools/task_board/server.py [--port 8765]
Then open http://127.0.0.1:8765 in a browser.

Tasks save to tools/task_board/tasks.json (commit it like any other file).
Opening index.html directly (file://) also works, but then tasks live only in
that browser's localStorage.

"Send to Claude" (task panel) starts `claude --bg` in the repo root with the
task's title, notes, subtasks and parent chain as the prompt. The session shows
in `claude agents`; `claude attach <id>` opens it. The board polls
`claude agents --json --all` for its state. Permission mode defaults to auto
(--permission-mode to change).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DATA = os.path.join(HERE, "tasks.json")
CLAUDE = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
PERMISSION_MODE = "auto"


def load_tasks():
    with open(DATA, encoding="utf-8") as f:
        return json.load(f)


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
            out += subtree(k["id"], depth + 1)
        return out

    lines = ["Work on this task from the project task board (tools/task_board).", "", f"Task: {t['title']}"]
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
    subs = subtree(t["id"], 0)
    if subs:
        lines += ["", "Subtasks ([x] = already done):", *subs]
    lines += ["", "Follow CLAUDE.md. Commit your work; never push. Do not edit tools/task_board/tasks.json "
              "(the board owns it). End with a short report of what you did and what is left."]
    return "\n".join(lines)


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
    return m.group(1)


def agent_states(ids):
    r = subprocess.run([CLAUDE, "agents", "--json", "--all"], cwd=REPO, capture_output=True, text=True, timeout=30)
    sessions = {s["id"]: s for s in json.loads(r.stdout or "[]")}
    return {i: (sessions[i].get("state") or "unknown") if i in sessions else "gone" for i in ids}


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
        if self.path.startswith("/api/agents?ids="):
            ids = [i for i in self.path.split("=", 1)[1].split(",") if re.fullmatch(r"[0-9a-f]+", i)]
            try:
                self._json(200, agent_states(ids))
            except Exception as e:  # noqa: BLE001 — surface any CLI failure to the board
                self._json(500, {"error": str(e)})
            return
        super().do_GET()

    def do_POST(self):
        if self.path != "/api/agent":
            self._send(404)
            return
        if not self._same_origin():
            self._json(403, {"error": "cross-origin launch refused"})
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self._json(200, {"id": launch(body["taskId"])})
        except LookupError as e:
            self._json(404, {"error": str(e)})
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
    print(f"Task board: http://127.0.0.1:{args.port}  (saving to {DATA})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
