"""A card moved to done while a watcher pass is running still gets its recap.

The pass reads tasks.json first, then works card by card. A move to done that lands in between used to look like
"not in done" to that pass, which deleted the fresh close marker, so the recap never ran and nothing said why.

Run: python3 tests/test_done_race.py  (stdlib only; a fresh temp HOME, every session call faked)."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="done-race-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

proj = os.path.join(TMP, "proj")
os.makedirs(proj)
p = server.Project(proj)
os.makedirs(p.board)
tasks = {"columns": [{"id": "review", "name": "Review"}, {"id": "done", "name": "Done", "done": True}],
         "tasks": [{"id": "t1", "title": "Finished task", "column": "review", "agent": {"id": "a0", "started": ""}}]}


def save_tasks():
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump(tasks, f)


save_tasks()
reports.append(p.board, "t1", "done", "All done.", "claude")
server.projects = lambda: [p]
server.list_sessions = lambda cwd: [{"id": "a0", "sessionId": "s0", "state": "done", "status": "idle"}]
server.run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))
server.notify = lambda *a: None
closed = []
server.archive = lambda p_, task_id: closed.append(task_id)
W = server.WATCH
marker = reports.close_path(p.board, "t1")


def wait_close():
    for _ in range(200):
        if not W.closing:
            return
        time.sleep(0.01)
    raise SystemExit("FAIL a close thread hung")


W.poll()  # primes the watcher, like the first pass after a server start
real_load = server.load_tasks
moved = []


def load_then_move(p_):
    """Return the state as read, then let the user's move to done land before the pass reaches the card."""
    state = real_load(p_)
    if not moved:
        moved.append(1)
        time.sleep(0.02)  # the move happens strictly after the read
        tasks["tasks"][0]["column"] = "done"
        save_tasks()
        W.close_when_idle(p_, "t1")  # what PUT /api/tasks does for a card landing in done
    return state


server.load_tasks = load_then_move
W.poll()
server.load_tasks = real_load
fails = 0
ok = os.path.exists(marker)
fails += not ok
print("ok  " if ok else "FAIL", "a pass that read the board before the move keeps the close marker")
W.poll()
wait_close()
ok = closed == ["t1"]
fails += not ok
print("ok  " if ok else "FAIL", "the next pass writes the recap", "" if ok else f"(archive calls: {closed})")

# A card moved back out of done still drops its marker (the old behaviour, kept).
tasks["tasks"][0]["column"] = "review"
save_tasks()
open(marker, "w").close()
os.utime(marker, (time.time() - 60, time.time() - 60))
W.poll()
ok = not os.path.exists(marker)
fails += not ok
print("ok  " if ok else "FAIL", "a card no longer in done loses an old marker")
print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
