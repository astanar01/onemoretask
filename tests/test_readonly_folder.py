"""A folder the board cannot write to: saving says so instead of "changed in another window", and opening it is refused.

Run: python3 tests/test_readonly_folder.py  (stdlib; a fresh temp HOME). Skipped as root, who can write anywhere."""
import json
import os
import stat
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="readonly-folder-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

if hasattr(os, "geteuid") and os.geteuid() == 0:
    print("SKIP: root ignores folder permissions")
    sys.exit(0)

fails = 0


def check(ok, what, detail=""):
    global fails
    fails += not ok
    print("ok  " if ok else "FAIL", what, "" if ok else detail)


ro = os.path.join(TMP, "readonly")
os.makedirs(os.path.join(ro, "Historique"))
os.chmod(ro, stat.S_IRUSR | stat.S_IXUSR)   # dr-x------, like a folder copied off a read-only disk
rw = os.path.join(TMP, "writable")
os.makedirs(rw)
p_ro, p_rw = server.Project(ro), server.Project(rw)
server.projects = lambda: [p_ro, p_rw]

check(not server.board_writable(p_ro), "read-only folder with no board: not writable")
check(server.board_writable(p_rw), "writable folder with no board: writable")

httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()
host = f"127.0.0.1:{httpd.server_address[1]}"


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(f"http://{host}{path}", json.dumps(body).encode() if body is not None else None,
                                 {"Content-Type": "application/json", **(headers or {})}, method=method)
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else None, r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read()), e.headers


board = {"columns": [{"id": "todo", "title": "To do"}], "tasks": [{"id": "t1", "title": "New", "column": "todo"}]}

# The bug: a new card in a read-only folder came back 409, which the page shows as "changed in another window".
code, r, _ = call("PUT", f"/api/tasks?p={p_ro.id}", board, {"If-Match": "0"})
check(code != 409 and r == {"error": server.READ_ONLY}, "read-only folder: save refused with its own message, not 409",
      (code, r))
check(not os.path.exists(p_ro.board), "read-only folder: nothing written")

# A real conflict still says "changed", which is what the page looks for.
code, _, h = call("PUT", f"/api/tasks?p={p_rw.id}", board, {"If-Match": "0"})
check(code == 204, "writable folder: first save works", code)
code, r, _ = call("PUT", f"/api/tasks?p={p_rw.id}", board, {"If-Match": "0"})
check(code == 409 and r.get("changed") is True, "stale save: 409 with changed", (code, r))
code, _, _ = call("PUT", f"/api/tasks?p={p_rw.id}", board, {"If-Match": h["X-Board-Rev"]})
check(code == 204, "save with the current rev works", code)

# Opening a read-only folder is refused up front, with the same plain message.
code, r, _ = call("POST", "/api/projects", {"path": ro})
check(code == 400 and r == {"error": server.READ_ONLY}, "open read-only folder: refused", (code, r))
check(all(os.path.realpath(e["path"]) != p_ro.path for e in server._read_registry()), "read-only folder: not added")
code, r, _ = call("POST", "/api/projects", {"path": rw})
check(code == 200 and r["id"] == p_rw.id, "open writable folder: added", (code, r))

# A board folder that exists but is read-only: also refused.
ro_board = os.path.join(TMP, "ro-board")
os.makedirs(os.path.join(ro_board, ".task_board"))
os.chmod(os.path.join(ro_board, ".task_board"), stat.S_IRUSR | stat.S_IXUSR)
check(not server.board_writable(server.Project(ro_board)), "read-only board folder: not writable")
httpd.shutdown()

for d in (ro, os.path.join(ro_board, ".task_board")):
    os.chmod(d, stat.S_IRWXU)
print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
