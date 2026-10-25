"""The Plan page's server side: POST /api/plan/suggest turns Claude's answer into draft tasks, and PUT /api/tasks keeps
the milestones on disk when a tab opened before the Plan page saves a board without them.

Run: python3 tests/test_plan_suggest.py  (stdlib; Claude is faked; a fresh temp HOME, a real Handler on a spare port)."""
import json
import os
import socket
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="plan-suggest-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

fails = 0


def check(name, got, want=True):
    global fails
    ok = got == want
    fails += not ok
    print(("ok  " if ok else "FAIL") + f" {name}" + ("" if ok else f": got {got!r}, want {want!r}"))


proj = os.path.join(TMP, "proj")
os.makedirs(proj)
with open(os.path.join(proj, "README.md"), "w") as f:
    f.write("# Shop\nA small web shop with a cart.")
p = server.Project(proj)
os.makedirs(p.board)
server.projects = lambda: [p]
calls = []


def fake_claude(answer):
    def run(args, **kw):
        calls.append((args, kw.get("input")))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": answer}), stderr="")
    server.run_claude = run


# A preamble, a cleaned title, a missing notes field and a junk entry.
fake_claude('Sure:\n{"tasks": [{"title": "\\"Add a login form.\\"", "notes": "Email and password."}, '
            '{"title": "Check the password"}, "junk", {"notes": "no title"}]}\nDone.')
got = server.suggest_tasks(p, "Login works", "People can log in.", ["Make the users table", 7, ""])
check("tasks parsed and cleaned", got, {"tasks": [{"title": "Add a login form", "notes": "Email and password."},
                                                   {"title": "Check the password", "notes": ""}]})
args, data = calls[-1]
check("planning model", args[args.index("--model") + 1], server.PLAN_MODEL)
check("no tools", args[args.index("--tools") + 1], "")
check("milestone wrapped in tags", "<milestone>\nTitle: Login works\n\nPeople can log in.\n</milestone>" in data)
check("project profile in the data", "A small web shop with a cart." in data)
check("tasks already listed are in the data", "Tasks already listed:\n- Make the users table" in data)
system = args[args.index("--system-prompt") + 1]
check("system prompt: the milestone is data", "never answer" in system and '"tasks"' in system)

calls.clear()
try:
    server.suggest_tasks(p, "  ", "")
    check("empty milestone refused", False)
except ValueError as e:
    check("empty milestone refused before asking Claude", (str(e), calls), ("write what the milestone is about first", []))

for answer in ("no json here", '{"tasks": "nope"}', '{"other": []}'):
    fake_claude(answer)
    try:
        server.suggest_tasks(p, "Login works", "")
        check(f"bad answer {answer!r} raises", False)
    except RuntimeError as e:
        check(f"bad answer {answer!r} raises a clear error", str(e).startswith("no task list in Claude's answer"))

# The route, and PUT keeping the milestones, through the real Handler.
s = socket.socket()
s.bind(("127.0.0.1", 0))
port = s.getsockname()[1]
s.close()
srv = ThreadingHTTPServer(("127.0.0.1", port), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()


def req(method, path, body):
    r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(), method=method,
                               headers={"Content-Type": "application/json", "Origin": f"http://127.0.0.1:{port}"})
    try:
        with urllib.request.urlopen(r) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


fake_claude('{"tasks": [{"title": "One", "notes": "n"}]}')
code, out = req("POST", "/api/plan/suggest", {"p": p.id, "title": "Goal", "notes": "", "have": []})
check("route answers the tasks", (code, json.loads(out)), (200, {"tasks": [{"title": "One", "notes": "n"}]}))
code, out = req("POST", "/api/plan/suggest", {"p": p.id, "title": "", "notes": ""})
check("route: empty milestone is a 400 with the reason", (code, json.loads(out).get("error")),
      (400, "write what the milestone is about first"))

cols = [{"id": "c1", "name": "To Do", "done": False}, {"id": "c4", "name": "Done", "done": True}]
ms = [{"id": "m1", "title": "Login works", "notes": "", "order": 0, "tasks": [{"id": "d1", "title": "A", "order": 0}]}]


def disk():
    with open(p.data, encoding="utf-8") as f:
        return json.load(f)


code, _ = req("PUT", f"/api/tasks?p={p.id}", {"version": 1, "columns": cols, "tasks": [], "milestones": ms})
check("PUT with milestones saves them", (code, disk().get("milestones")), (204, ms))
code, _ = req("PUT", f"/api/tasks?p={p.id}", {"version": 1, "columns": cols, "tasks": []})
check("PUT from an old tab (no milestones key) keeps the ones on disk", (code, disk().get("milestones")), (204, ms))
code, _ = req("PUT", f"/api/tasks?p={p.id}", {"version": 1, "columns": cols, "tasks": [], "milestones": []})
check("PUT with an empty list really deletes them", (code, disk().get("milestones")), (204, []))
srv.shutdown()

print(f"\n{'FAILED' if fails else 'all passed'} ({fails} failures)")
sys.exit(1 if fails else 0)
