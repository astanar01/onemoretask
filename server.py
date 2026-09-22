#!/usr/bin/env python3
"""Task board: a local kanban with subtasks.

Run from anywhere:  python3 tools/task_board/server.py [--port 8765]
Then open http://127.0.0.1:8765 in a browser.

Tasks save to tools/task_board/tasks.json (commit it like any other file).
Opening index.html directly (file://) also works, but then tasks live only in
that browser's localStorage.
"""
import argparse
import json
import os
import tempfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "tasks.json")


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

    def do_GET(self):
        if self.path == "/api/tasks":
            if os.path.exists(DATA):
                with open(DATA, "rb") as f:
                    self._send(200, f.read())
            else:
                self._send(200, b"null")
            return
        super().do_GET()

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
        if "/api/tasks" not in self.path:
            super().log_message(fmt, *args)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Task board: http://127.0.0.1:{args.port}  (saving to {DATA})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
