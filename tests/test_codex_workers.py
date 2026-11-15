"""Codex workers of a Claude card session: codex_worker.py, codex.worker_rows / worker_usage, the brief, poll + tokens.

Run: python3 tests/test_codex_workers.py  (stdlib only; a fresh temp HOME and board, a fake `codex` on PATH)."""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="codex-workers-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])

BIN = os.path.join(TMP, "bin")
os.makedirs(BIN)
GOT = os.path.join(TMP, "got.txt")
with open(os.path.join(BIN, "codex"), "w", encoding="utf-8") as f:
    f.write(r'''#!%s
import json, os, sys, time
args = sys.argv[1:]
prompt = sys.stdin.read()
with open(%r, "a", encoding="utf-8") as g:
    g.write(json.dumps({"argv": args, "prompt": prompt, "cwd": os.getcwd()}) + "\n")
def out(e):
    print(json.dumps(e), flush=True)
out({"type": "thread.started", "thread_id": "th-1"})
out({"type": "turn.started"})
if prompt.startswith("FAIL"):
    sys.stderr.write("Reading prompt from stdin...\nerror: model not found\n")
    out({"type": "turn.failed", "error": {"message": "model not found"}})
    sys.exit(1)
if prompt.startswith("SLEEP"):
    out({"type": "item.completed", "item": {"id": "item_0", "type": "command_execution", "command": "echo previous job", "status": "completed"}})
    out({"type": "item.started", "item": {"id": "item_1", "type": "command_execution", "command": "sleep 30", "status": "in_progress"}})
    time.sleep(60)
out({"type": "item.completed", "item": {"id": "item_0", "type": "command_execution", "command": "ls -la\nmore", "status": "completed"}})
out({"type": "item.completed", "item": {"id": "item_1", "type": "file_change", "changes": [{"path": "/x/hello.txt", "kind": "add"}], "status": "completed"}})
out({"type": "item.completed", "item": {"id": "item_2", "type": "agent_message", "text": "DONE: " + prompt.splitlines()[0]}})
out({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 0, "output_tokens": 7}})
''' % (sys.executable, GOT))
os.chmod(os.path.join(BIN, "codex"), 0o755)
ENV = dict(os.environ, PATH=BIN + os.pathsep + os.environ.get("PATH", ""))

proj = os.path.join(TMP, "proj")
os.makedirs(proj)
sys.path.insert(0, ROOT)
import codex  # noqa: E402
import server  # noqa: E402

p = server.Project(proj)
os.makedirs(p.board)
HELPER = os.path.join(ROOT, "codex_worker.py")


def run(task, prompt, *extra):
    return subprocess.run([sys.executable, HELPER, "--board", p.board, task, "--model", "gpt-5.5", "--name", "Hello",
                           *extra], input=prompt, capture_output=True, text=True, cwd=proj, env=ENV, timeout=60)


def calls():
    return [json.loads(x) for x in open(GOT, encoding="utf-8")]


WF = os.path.join(p.board, "agent_reports", "codex-workers")

# ---- the helper: log + err + meta in codex-workers, the answer on stdout
before = time.time()
r = run("t1", "make hello\nmore lines")
assert r.returncode == 0 and r.stdout.strip() == "DONE: make hello", (r.returncode, r.stdout, r.stderr)
c = calls()[-1]
assert c["argv"][:4] == ["exec", "--json", "--skip-git-repo-check", "-s"], c["argv"]
assert c["argv"][4] == "workspace-write" and c["argv"][c["argv"].index("-m") + 1] == "gpt-5.5", c["argv"]
assert os.path.realpath(c["argv"][c["argv"].index("-C") + 1]) == os.path.realpath(proj), c
assert c["argv"][-1] == "-" and c["prompt"] == "make hello\nmore lines", c
names = sorted(os.listdir(WF))
assert len(names) == 3 and {n.rsplit(".", 1)[1] for n in names} == {"json", "jsonl", "err"}, names
assert all(n.startswith("t1-") for n in names), names
meta = json.load(open(os.path.join(WF, [n for n in names if n.endswith(".json")][0]), encoding="utf-8"))
assert meta["task"] == "t1" and meta["name"] == "Hello" and meta["model"] == "gpt-5.5" and meta["exit"] == 0, meta
assert os.path.realpath(meta["cwd"]) == os.path.realpath(proj) and meta["pid"] and meta["started"] >= before - 1 and meta["log"].endswith(".jsonl"), meta
print("ok  helper: codex exec -s workspace-write -m gpt-5.5 -C <cwd> -, log/err/meta in codex-workers, answer printed")

r = run("t1", "look only", "--read-only", "--cwd", TMP)
assert r.returncode == 0, r.stderr
c = calls()[-1]
assert c["argv"][4] == "read-only" and os.path.realpath(c["argv"][c["argv"].index("-C") + 1]) == os.path.realpath(TMP), c["argv"]
print("ok  --read-only gives -s read-only; --cwd sets -C")

# ---- failure: non-zero exit, the end of stderr, exit in the meta
r = run("t1", "FAIL now")
assert r.returncode == 1 and "model not found" in r.stderr and r.stdout.strip() == "", (r.returncode, r.stdout, r.stderr)
metas = [json.load(open(os.path.join(WF, n), encoding="utf-8")) for n in sorted(os.listdir(WF)) if n.endswith(".json")]
assert sorted(m["exit"] for m in metas) == [0, 0, 1], metas
print("ok  failure: exit 1, stderr tail printed, exit 1 in the meta")

r = subprocess.run([sys.executable, HELPER, "--board", p.board, "t1", "--model", "gpt-5.5"], input="  \n",
                   capture_output=True, text=True, env=ENV, timeout=30)
assert r.returncode != 0 and "no prompt" in r.stderr, r
print("ok  an empty prompt is refused")

# ---- not a card session
assert codex.sessions(p.board) == [], codex.sessions(p.board)
assert not any(codex.is_log(m["log"]) for m in metas)
assert not os.listdir(codex.folder(p.board))
print("ok  codex.sessions does not list a worker; is_log is false for its log")

# ---- rows and usage
rows = codex.worker_rows(p.board, "t1")
assert len(rows) == 3, rows
ok_row = next(r for r in rows if r["type"] == "gpt-5.5" and r["finished"] and r["action"] == "edit hello.txt")
assert ok_row["name"] == "Hello" and ok_row["skills"] == [] and ok_row["cli"] == "codex" and ok_row["tokens"] == 107, ok_row
assert ok_row["started"] <= ok_row["updated"], ok_row
bad = [r for r in rows if not r["finished"]]
assert len(bad) == 1 and bad[0]["tokens"] == 0 and bad[0]["action"] == "", bad
assert [r["started"] for r in rows] == sorted(r["started"] for r in rows)
assert codex.worker_usage(p.board, "t1") == ([120, 0, 80, 14], 3), codex.worker_usage(p.board, "t1")
assert codex.worker_rows(p.board, "t2") == [] and codex.worker_usage(p.board, "t2") == ([0, 0, 0, 0], 0)
assert codex.worker_rows(p.board, "t1", time.time() + 5) == []
assert len(codex.worker_rows(p.board, "t1", rows[1]["started"])) == 2
print("ok  worker_rows: 3 rows (finished + action + 107 tokens, the failed one unfinished), usage [120, 0, 80, 14], since")

# ---- stopped vs running, and the job running now
live = subprocess.Popen([sys.executable, HELPER, "--board", p.board, "t3", "--model", "gpt-5.5", "--name", "Slow"],
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=proj,
                        env=ENV)
live.stdin.write("SLEEP please")
live.stdin.close()
deadline, lmeta = time.time() + 20, None
while time.time() < deadline:
    found = [n for n in os.listdir(WF) if n.startswith("t3-") and n.endswith(".json")]
    if found:
        lmeta = json.load(open(os.path.join(WF, found[0])))
        if "sleep 30" in open(lmeta["log"]).read():
            break
    time.sleep(0.1)
assert lmeta and live.poll() is None, (lmeta, live.poll())
running, live_start = codex.worker_rows(p.board, "t3")[0], codex._pid_start(lmeta["pid"])
gone = subprocess.Popen([sys.executable, "-c", "pass"])
gone.wait()  # a real pid whose process is gone
with open(os.path.join(WF, "t4-1.jsonl"), "w") as f:
    f.write("")
with open(os.path.join(WF, "t4-1.json"), "w") as f:
    json.dump({"task": "t4", "name": "Killed", "model": "gpt-5.5", "log": os.path.join(WF, "t4-1.jsonl"), "started": 1,
               "pid": gone.pid, "exit": None}, f)
dead = codex.worker_rows(p.board, "t4")[0]
got = {"failed exit=1": (bad[0].get("stopped"), bad[0]["finished"]), "ok exit=0": ok_row.get("stopped"),
       "running": (running.get("stopped"), running["finished"]), "now": running["action"],
       "no exit, dead pid": (dead.get("stopped"), dead["finished"])}
want = {"failed exit=1": (True, False), "ok exit=0": False, "running": (False, False), "now": "sleep 30",
        "no exit, dead pid": (True, False)}
try:
    assert got == want, got
finally:
    live.terminate()  # as when the lead stops its background task
    try:
        live.wait(timeout=20)
    except subprocess.TimeoutExpired:
        live.kill()
        live.wait()
        raise AssertionError("codex_worker.py hung on SIGTERM")
assert live.returncode == 143, live.returncode
assert set(ok_row) == {"id", "name", "type", "started", "updated", "finished", "stopped", "action", "skills", "tokens",
                       "cli"}, ok_row
assert lmeta["pid_start"] and lmeta["pid_start"] == live_start or not codex.POSIX, (lmeta, live_start)
killed = codex.worker_rows(p.board, "t3")[0]
assert killed["stopped"] and not killed["finished"] and killed["action"] == "sleep 30", killed
print("ok  stopped: exit 1 or a gone pid -> stopped; a running worker is not; Now shows the started command")

# ---- the brief
W_CMD = server.script_cmd(p, "t1", "codex_worker.py")
assert "codex_worker.py" in W_CMD and p.board in W_CMD and W_CMD.endswith(" t1"), W_CMD
cl = "\n".join(server.delegate_lines("gpt-5.5", "R", False, False, W_CMD))
assert f"`{W_CMD} --model gpt-5.5 --name \"<short label>\" <<'EOF'`" in cl, cl
assert "run_in_background: true" in cl and "background-task notification" in cl and "SKILL.md" in cl, cl
assert "Agent tool" in cl and "spawn_agent" not in cl and "Skill('" not in cl and "--read-only" not in cl, cl
ao = "\n".join(server.delegate_lines("gpt-5.5", "R", True, False, W_CMD))
assert "--read-only <<'EOF'" in ao, ao
cs = "\n".join(server.delegate_lines("sonnet", "R", False, False, W_CMD))
assert 'model: "sonnet"' in cs and "codex_worker" not in cs and "Skill('" in cs, cs
cx = "\n".join(server.delegate_lines("gpt-5.5", "R", False, True, W_CMD))
assert "spawn_agent" in cx and "codex_worker" not in cx and 'model: "gpt-5.5"' in cx, cx
rem = server.delegate_reminder({"delegate": True, "subagentModel": "gpt-5.5", "agent": {"model": "opus"}}, W_CMD)
assert f"`{W_CMD} --model gpt-5.5" in rem and "run_in_background" in rem and "SKILL.md" in rem, rem
rem = server.delegate_reminder({"delegate": True, "subagentModel": "gpt-5.5", "answerOnly": True,
                                "agent": {"model": "opus"}}, W_CMD)
assert "--read-only <<'EOF'" in rem, rem
rem = server.delegate_reminder({"delegate": True, "subagentModel": "gpt-5.5", "agent": {"cli": "codex"}}, W_CMD)
assert "codex_worker" not in rem and "spawn_agent" in rem, rem
assert server.pinned_worker({"delegate": True, "subagentModel": "gpt-5.5"}, "opus") == ""
print("ok  brief: Claude session + Codex pick -> codex_worker.py in background Bash; other two cases unchanged; no pin")

with open(p.data, "w", encoding="utf-8") as f:
    json.dump({"columns": [{"id": "todo", "name": "To do"}], "tasks": [
        {"id": "t1", "title": "Split it", "column": "todo", "order": 0, "delegate": True, "subagentModel": "gpt-5.5",
         "agent": {"id": "a1", "started": "2000-01-01T00:00:00.000Z", "model": "opus"}}]}, f)
state = server.load_tasks(p)
assert f"`{W_CMD} --model gpt-5.5" in server.build_prompt(p, state, state["tasks"][0], None, "opus")
assert f"`{W_CMD} --model gpt-5.5" in server.board_tail(p, "t1")
print("ok  build_prompt and board_tail carry the helper command with this board and task")

# ---- tokens and the watcher's poll for a Claude session
tk = server.tokens([], (p.board, "t1"))
assert tk == {"main": [0] * 4, "subagents": [120, 0, 80, 14], "count": 3}, tk
assert server.tokens([]) == {"main": [0] * 4, "subagents": [0] * 4, "count": 0}
os.makedirs(server.PROJECTS_DIR, exist_ok=True)  # Claude Code's transcript folder, there on a real machine
server.projects = lambda: [p]
server.list_sessions = lambda cwd: [{"id": "a1", "sessionId": "s1", "state": "working", "status": "busy"}]
server.run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))
server.WATCH.poll()
card = server.WATCH.snapshot(p.id)["t1"]
got = [r for r in card["subagents"] if r.get("cli") == "codex"]
assert len(got) == 3 and any(r["action"] == "edit hello.txt" for r in got), card["subagents"]
assert card["tokens"]["subagents"] == [120, 0, 80, 14] and card["tokens"]["count"] == 3, card["tokens"]
print("ok  poll: a Claude card lists its Codex workers as subagent rows and counts their tokens")
print("PASS")
