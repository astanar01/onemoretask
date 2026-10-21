"""A send can pick an effort level: claude gets --effort, codex gets -c model_reasoning_effort, the card keeps it.

Run: python3 tests/test_effort.py  (stdlib only; a fresh temp HOME, a fake codex binary, no real CLI runs)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="effort-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import codex  # noqa: E402
import reports  # noqa: E402
import server  # noqa: E402

# A fake codex binary that records its argv and names a thread.
GOT = os.path.join(TMP, "got.txt")
FAKE = os.path.join(TMP, "fake_codex.py")
with open(FAKE, "w", encoding="utf-8") as f:
    f.write(r'''#!/usr/bin/env python3
import json, sys, uuid
args = sys.argv[1:]
sys.stdin.read()
with open(%r, "a", encoding="utf-8") as g:
    g.write(json.dumps(args) + "\n")
tid = [a for a in args if a.count("-") == 4][0] if "resume" in args else str(uuid.uuid4())
print(json.dumps({"type": "thread.started", "thread_id": tid}), flush=True)
print(json.dumps({"type": "turn.completed", "usage": {}}), flush=True)
''' % GOT)
codex.CODEX_CMD = [sys.executable, FAKE]


def codex_argv():
    with open(GOT, encoding="utf-8") as f:
        return json.loads(f.readlines()[-1])


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
board = {"columns": [{"id": "prog", "name": "In Progress"}],
         "tasks": [{"id": f"t{i}", "title": f"Task {i}", "column": "prog", "order": i, "worktree": False}
                   for i in range(1, 6)]}
with open(p.data, "w", encoding="utf-8") as f:
    json.dump(board, f)

calls = []


def fake_run_claude(args, **kw):
    calls.append(args)
    return types.SimpleNamespace(returncode=0, stdout="backgrounded · abc123\n", stderr="")


server.run_claude = fake_run_claude
server.WATCH.expect = lambda *a: None


def on_disk(tid):
    return next(t for t in server.load_tasks(p)["tasks"] if t["id"] == tid)


# ---- (a) claude: --effort high next to --model, before --settings; the card and the launch line keep it
res = server.launch(p, "t1", "opus", "high")
argv = calls[-1]
i = argv.index("--effort")
assert argv[i + 1] == "high" and argv[i - 2:i] == ["--model", "opus"] and argv[i + 2] == "--settings", argv
a = on_disk("t1")["agent"]
assert a["effort"] == "high" and a["model"] == "opus" and a == res["agent"], a
line = next(e["message"] for e in reports.read(p.board, "t1") if e.get("status") == "launch")
assert line == "Sent to Claude (session abc123, model opus, effort high)", line
print("ok  claude: --effort high in the argv; agent.effort saved; launch line names it")

# ---- (b) no effort: no --effort, no agent.effort, launch line unchanged
server.launch(p, "t2", "opus")
assert "--effort" not in calls[-1], calls[-1]
assert "effort" not in on_disk("t2")["agent"], on_disk("t2")["agent"]
line = next(e["message"] for e in reports.read(p.board, "t2") if e.get("status") == "launch")
assert line == "Sent to Claude (session abc123, model opus)", line
print("ok  empty effort: no --effort flag and no effort on the card")

# ---- (c) a bad level is refused like a bad model: ValueError, nothing started, card unlocked
n = len(calls)
for bad in ("extreme", "HIGH", "--model"):
    try:
        server.launch(p, "t3", "opus", bad)
        raise AssertionError(f"effort {bad!r} was accepted")
    except ValueError as e:
        assert "bad effort level" in str(e), e
assert len(calls) == n and "agent" not in on_disk("t3"), calls[n:]
assert server.effort_args("") == [] and all(server.effort_args(x) == ["--effort", x] for x in server.EFFORTS)
print("ok  bad effort level: ValueError, no session started")

# ---- (d) codex: -c model_reasoning_effort="xhigh" in exec argv, stored in its meta, reused on resume
res = server.launch(p, "t4", "gpt-5.5", "xhigh")
argv = codex_argv()
i = argv.index("-c")
assert argv[:2] == ["exec", "--json"] and argv[i + 1] == 'model_reasoning_effort="xhigh"', argv
assert argv[argv.index("-m") + 1] == "gpt-5.5", argv
a = on_disk("t4")["agent"]
assert a["effort"] == "xhigh" and a["cli"] == "codex", a
tid = res["id"]
with open(os.path.join(codex.folder(p.board), tid + ".json"), encoding="utf-8") as f:
    assert json.load(f)["effort"] == "xhigh"
for _ in range(100):
    if codex.session(p.board, tid)["state"] != "working":
        break
    time.sleep(0.1)
codex.resume(p.board, tid, "more")
argv = codex_argv()
assert argv[:2] == ["exec", "resume"] and 'model_reasoning_effort="xhigh"' in argv, argv
print("ok  codex: -c model_reasoning_effort=\"xhigh\" on exec and on resume; meta and card keep it")

# ---- (e) codex without effort: no -c
server.launch(p, "t5", "gpt-5.5")
assert "-c" not in codex_argv(), codex_argv()
print("ok  codex without effort: no -c")
print("all effort tests passed")
