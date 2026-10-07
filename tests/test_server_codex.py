"""The board drives a Codex CLI session like a Claude one: start, watch, reply, review, tokens, model list.

Run: python3 tests/test_server_codex.py  (stdlib only; a fresh temp HOME, a fake codex module, no real CLI runs)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="codex-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.makedirs(os.environ["HOME"])

# A fake codex module, first on sys.path so server imports it instead of the real one.
FAKE = os.path.join(TMP, "fake")
os.makedirs(FAKE)
with open(os.path.join(FAKE, "codex.py"), "w", encoding="utf-8") as f:
    f.write('''
import os
SESSIONS, STARTS, RESUMES, REMOVED = {}, [], [], []
ANSWER = [""]
MODELS = [{"id": "gpt-5.5", "name": "GPT-5.5"}]

def is_model(model):
    return bool(model) and model.startswith(("gpt-", "o1", "o3", "o4", "codex"))

def models():
    return MODELS

def log_dir(board):
    return os.path.join(board, "agent_reports", "codex")

def start(board, task_id, name, prompt, model, cwd):
    STARTS.append({"board": board, "task": task_id, "name": name, "prompt": prompt, "model": model, "cwd": cwd})
    return "cdx-%d" % len(STARTS)

def resume(board, thread_id, message):
    s = SESSIONS.get(thread_id)
    if s and "pid" in s:
        raise PermissionError("still running")
    RESUMES.append((thread_id, message))
    return thread_id

def sessions(board):
    return [dict(s) for s in SESSIONS.values() if s.get("board") == board]

def session(board, thread_id):
    s = SESSIONS.get(thread_id)
    return dict(s) if s and s.get("board") == board else None

def is_session(board, thread_id):
    return session(board, thread_id) is not None

def remove(board, thread_id):
    REMOVED.append(thread_id)
    SESSIONS.pop(thread_id, None)

def last_answer(log_path, reply_text):
    return ANSWER[0]

def usage(log_path):
    return [1, 2, 3, 4]

def digest(log_path):
    return "CODEX DIGEST"

def is_log(path):
    return "%sagent_reports%scodex%s" % (os.sep, os.sep, os.sep) in path

def turn_ended_at(session):
    return None

SUBS, SUB_USAGE = [[]], [([0, 0, 0, 0], 0)]

def subagents(log_path):
    return SUBS[0]

def subagent_usage(log_path):
    return SUB_USAGE[0]
''')
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, FAKE)
import codex  # noqa: E402
import reports  # noqa: E402
import server  # noqa: E402

assert server.codex is codex, "server did not pick up the fake codex module"

# ---- a project with a git repo, registered the way open_project does it
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
os.makedirs(os.path.dirname(server.REGISTRY), exist_ok=True)
with open(server.REGISTRY, "w", encoding="utf-8") as f:
    json.dump({"projects": [{"path": proj, "opened": "2026-01-01T00:00:00+00:00"}]}, f)
assert [x.id for x in server.projects()] == [p.id], server.projects()

now_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
tasks = {"columns": [{"id": "todo", "name": "To do"}, {"id": "review", "name": "Review"},
                     {"id": "done", "name": "Done", "done": True}],
         "tasks": [{"id": "t1", "title": "Codex task", "column": "todo", "order": 0,
                    "agent": {"id": "cdx-1", "started": now_iso, "model": "gpt-5.5", "cli": "codex"}}]}
with open(p.data, "w", encoding="utf-8") as f:
    json.dump(tasks, f)

claude_calls, alerts = [], []


def fake_run_claude(args, **kw):
    claude_calls.append(args)
    if args and args[0] == "--bg":
        return types.SimpleNamespace(returncode=0, stdout="backgrounded · abc123\n", stderr="")
    raise RuntimeError("no real claude in this test: %r" % (args[:3],))


server.run_claude = fake_run_claude
server.list_sessions = lambda cwd: []
server.notify = lambda title, sub, msg: alerts.append((title, msg))

# ---- (a) start_session picks the CLI by model
aid = server.start_session(p, "t1", "task: Codex task", "THE BRIEF", "gpt-5.5")
assert aid == "cdx-1", aid
st = codex.STARTS[-1]
assert st["prompt"].startswith("THE BRIEF") and st["prompt"].endswith(server.codex_note(p)), st["prompt"][-200:]
assert st["model"] == "gpt-5.5" and st["cwd"] == p.path and st["board"] == p.board, st
assert not claude_calls, "a Codex start ran claude"
aid = server.start_session(p, "t1", "task: Codex task", "THE BRIEF", "opus")
assert aid == "abc123" and claude_calls and claude_calls[-1][0] == "--bg" and "opus" in claude_calls[-1], claude_calls
assert len(codex.STARTS) == 1, "a Claude start went to codex"
print("ok  start_session: gpt-5.5 -> codex.start with the Codex note at the end; opus -> claude --bg")

# ---- (b) the watcher follows the Codex session
LOG = os.path.join(p.board, "agent_reports", "codex", "cdx-1.jsonl")
os.makedirs(os.path.dirname(LOG), exist_ok=True)
open(LOG, "w").close()
codex.SESSIONS["cdx-1"] = {"id": "cdx-1", "sessionId": "cdx-1", "cli": "codex", "board": p.board, "name": "task",
                           "model": "gpt-5.5", "cwd": p.path, "log": LOG, "err": "", "task": "t1",
                           "state": "working", "detail": "", "status": "busy", "pid": 4242}
reports.append(p.board, "t1", "launch", "Sent to Codex (session cdx-1, model gpt-5.5)", "you", session="cdx-1")
W = server.WATCH


def card():
    return W.snapshot(p.id)["t1"]


def poll():
    W.poll()
    for _ in range(300):
        if not W.delivering:
            break
        time.sleep(0.01)
    assert not W.delivering, "a delivery thread hung"


poll()
assert card()["phase"] == "working" and card()["cli"] == "codex", card()
assert card()["tokens"]["main"] == [1, 2, 3, 4] and card()["subagents"] == [] and card()["cache"] is None, card()
print("ok  busy Codex session: phase working, cli codex, tokens from its log")
row = {"id": "w1", "name": "Ramanujan", "type": "gpt-5.5", "started": 1.0, "updated": 2.0, "finished": True,
       "action": "ls", "skills": [], "tokens": 9}
codex.SUBS[0] = [row]
poll()
assert card()["subagents"] == [row], card()["subagents"]
codex.SUBS[0] = []
print("ok  the card lists the Codex session's workers (codex.subagents) as its subagent rows")

s = codex.SESSIONS["cdx-1"]
s.update(state="idle")
del s["status"], s["pid"]
poll()
assert card()["phase"] == "needs_you" and card()["reason"] == "Stopped without reporting — reply to check on it", card()
codex.ANSWER[0] = "my chat answer"
poll()
log = reports.read(p.board, "t1")
assert log[-1]["status"] == "answer" and log[-1]["message"] == "my chat answer" and log[-1]["from"] == "claude", log[-1]
assert card()["phase"] == "finished", card()
print("ok  idle, no report: needs_you ('reply to check on it'), then its chat answer is copied to the card")

reports.append(p.board, "t1", "done", "All done.", "claude")
poll()
assert card()["phase"] == "finished" and card()["reason"] == "All done.", card()
print("ok  done report: phase finished")

# ---- (c) a card reply resumes the Codex session
queued = server.reply(p, "t1", "please also do Y")
assert queued is False, queued
tid, msg = codex.RESUMES[-1]
assert tid == "cdx-1" and msg.startswith("please also do Y") and msg.endswith(server.board_tail(p, "t1")), msg
log = reports.read(p.board, "t1")
assert log[-1]["status"] == "reply" and log[-1]["session"] == "cdx-1" and log[-1]["message"] == "please also do Y", log[-1]
assert not [c for c in claude_calls if c[:1] == ["stop"]], "a Codex resume ran claude stop"
print("ok  reply: codex.resume with the text + board tail, card logs the reply on session cdx-1")

# ---- answer_permission is Claude-only
try:
    server.answer_permission(p, "t1", "allow", "x")
    raise AssertionError("answer_permission on a Codex session did not refuse")
except LookupError as e:
    assert "Codex sessions have no permission prompts" in str(e), e
print("ok  answer_permission on a Codex session: LookupError")

# ---- (d) review with a Codex reviewer
time.sleep(1.1)  # git log --since has one-second steps
with open(os.path.join(proj, "a.txt"), "a") as f:
    f.write("two\n")
git("commit", "-q", "-am", "Do the task")
sha = git("rev-parse", "--short", "HEAD")
reports.append(p.board, "t1", "done", f"Did Y in commit {sha}.", "claude")
poll()
assert card()["phase"] == "finished", card()
rid = server.review(p, "t1", "low", "gpt-5.5")
assert rid == "cdx-2", rid
prompt = codex.STARTS[-1]["prompt"]
assert "commit-review" in prompt and "SKILL.md" in prompt and f'"low {sha}' in prompt, prompt
assert "the Skill tool, skill" not in prompt, "Codex reviewer got the Claude Skill-tool step"
assert prompt.endswith(server.codex_note(p)), prompt[-200:]
log = reports.read(p.board, "t1")
assert log[-1]["status"] == "review" and log[-1]["message"] == "Code review started (low, gpt-5.5, session cdx-2)", log[-1]
print("ok  review(low, gpt-5.5): codex.start with a commit-review / SKILL.md step naming", sha)

# ---- (e) tokens of a Codex log
assert server.tokens([LOG]) == {"main": [1, 2, 3, 4], "subagents": [0] * 4, "count": 0}, server.tokens([LOG])
print("ok  tokens([codex log]) uses codex.usage")
codex.SUB_USAGE[0] = ([5, 6, 7, 8], 2)
assert server.tokens([LOG]) == {"main": [1, 2, 3, 4], "subagents": [5, 6, 7, 8], "count": 2}, server.tokens([LOG])
codex.SUB_USAGE[0] = ([0, 0, 0, 0], 0)
print("ok  a Codex session's workers: tokens add codex.subagent_usage")

# ---- (e2) "divide in subtasks" wording for a Codex session
cx = "\n".join(server.delegate_lines("sonnet", "R", False, codex=True))
assert "spawn_agent" in cx and "Agent tool" not in cx and "Skill('" not in cx and 'model: "' not in cx, cx
assert "SKILL.md" in cx and "plan mode" not in cx, cx
cl = "\n".join(server.delegate_lines("sonnet", "R", False))
assert "Agent tool" in cl and "Skill('" in cl and 'model: "sonnet"' in cl and "spawn_agent" not in cl, cl
rem = server.delegate_reminder({"delegate": True, "agent": {"model": "gpt-5.5", "cli": "codex"}})
assert "spawn_agent" in rem and "Agent call" not in rem and "Skill('" not in rem, rem
assert "Skill('" in server.delegate_reminder({"delegate": True, "agent": {"model": "opus"}})
state = server.load_tasks(p)
td = dict(state["tasks"][0], delegate=True)
assert "spawn_agent" in server.build_prompt(p, state, td, None, "gpt-5.5")
assert "the Agent tool" in server.build_prompt(p, state, td, None, "opus")
assert "spawn_agent" in server.codex_note(p)
print("ok  delegate wording: Codex gets spawn_agent / SKILL.md paths, Claude keeps the Agent tool / Skill('…')")

# ---- (f) the model list route
assert server.codex_models() == {"models": codex.MODELS}, server.codex_models()
print("ok  codex_models (GET /api/codex-models) returns codex.models()")
print("PASS")
