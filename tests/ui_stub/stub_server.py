"""Static stub of the board server: serves a checkout's index.html and fakes the few /api routes the detail panel needs.

Usage: python3 stub_server.py <checkout> <port> [agent_reports/<id>.json]
Shots: shot.mjs (detail panel), shot_list.mjs (List view); needs playwright-core in a temp dir.
"""
import json, sys, os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

ROOT, PORT = sys.argv[1], int(sys.argv[2])
REAL = json.load(open(sys.argv[3]))[:5] if len(sys.argv) > 3 else []
SAMPLE = open(os.path.join(os.path.dirname(__file__), "sample_done.md")).read()
log = REAL + [{"from": "claude", "status": "done", "at": "2026-10-15T12:00:00+00:00", "message": SAMPLE}]
TASK = {"id": "demo1", "title": "Enforce response language rule via hook", "notes": "", "priority": "", "tags": ["feature"],
        "column": None, "parent": None, "order": 0, "created": "2026-10-15T11:00:00+00:00", "updated": "2026-10-15T11:00:00+00:00",
        "agent": {"id": "demo1", "started": "2026-10-15T11:47:00+00:00"}}
BOARD = {"version": 1, "columns": [{"id": "c1", "name": "To Do", "done": False}, {"id": "c2", "name": "In Progress", "done": False, "role": "progress"},
                                   {"id": "c3", "name": "Review", "done": False, "role": "review"}, {"id": "c4", "name": "Done", "done": True}], "tasks": [TASK]}
TASK["column"] = "c2"


class H(SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=ROOT, **k)

    def log_message(self, *a):
        pass

    def send_json(self, obj, extra=None):
        b = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/api/projects":
            return self.send_json([{"id": "demo", "name": "demo", "path": "/tmp/demo", "board": "/tmp/demo/.task_board"}])
        if p == "/api/tasks":
            return self.send_json(BOARD, {"X-Board-Rev": "1"})
        if p == "/api/agents":
            return self.send_json({"demo1": {"id": "demo1", "state": "idle", "phase": "finished", "reason": "", "log": log}})
        if p.startswith("/api/"):
            return self.send_json({})
        return super().do_GET()

    def do_PUT(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        return self.send_json({"ok": True}, {"X-Board-Rev": "1"})

    do_POST = do_PUT


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
