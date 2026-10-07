"""The Codex backend: start, resume, state, answer, tokens and digest, against a fake `codex` binary.

Run: python3 tests/test_codex.py  (stdlib only; a fresh temp HOME and board)."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="codex-test-")
os.environ["HOME"] = os.path.join(TMP, "home")
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import codex  # noqa: E402

GOT = os.path.join(TMP, "got.txt")
FAKE = os.path.join(TMP, "fake_codex.py")
with open(FAKE, "w", encoding="utf-8") as f:
    f.write(r'''#!/usr/bin/env python3
import json, sys, time, uuid
args = sys.argv[1:]
tid = None
prompt = sys.stdin.read()
with open(%r, "a", encoding="utf-8") as g:
    g.write(json.dumps({"argv": args, "prompt": prompt}) + "\n")
def out(e):
    print(json.dumps(e), flush=True)
if prompt.startswith("NOSTART"):
    sys.stderr.write("error: bad auth\n")
    sys.exit(3)
if "resume" in args:
    tid = [a for a in args if a.count("-") == 4][0]
out({"type": "thread.started", "thread_id": tid or str(uuid.uuid4())})
out({"type": "turn.started"})
if prompt.startswith("SLEEP"):
    time.sleep(3)
if prompt.startswith("FAIL"):
    out({"type": "turn.failed", "error": {"message": "boom"}})
    sys.exit(1)
out({"type": "item.completed", "item": {"id": "item_0", "type": "command_execution", "command": "ls -la\nmore", "status": "completed", "aggregated_output": "x"}})
out({"type": "item.completed", "item": {"id": "item_1", "type": "agent_message", "text": "ECHO: " + prompt.splitlines()[0]}})
out({"type": "turn.completed", "usage": {"input_tokens": 100, "cached_input_tokens": 40, "cache_write_input_tokens": 0, "output_tokens": 7}})
''' % GOT)
os.chmod(FAKE, 0o755)
codex.CODEX_CMD = [sys.executable, FAKE]

BOARD = os.path.join(TMP, "proj", ".task_board")
CWD = os.path.join(TMP, "proj")
os.makedirs(BOARD)


def wait_idle(tid, limit=15):
    end = time.time() + limit
    while time.time() < end:
        s = codex.session(BOARD, tid)
        if s["state"] != "working":
            return s
        time.sleep(0.1)
    raise AssertionError("still working after %ss" % limit)


# --- start: thread id, meta, a 60 KB prompt arrives whole, busy while it runs
big = "SLEEP first line\n" + "x" * 60_000
tid = codex.start(BOARD, "t1", "first", big, "gpt-5.5", CWD)
meta = json.load(open(os.path.join(codex.folder(BOARD), tid + ".json"), encoding="utf-8"))
assert meta["id"] == tid and meta["task"] == "t1" and meta["turns"] == 1 and meta["exit"] is None, meta
assert meta["log"].endswith(".jsonl") and os.path.basename(meta["log"]).startswith("t1-"), meta
assert codex.is_log(meta["log"]) and not codex.is_log(os.path.join(TMP, "x.jsonl"))
s = codex.session(BOARD, tid)
assert s["status"] == "busy" and s["pid"] == meta["pid"] and s["state"] == "working" and s["cli"] == "codex", s
assert codex.turn_ended_at(s) is None and codex.turn_ended_at(meta) is None
print("ok  start returns the thread id, writes the meta, busy with a pid while it runs")

try:
    codex.resume(BOARD, tid, "too early")
    raise AssertionError("resume while busy did not raise")
except PermissionError:
    pass
print("ok  a reply while it works raises PermissionError")

s = wait_idle(tid)
assert "status" not in s and "pid" not in s and s["state"] == "idle" and s["detail"] == "", s
assert codex.turn_ended_at(s) is not None
first = json.loads(open(GOT, encoding="utf-8").readline())
assert first["prompt"] == big, len(first["prompt"])
assert first["argv"][:2] == ["exec", "--json"] and "-C" in first["argv"] and first["argv"][-1] == "-", first["argv"]
assert codex.last_answer(meta["log"], "") == "ECHO: SLEEP first line"
print("ok  idle after the turn, no status; the 60 KB prompt arrived whole")

# --- resume: same log, last turn only, usage of both turns, digest
assert codex.resume(BOARD, tid, "second message\nmore") == tid
wait_idle(tid)
meta = json.load(open(os.path.join(codex.folder(BOARD), tid + ".json"), encoding="utf-8"))
assert meta["turns"] == 2 and meta["exit"] == 0, meta
argv = json.loads(open(GOT, encoding="utf-8").readlines()[-1])["argv"]
assert argv[:2] == ["exec", "resume"] and tid in argv and "-C" not in argv, argv
assert codex.last_answer(meta["log"], "") == "ECHO: second message"
assert codex.usage(meta["log"]) == [120, 0, 80, 14], codex.usage(meta["log"])
d = codex.digest(meta["log"])
assert "TURN 1" in d and "TURN 2" in d and "CODEX: ECHO: second message" in d and "  · ls -la" in d, d
print("ok  resume appends to the same log; last answer, usage [120, 0, 80, 14] and digest")

# --- a failed turn: blocked, detail from turn.failed
codex.resume(BOARD, tid, "FAIL please")
s = wait_idle(tid)
assert s["state"] == "blocked" and s["detail"] == "boom", s
assert codex.last_answer(meta["log"], "") == ""
print("ok  a failed turn is blocked with detail 'boom'")

# --- a restart: no Popen known, pid dead, exit unknown -> idle
tid2 = codex.start(BOARD, "t2", "second", "hello", "", CWD)
wait_idle(tid2)
m2 = json.load(open(os.path.join(codex.folder(BOARD), tid2 + ".json"), encoding="utf-8"))
m2["exit"] = None
codex._write_meta(BOARD, m2)
codex.PROCS.clear()
assert codex.session(BOARD, tid2)["state"] == "idle"
assert json.load(open(os.path.join(codex.folder(BOARD), tid2 + ".json"), encoding="utf-8"))["exit"] == 0
print("ok  after a board restart a dead pid reads idle")

# --- remove hides it
assert {x["id"] for x in codex.sessions(BOARD)} == {tid, tid2}
codex.remove(BOARD, tid)
assert [x["id"] for x in codex.sessions(BOARD)] == [tid2] and not codex.is_session(BOARD, tid)
assert codex.session(BOARD, tid) is None and codex.is_session(BOARD, tid2)
assert os.path.exists(meta["log"])
tid3 = codex.start(BOARD, "t3", "third", "SLEEP again", "", CWD)
codex.remove(BOARD, tid3)  # kills a running turn
assert codex.session(BOARD, tid3) is None and tid3 not in codex.PROCS
print("ok  remove hides the session (and stops a running one), the log stays")

# --- start that fails before thread.started
try:
    codex.start(BOARD, "t4", "bad", "NOSTART", "", CWD)
    raise AssertionError("start did not raise")
except RuntimeError as e:
    assert "bad auth" in str(e), e
print("ok  a start that exits early raises RuntimeError with the stderr")

# --- models
assert codex.is_model("gpt-5.5") and codex.is_model("O3-mini") and codex.is_model("codex-auto")
assert not codex.is_model("opus") and not codex.is_model("") and not codex.is_model(None)
assert codex.models() == codex.FALLBACK_MODELS
os.makedirs(os.path.join(os.environ["HOME"], ".codex"))
with open(os.path.join(os.environ["HOME"], ".codex", "models_cache.json"), "w", encoding="utf-8") as f:
    json.dump({"models": [{"slug": "gpt-b", "display_name": "B", "visibility": "list"},
                          {"slug": "gpt-h", "display_name": "H", "visibility": "hide"},
                          {"slug": "gpt-a", "display_name": "A", "visibility": "list"}]}, f)
assert codex.models() == [{"id": "gpt-b", "name": "B"}, {"id": "gpt-a", "name": "A"}], codex.models()
print("ok  is_model and models (fallback, then the cache file)")

# --- two threads writing one meta at once: no clash on the temp file
import subprocess  # noqa: E402
import threading  # noqa: E402

errors = []


def hammer(n):
    for i in range(300):
        try:
            codex._write_meta(BOARD, {"id": "race", "writer": n, "i": i})
        except Exception as e:  # noqa: BLE001
            errors.append(e)


threads = [threading.Thread(target=hammer, args=(n,)) for n in (1, 2)]
for t in threads:
    t.start()
for t in threads:
    t.join()
assert not errors, errors[:3]
assert json.load(open(os.path.join(codex.folder(BOARD), "race.json"), encoding="utf-8"))["i"] == 299
assert not [n for n in os.listdir(codex.folder(BOARD)) if n.endswith(".tmp")]
print("ok  two threads x 300 meta writes: no error, valid JSON, no temp left")


# --- after a restart a pid is only trusted with its start time
def sleeper_meta(tid, started):
    p = subprocess.Popen(["sleep", "60"], start_new_session=True)
    threading.Thread(target=p.wait, daemon=True).start()  # reap it so a killed sleep is gone, not a zombie
    m = dict(m2, id=tid, pid=p.pid, pid_start=started(p.pid), exit=None, removed=False)
    codex._write_meta(BOARD, m)
    return p


right = sleeper_meta("pid-right", codex._pid_start)
wrong = sleeper_meta("pid-wrong", lambda pid: "Thu Jan  1 00:00:00 1970")
try:
    assert codex.PROCS.get("pid-right") is None and codex._pid_start(right.pid)
    assert codex.session(BOARD, "pid-right")["state"] == "working"
    codex.remove(BOARD, "pid-right")
    time.sleep(0.3)
    assert right.poll() is not None, "remove left the right pid running"
    s = codex.session(BOARD, "pid-wrong")
    assert s["state"] == "idle", s
    assert json.load(open(os.path.join(codex.folder(BOARD), "pid-wrong.json"), encoding="utf-8"))["exit"] == 0
    m = json.load(open(os.path.join(codex.folder(BOARD), "pid-wrong.json"), encoding="utf-8"))
    m["exit"] = None  # remove() must not signal it even while the meta still says the turn runs
    codex._write_meta(BOARD, m)
    codex.remove(BOARD, "pid-wrong")
    time.sleep(0.3)
    assert wrong.poll() is None, "remove killed a pid that another process reused"
finally:
    for p in (right, wrong):
        if p.poll() is None:
            p.kill()
print("ok  a saved pid with its start time is working and removed; a reused pid reads idle and is left alone")

# --- workers (spawn_agent / wait): rows and tokens from the event log and each worker's rollout
import shutil  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "codex")
RAMA, AQUI = "01a116f3-9e5c-7aa1-9a07-c7eb4c4d7a65", "01a116f3-9f2d-7513-b971-34c6d235dbd4"
RUN = os.path.join(codex.folder(BOARD), "w1-1.jsonl")
shutil.copy(os.path.join(FIX, "run1.jsonl"), RUN)

os.environ["CODEX_HOME"] = os.path.join(TMP, "codex-home-empty")  # no rollouts: only the event log
rows = codex.subagents(RUN)
assert [r["id"] for r in rows] == [RAMA, AQUI], rows
assert all(r["finished"] and r["tokens"] == 0 and r["name"] == "Worker" and r["type"] == "codex" and r["skills"] == []
           for r in rows), rows
assert codex.subagent_usage(RUN) == ([0, 0, 0, 0], 2), codex.subagent_usage(RUN)
print("ok  no rollouts: 2 worker rows from the event log, finished by the wait items, 0 tokens")

HOME_C = os.path.join(TMP, "codex-home")
day = os.path.join(HOME_C, "sessions", "2026", "10", "07")
os.makedirs(day)
for n in os.listdir(FIX):
    if n.startswith("rollout-"):
        shutil.copy(os.path.join(FIX, n), day)
os.environ["CODEX_HOME"] = HOME_C
rows = {r["id"]: r for r in codex.subagents(RUN)}
assert set(rows) == {RAMA, AQUI}, rows
assert rows[RAMA]["name"] == "Ramanujan" and rows[AQUI]["name"] == "Aquinas", rows
assert rows[RAMA]["tokens"] == 114147 and rows[AQUI]["tokens"] == 114407, rows
assert all(r["finished"] and r["action"] and r["type"] == "gpt-5.6-luna" and r["skills"] == [] for r in rows.values()), rows
assert rows[RAMA]["started"] < rows[RAMA]["updated"], rows[RAMA]
assert [r["id"] for r in codex.subagents(RUN)] == sorted(rows, key=lambda k: rows[k]["started"])
used, count = codex.subagent_usage(RUN)
assert count == 2 and used == [16117 + 13111, 0, 97280 + 100352, 750 + 944], used
print("ok  with rollouts: Ramanujan / Aquinas, model, action, tokens 114147 + 114407, usage split", used)

d = codex.digest(RUN)
assert "  · spawn worker: Create hello.txt" in d and "  · spawn worker: Create world.txt" in d, d
assert d.count("  · wait for workers") == 2, d
print("ok  digest names each spawn and wait")

import server  # noqa: E402

tk = server.tokens([RUN])
assert tk["count"] == 2 and tk["subagents"] == used and tk["main"] == codex.usage(RUN), tk
print("ok  server.tokens([codex log]) counts the 2 workers and their tokens")
print("PASS")
