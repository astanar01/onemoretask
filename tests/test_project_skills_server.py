"""S64 / S73 / S74 (server side): before a Codex or Antigravity process starts, the board refreshes that CLI's rules
and links the project's skills where it looks; the launch line says how many were linked. Never for Claude.

Run: python3 tests/test_project_skills_server.py  (stdlib only; temp HOME, fake codex / agy / install_clis modules)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.realpath(tempfile.mkdtemp(prefix="skills-srv-test-"))
os.environ["HOME"] = os.path.join(TMP, "home")
os.makedirs(os.environ["HOME"])
FAKE = os.path.join(TMP, "fake")
os.makedirs(FAKE)
COMMON = '''
import os, tempfile
SESSIONS, STARTS, RESUMES = {}, [], []
def installed(): return True
def models(): return []
def refreshing(): return False
def models_error(): return ""
def start(board, task_id, name, prompt, model, cwd, effort="", writable=None):
    STARTS.append({"model": model, "cwd": cwd, "writable": writable})
    sid = PREFIX + str(len(STARTS))
    SESSIONS[sid] = {"id": sid, "sessionId": sid, "cli": CLI, "board": board, "cwd": CWD.get(task_id, cwd),
                     "log": "", "err": "", "state": "idle", "detail": "", "task": task_id}
    return sid
CWD = {}
def resume(board, sid, message):
    RESUMES.append(sid)
    return sid
def sessions(board): return [dict(s) for s in SESSIONS.values() if s["board"] == board]
def session(board, sid):
    s = SESSIONS.get(sid)
    return dict(s) if s and s["board"] == board else None
def is_session(board, sid): return session(board, sid) is not None
def remove(board, sid, grace=5): SESSIONS.pop(sid, None)
def is_log(path): return False
def last_answer(log, reply_text=None): return ""
def usage(log): return [0, 0, 0, 0]
def subagents(log): return []
def subagent_usage(log): return [0, 0, 0, 0], 0
def worker_rows(board, task_id, since=0): return []
def worker_usage(board, task_id, since=0): return [0, 0, 0, 0], 0
def context(log): return None
def mcp_uses(log): return []
def digest(log): return ""
def mcp_what(args): return ""
def turn_ended_at(s): return None
'''
with open(os.path.join(FAKE, "codex.py"), "w") as f:
    f.write('PREFIX, CLI = "cdx-", "codex"\n' + COMMON + '''
def is_model(m): return bool(m) and m.startswith(("gpt-", "o1", "o3", "o4", "codex"))
def answer_dir(task_id): return os.path.join(tempfile.gettempdir(), "skills-srv-answer", task_id)
''')
with open(os.path.join(FAKE, "agy.py"), "w") as f:
    f.write('PREFIX, CLI = "ag-", "antigravity"\n' + COMMON + '''
def is_model(m): return bool(m) and m.startswith("agy:")
def slug(m): return m[4:] if is_model(m) else m
def answer_dir(board, task_id): return os.path.join(tempfile.gettempdir(), "skills-srv-answer", "agy-" + task_id)
''')
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, FAKE)
import agy  # noqa: E402
import codex  # noqa: E402
import reports  # noqa: E402
import server  # noqa: E402

CALLS = []
RESULT = [(["alpha", "beta"], [])]
fake_setup = types.SimpleNamespace(
    ensure_rules=lambda cli, home=None, claude=None: CALLS.append(("rules", cli)),
    ensure_project_skills=lambda project, target=None: (CALLS.append(("skills", project, target)), RESULT[0])[1])
server._INSTALL_CLIS[:] = [fake_setup]

proj = os.path.join(TMP, "proj")
os.makedirs(proj)
subprocess.run(["git", "-C", proj, "init", "-q"], check=True)
p = server.Project(proj)
os.makedirs(p.board)
os.makedirs(os.path.dirname(server.REGISTRY), exist_ok=True)
with open(server.REGISTRY, "w") as f:
    json.dump({"projects": [{"path": proj, "opened": "2026-01-01T00:00:00+00:00"}]}, f)
server.run_claude = lambda args, **kw: types.SimpleNamespace(returncode=0, stdout="backgrounded · abc123\n", stderr="")
server.list_sessions = lambda cwd: []
FAILS, RESULTS = [], []


def run(fn):
    try:
        fn()
        print("ok   " + fn.__name__)
    except Exception as e:  # noqa: BLE001
        FAILS.append(fn.__name__)
        print("FAIL " + fn.__name__ + ": " + (str(e) or type(e).__name__)[:400])
        if os.environ.get("VERBOSE"):
            traceback.print_exc()
    RESULTS.append(fn.__name__)


def board(*tasks):
    with open(p.data, "w") as f:
        json.dump({"columns": [{"id": "todo", "name": "To do"}], "tasks": list(tasks)}, f)


def test_s64_s73_start_session_targets():
    board({"id": "t1", "title": "x", "column": "todo"}, {"id": "t2", "title": "y", "column": "todo", "answerOnly": True})
    CALLS.clear()
    server.start_session(p, "t1", "n", "P", "gpt-5.5")
    assert ("rules", "codex") in CALLS and ("skills", p.path, p.path) in CALLS, CALLS
    CALLS.clear()
    server.start_session(p, "t1", "n", "P", "agy:gemini-4-neon-high")
    assert ("rules", "antigravity") in CALLS and ("skills", p.path, p.path) in CALLS, CALLS
    CALLS.clear()
    server.start_session(p, "t2", "n", "P", "gpt-5.5")
    assert ("skills", p.path, codex.answer_dir("t2")) in CALLS and ("skills", p.path, p.path) not in CALLS, CALLS
    CALLS.clear()
    server.start_session(p, "t1", "n", "P", "opus")
    assert CALLS == [], ("a Claude start ran the step", CALLS)


def test_s64_s73_resume_target():
    sid = server.start_session(p, "t2", "n", "P", "gpt-5.5")
    codex.SESSIONS[sid]["cwd"] = "/tmp/answer-folder"
    CALLS.clear()
    server.resume(p, {"id": sid, "sessionId": sid}, "go on")
    assert ("rules", "codex") in CALLS and ("skills", p.path, "/tmp/answer-folder") in CALLS, CALLS
    sid = server.start_session(p, "t1", "n", "P", "agy:gemini-4-neon-high")
    CALLS.clear()
    server.resume(p, {"id": sid, "sessionId": sid}, "go on")
    assert ("rules", "antigravity") in CALLS and ("skills", p.path, p.path) in CALLS, CALLS


def _launch_line(tid, model, **kw):
    board({"id": tid, "title": "Launch " + tid, "column": "todo", "worktree": False, **kw})
    server.launch(p, tid, model)
    return next(e["message"] for e in reports.read(p.board, tid) if e.get("status") == "launch")


def test_s74_launch_line_suffix():
    RESULT[0] = (["alpha", "beta"], [])
    line = _launch_line("l1", "agy:gemini-4-neon-high")
    assert line.endswith(", project skills: 2 linked)"), line
    RESULT[0] = (["alpha"], ["gamma"])
    line = _launch_line("l2", "gpt-5.5")
    assert line.endswith(", project skills: 1 linked, 1 kept as they were (gamma))"), line
    RESULT[0] = (["a"], ["k1", "k2", "k3", "k4"])
    line = _launch_line("l3", "gpt-5.5")
    assert line.endswith(", project skills: 1 linked, 4 kept as they were)"), line
    RESULT[0] = ([], [])
    line = _launch_line("l4", "gpt-5.5")
    assert "project skills" not in line, line
    CALLS.clear()
    RESULT[0] = (["alpha"], [])
    line = _launch_line("l5", "opus")
    assert "project skills" not in line and not [c for c in CALLS if c[0] == "skills"], (line, CALLS)


def test_s74_answer_only_codex_target():
    RESULT[0] = (["alpha"], [])
    CALLS.clear()
    _launch_line("l6", "gpt-5.5", answerOnly=True)
    targets = {c[2] for c in CALLS if c[0] == "skills"}
    assert targets == {codex.answer_dir("l6")}, CALLS


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            run(fn)
    print("\n%d of %d failed: %s" % (len(FAILS), len(RESULTS), ", ".join(FAILS)))
    sys.exit(1 if FAILS else 0)
