"""A card whose sessions a new task already closed, moved to done afterwards, is not stuck on "writing the recap".

Moving a card to done leaves a close marker for the watcher, which removes it after writing the recap. With the recap
saved and no session left there is nothing to close, so the marker stayed and the card read as recapping for good.

Run: python3 tests/test_closed_card_to_done.py  (stdlib only; a fresh temp HOME, every session call faked)."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="closed-to-done-test-")
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
agent = {"id": "a0", "started": ""}
tasks = {"columns": [{"id": "review", "name": "Review"}, {"id": "done", "name": "Done", "done": True}],
         "tasks": [{"id": "closed", "title": "Recap saved, no session", "column": "done", "agent": dict(agent)},
                   {"id": "norecap", "title": "No recap yet, no session", "column": "done", "agent": dict(agent)},
                   {"id": "live", "title": "Recap saved, new session", "column": "done", "agent": dict(agent, id="a1")}]}
with open(p.data, "w", encoding="utf-8") as f:
    json.dump(tasks, f)
reports.append(p.board, "closed", "done", "All done.", "claude")
reports.append(p.board, "closed", "recap", "## Goal\nDo the thing.", "claude")
reports.append(p.board, "norecap", "done", "All done.", "claude")
reports.append(p.board, "live", "done", "All done.", "claude")
reports.append(p.board, "live", "recap", "## Goal\nDo the thing.", "claude")
server.projects = lambda: [p]
server.list_sessions = lambda cwd: [{"id": "a1", "sessionId": "s1", "state": "working", "status": "busy"}]
server.run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))
W = server.WATCH
for tid in ("closed", "norecap", "live"):
    W.close_when_idle(p, tid)  # what PUT /api/tasks does for a card landing in done
W.poll()

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("ok  " if ok else "FAIL", name, "" if ok else f"(got {got!r}, want {want!r})")


def entry(tid):
    return W.cache[(p.id, tid)]


check("closed card: not recapping", entry("closed")["recapping"], False)
check("closed card: archived", entry("closed")["archived"], True)
check("closed card: marker gone", os.path.exists(reports.close_path(p.board, "closed")), False)
check("no recap yet: marker kept", os.path.exists(reports.close_path(p.board, "norecap")), True)
check("a session still running: marker kept", os.path.exists(reports.close_path(p.board, "live")), True)
check("a session still running: recapping", entry("live")["recapping"], True)
print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
