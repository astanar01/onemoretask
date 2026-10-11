"""launch() puts the session on the card in tasks.json itself, so a lost page save cannot leave a running task
reading "Ready to send".

Run: python3 tests/test_launch_agent.py  (stdlib only; a fresh temp HOME, no real claude runs)."""
import json
import os
import subprocess
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="launch-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

proj = os.path.join(TMP, "proj")
os.makedirs(proj)


def git(*args):
    return subprocess.run(["git", "-C", proj, *args], capture_output=True, text=True, check=True).stdout.strip()


git("init", "-q")
git("config", "user.email", "t@example.com")
git("config", "user.name", "Test")
git("config", "commit.gpgsign", "false")
with open(os.path.join(proj, "a.txt"), "w") as f:
    f.write("one\n")
git("add", "a.txt")
git("commit", "-q", "-m", "Initial commit")

p = server.Project(proj)
os.makedirs(p.board)
board = {"columns": [{"id": "todo", "name": "To do"}, {"id": "prog", "name": "In Progress"}],
         "tasks": [{"id": "t1", "title": "Plain task", "column": "prog", "order": 0, "worktree": False},
                   {"id": "t2", "title": "Worktree task", "column": "prog", "order": 1},
                   {"id": "t3", "title": "Apply skill observation #1", "column": "prog", "order": 2,
                    "tags": ["observations"], "worktree": True}]}
with open(p.data, "w", encoding="utf-8") as f:
    json.dump(board, f)

server.run_claude = lambda args, **kw: types.SimpleNamespace(returncode=0, stdout="backgrounded · abc123\n", stderr="")
server.WATCH.expect = lambda *a: None


def on_disk(tid):
    return next(t for t in server.load_tasks(p)["tasks"] if t["id"] == tid)


# ---- (a) a plain send: the card on disk carries the session, and the revs bracket that write
before = server.board_rev(p)
res = server.launch(p, "t1", "opus")
a = on_disk("t1").get("agent")
assert a and a["id"] == "abc123" and a["model"] == "opus" and "worktree" not in a and "cli" not in a, a
assert a["started"].endswith("Z") and a == res["agent"], (a, res)
assert res["revBefore"] == before and res["rev"] == server.board_rev(p), res
assert on_disk("t2").get("agent") is None, "another card got the session"
print("ok  launch writes the agent to tasks.json; revBefore/rev bracket the write")

# ---- (b) a worktree send records the worktree on the card
res = server.launch(p, "t2")
a = on_disk("t2")["agent"]
assert a["worktree"]["branch"] == res["worktree"]["branch"] and os.path.isdir(a["worktree"]["path"]), a
assert "model" not in a, a
print("ok  a worktree send records the worktree on the card")

# ---- (c) set_agent never replaces a card's agent
assert server.set_agent(p, "t1", {"id": "other"}) == {}, "set_agent overwrote an agent"
assert on_disk("t1")["agent"]["id"] == "abc123"
print("ok  set_agent never replaces a card's agent")

# ---- (d) an observations card never gets a worktree, even with the option on
wts = git("worktree", "list")
res = server.launch(p, "t3")
assert res["worktree"] is None and "worktree" not in on_disk("t3")["agent"], res
assert git("worktree", "list") == wts, "an observations card made a worktree"
print("ok  an observations card runs in the project folder")

# ---- (e) the launch log still names the session
assert any(e.get("status") == "launch" and e.get("session") == "abc123" for e in reports.read(p.board, "t1"))
print("all launch-agent tests passed")
