"""The board drives an Antigravity session like a Codex one: models, start, watch, reply, review, hand-back, tokens.

Run: python3 tests/test_server_agy.py  (stdlib only; a fresh temp HOME, a fake agy module, no real CLI runs).
Each check is named after its spec point (plan mwsc8ylwxqq3w, spec.md)."""
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.realpath(tempfile.mkdtemp(prefix="agy-srv-test-"))
os.environ["HOME"] = os.path.join(TMP, "home")
os.makedirs(os.environ["HOME"])
os.environ["ONEMORETASK_NO_CLI_SETUP"] = "1"

FAKE = os.path.join(TMP, "fake")
os.makedirs(FAKE)
with open(os.path.join(FAKE, "agy.py"), "w", encoding="utf-8") as f:
    f.write('''
import os, tempfile
SESSIONS, STARTS, RESUMES, REMOVED = {}, [], [], []
ANSWER = [""]
MODELS = [{"id": "agy:gemini-4-neon-high", "name": "Gemini 4 Neon (High)"}]
FAIL = [None]
ERR = [""]
def is_model(model):
    return bool(model) and model.startswith("agy:")
def installed():
    return True
def models():
    return MODELS
def refreshing():
    return False
def models_error():
    return ERR[0]
def answer_dir(board, task_id):
    return os.path.join(tempfile.gettempdir(), "agy-answer-test", task_id)
def start(board, task_id, name, prompt, model, cwd, effort="", writable=None):
    if FAIL[0]:
        raise RuntimeError(FAIL[0])
    STARTS.append({"board": board, "task": task_id, "name": name, "prompt": prompt, "model": model, "cwd": cwd,
                   "effort": effort, "writable": writable})
    return "ag-%d" % len(STARTS)
def resume(board, sid, message):
    s = SESSIONS.get(sid)
    if s and "pid" in s:
        raise PermissionError("Antigravity is still working")
    RESUMES.append((sid, message))
    return sid
def sessions(board):
    return [dict(s) for s in SESSIONS.values() if s.get("board") == board]
def session(board, sid):
    s = SESSIONS.get(sid)
    return dict(s) if s and s.get("board") == board else None
def is_session(board, sid):
    return session(board, sid) is not None
def remove(board, sid, grace=5):
    REMOVED.append(sid)
    SESSIONS.pop(sid, None)
def last_answer(log_path, reply_text=None):
    return ANSWER[0]
def usage(log_path):
    return [11, 0, 22, 33]
def digest(log_path):
    return "AGY DIGEST"
def is_log(path):
    return "%sagent_reports%sagy%s" % (os.sep, os.sep, os.sep) in path
SUBS = [[]]
def subagents(log_path):
    return SUBS[0]
def subagent_usage(log_path):
    return [0, 0, 0, 0], 0
WROWS = [[]]
def worker_rows(board, task_id, since=0):
    return WROWS[0]
def worker_usage(board, task_id, since=0):
    return [0, 0, 0, 0], len(WROWS[0])
def context(log_path):
    return None
def mcp_uses(log_path):
    return []
ANSWERS = []
def answer(board, sid, choice, shown):
    s = SESSIONS.get(sid)
    if not s or s.get("ask") != shown:
        raise LookupError("the session is no longer asking that; the card will update")
    ANSWERS.append((sid, choice))
    return "write_file", "/private/tmp/q.gd", "NEXT TURN: " + choice
def words(tool, target):
    return "write the file " + target
''')
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, FAKE)
FAILS, RESULTS = [], []


def run(fn):
    try:
        fn()
        RESULTS.append(fn.__name__)
        print("ok   " + fn.__name__)
    except Exception as e:  # noqa: BLE001
        RESULTS.append(fn.__name__)
        FAILS.append(fn.__name__)
        print("FAIL " + fn.__name__ + ": " + (str(e) or type(e).__name__)[:400])
        if os.environ.get("VERBOSE"):
            traceback.print_exc()


try:
    import agy  # noqa: E402
    import codex  # noqa: E402
    import reports  # noqa: E402
    import server  # noqa: E402
    IMPORT_ERROR = None
except Exception as e:  # noqa: BLE001
    IMPORT_ERROR = e

proj = os.path.join(TMP, "proj")
os.makedirs(proj)


def git(*args):
    return subprocess.run(["git", "-C", proj, *args], capture_output=True, text=True, check=True).stdout.strip()


git("init", "-q")
git("config", "user.email", "t@example.com")
git("config", "user.name", "Test")
git("config", "commit.gpgsign", "false")
open(os.path.join(proj, "a.txt"), "w").write("one\n")
git("add", "a.txt")
git("commit", "-q", "-m", "Initial commit")
NOW = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
claude_calls, alerts = [], []


def fake_run_claude(args, **kw):
    claude_calls.append(args)
    if args and args[0] == "--bg":
        return types.SimpleNamespace(returncode=0, stdout="backgrounded · abc123\n", stderr="")
    if args[:1] in (["stop"], ["rm"]):
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    raise RuntimeError("no real claude in this test: %r" % (args[:3],))


def setup():
    global p, W
    p = server.Project(proj)
    os.makedirs(p.board, exist_ok=True)
    os.makedirs(os.path.dirname(server.REGISTRY), exist_ok=True)
    with open(server.REGISTRY, "w", encoding="utf-8") as f:
        json.dump({"projects": [{"path": proj, "opened": "2026-01-01T00:00:00+00:00"}]}, f)
    save({"columns": [{"id": "todo", "name": "To do"}, {"id": "review", "name": "Review"},
                      {"id": "done", "name": "Done", "done": True}], "tasks": []})
    server.run_claude = fake_run_claude
    server.list_sessions = lambda cwd: []
    server.ALERTS.publish = lambda a: alerts.append((a["title"], a["reason"]))
    W = server.WATCH


def save(state):
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump(state, f)


def add_task(tid, title="Ag task", agent=None, **kw):
    state = server.load_tasks(p)
    state["tasks"] = [t for t in state["tasks"] if t["id"] != tid]
    t = {"id": tid, "title": title, "column": "todo", "order": len(state["tasks"]), **kw}
    if agent:
        t["agent"] = agent
    state["tasks"].append(t)
    save(state)
    return t


def ag_session(sid, task, state="idle", busy=False, log=None, **kw):
    log = log or os.path.join(p.board, "agent_reports", "agy", sid + ".jsonl")
    os.makedirs(os.path.dirname(log), exist_ok=True)
    open(log, "a").close()
    s = {"id": sid, "sessionId": sid, "cli": "antigravity", "board": p.board, "name": "task", "model": "agy:gemini-4-neon-high",
         "cwd": p.path, "log": log, "err": "", "task": task, "state": state, "detail": "", **kw}
    if busy:
        s.update(status="busy", pid=4242)
    agy.SESSIONS[sid] = s
    return s


def poll():
    W.poll()
    for _ in range(300):
        if not W.delivering:
            break
        time.sleep(0.01)


def card(tid):
    return W.snapshot(p.id)[tid]


# ---------------------------------------------------------------- tests
def test_s7_models_route_keys():
    agy.ERR[0] = "Antigravity is not logged in"
    res = server.codex_models()
    assert res["antigravity"] is True and res["antigravity_models"] == agy.MODELS, res
    assert res["antigravity_refreshing"] is False and res["antigravity_error"] == "Antigravity is not logged in", res
    for k in ("models", "claude", "claude_models", "codex", "codex_refreshing"):
        assert k in res, k
    agy.ERR[0] = ""


def test_s12_valid_model():
    v = server.valid_model
    for m in ("opus", "sonnet", "haiku", "gpt-5.5", "claude-opus-4-1", "agy:gemini-4-neon-high", "agy:gemini-3.8-flash-low"):
        assert v(m), m
    for m in ("agy:", "agy:bad slug", "agy:x;rm", "foo:bar", ""):
        assert not v(m), m
    try:
        server.model_args("agy:gemini-4-neon-high")
        raise AssertionError("model_args took an Antigravity model")
    except ValueError:
        pass
    assert server.model_args("opus") == ["--model", "opus"]


def test_s15_session_effort_keeps_max():
    assert server.session_effort("agy:gemini-pro-orchestrator-neon", "max") == "max"
    assert server.session_effort("gpt-5.5", "max") == "xhigh"


def test_s16_failed_send_takes_back_worktree():
    add_task("tf", "Fail task")
    agy.FAIL[0] = "error: Authentication required\nLog in to Antigravity once in a terminal."
    try:
        server.launch(p, "tf", "agy:gemini-4-neon-high")
        raise AssertionError("a failed Antigravity start did not fail the send")
    except RuntimeError as e:
        assert "Log in to Antigravity" in str(e), e
    finally:
        agy.FAIL[0] = None
    assert not any("tf" in x for x in git("worktree", "list").splitlines()[1:]), git("worktree", "list")
    assert not reports.read(p.board, "tf"), "a failed send wrote a card log line"
    assert not server.load_tasks(p)["tasks"][-1].get("agent")


def test_s18_launch_record_and_line():
    add_task("t1", "Ag task", worktree=False)
    r = server.launch(p, "t1", "agy:gemini-4-neon-high", "high")
    assert r["cli"] == "antigravity", r
    agent = next(t for t in server.load_tasks(p)["tasks"] if t["id"] == "t1")["agent"]
    assert agent["cli"] == "antigravity" and agent["model"] == "agy:gemini-4-neon-high", agent
    line = reports.read(p.board, "t1")[-1]["message"]
    assert line.startswith("Sent to Antigravity (session ag-") and "model agy:gemini-4-neon-high" in line, line
    assert ", effort high" in line, line


def test_s19_prompt_ends_with_agy_note():
    st = agy.STARTS[-1]
    note = server.agy_note(p, False)
    assert st["prompt"].endswith(note) and st["cwd"] == p.path and st["writable"] is None, st["prompt"][-300:]
    for s in ("you run in Google's Antigravity CLI, not Claude Code", "SKILL.md",
              "use your own subagents, one per part, independent parts together",
              "You must still post on the card with the report.py command exactly as the brief says"):
        assert s in note, s
    assert not claude_calls or claude_calls[-1][0] != "--bg" or "agy:" not in " ".join(claude_calls[-1])


def test_s21_reply_goes_to_agy_by_id():
    ag_session("ag-1", "t1", "idle")
    W.launched["ag-1"] = time.time() - 100  # past the 45 s a fresh launch reads as working
    poll()
    n = len(claude_calls)
    server.reply(p, "t1", "please also do Y")
    sid, msg = agy.RESUMES[-1]
    assert sid == "ag-1" and msg.startswith("please also do Y"), (sid, msg[:80])
    assert len(claude_calls) == n, "a reply to an Antigravity card ran claude"
    assert server.task_transcripts(p, "t1", ["ag-1"]) == [agy.SESSIONS["ag-1"]["log"]]


def test_s23_remove_paths():
    add_task("tr", "Remove me", worktree=False)
    real = server.set_agent

    def boom(*a, **k):
        raise OSError("disk full")
    server.set_agent = boom
    try:
        try:
            server.launch(p, "tr", "agy:gemini-4-neon-high")
        except OSError:
            pass
    finally:
        server.set_agent = real
    assert agy.REMOVED and agy.REMOVED[-1].startswith("ag-"), ("failed-launch clean-up", agy.REMOVED)
    add_task("ta", "Archive me", agent={"id": "ag-9", "started": NOW, "model": "agy:gemini-4-neon-high",
                                         "cli": "antigravity"})
    ag_session("ag-9", "ta", "idle")
    reports.append(p.board, "ta", "launch", "Sent to Antigravity (session ag-9, model agy:x)", "you", session="ag-9")
    server.ask_claude = lambda *a, **k: "THE RECAP"
    server.archive(p, "ta")
    assert "ag-9" in agy.REMOVED, ("archive", agy.REMOVED)


def test_s26_phase_wording():
    s = {"id": "x", "cli": "antigravity", "state": "idle", "detail": ""}
    log = [{"status": "launch", "from": "you", "message": "Sent", "at": NOW, "session": "x"}]
    ph, why = server.phase_of(s, log, 0, "")
    assert (ph, why) == ("needs_you", "Stopped without reporting — reply to check on it"), (ph, why)
    s = dict(s, state="blocked", detail="ERROR: boom")
    ph, why = server.phase_of(s, log, 0, "")
    assert ph == "needs_you" and why == "Stopped in the session: ERROR: boom — reply to check on it", why
    add_task("tw", "Watch me", agent={"id": "ag-w", "started": NOW, "model": "agy:gemini-4-neon-high", "cli": "antigravity"})
    ag_session("ag-w", "tw", "working", busy=True)
    reports.append(p.board, "tw", "launch", "Sent to Antigravity (session ag-w, model agy:x)", "you", session="ag-w")
    called = []
    for name in ("popup_questions", "cache_info", "mcp_state", "auto_mode_stall"):
        orig = getattr(server, name)
        setattr(server, name, (lambda o, n: lambda *a, **k: (
            called.append(n) if a and (a[0] == "ag-w" or isinstance(a[0], dict) and a[0].get("id") == "ag-w") else None,
            o(*a, **k))[1])(orig, name))
    poll()
    c = card("tw")
    assert c["phase"] == "working" and c["cli"] == "antigravity", c
    assert called == [], ("Claude-only steps ran for an Antigravity session", called)


def test_s27_answer_copied():
    s = agy.SESSIONS["ag-w"]
    s.update(state="idle")
    s.pop("status", None), s.pop("pid", None)
    reports.append(p.board, "tw", "reply", "and Z?", "you", session="ag-w")
    agy.ANSWER[0] = "my chat answer"
    poll()
    last = reports.read(p.board, "tw")[-1]
    assert last["status"] == "answer" and last["message"] == "my chat answer", last
    agy.ANSWER[0] = ""


def test_s28_notes_wait_for_turn_end():
    s = ag_session("ag-w", "tw", "working", busy=True)
    reports.append(p.board, "tw", "reply", "go on", "you", session="ag-w")  # a reply started this turn
    poll()
    n = len(agy.RESUMES)
    queued = server.reply(p, "tw", "a note while it works")
    assert queued is True, queued
    poll()
    assert len(agy.RESUMES) == n, "a note was pushed into a running turn"
    s.update(state="idle")
    s.pop("status"), s.pop("pid")
    agy.SESSIONS["ag-w"] = s
    poll()
    assert len(agy.RESUMES) == n + 1 and "a note while it works" in agy.RESUMES[-1][1], agy.RESUMES[-1:]


def test_s28_reply_after_done_while_turn_ends():
    # Real run (S54): the session posted done, its process was still finishing the turn, and a reply then got
    # "Antigravity is still working". It must wait as a note and go in once the process exits.
    add_task("td", "Done but busy", agent={"id": "ag-d", "started": NOW, "model": "agy:gemini-4-neon-high",
                                            "cli": "antigravity"})
    s = ag_session("ag-d", "td", "working", busy=True)
    reports.append(p.board, "td", "launch", "Sent", "you", session="ag-d")
    reports.append(p.board, "td", "done", "Counted: 2 lines.", "claude")
    W.launched["ag-d"] = time.time() - 100
    poll()
    n = len(agy.RESUMES)
    queued = server.reply(p, "td", "and the first function?")
    assert queued is True, queued
    assert len(agy.RESUMES) == n
    s.update(state="idle")
    s.pop("status"), s.pop("pid")
    poll()
    assert len(agy.RESUMES) == n + 1 and "and the first function?" in agy.RESUMES[-1][1], agy.RESUMES[-1:]


def test_s30_tokens():
    log = agy.SESSIONS["ag-w"]["log"]
    assert server.tokens([log]) == {"main": [11, 0, 22, 33], "subagents": [0] * 4, "count": 0}, server.tokens([log])


def test_s33_recap_digest_and_log():
    got = {}
    server.ask_claude = lambda model, ask, text, timeout: got.setdefault("text", text) and "RECAP"
    add_task("tc", "Recap me", agent={"id": "ag-c", "started": NOW, "model": "agy:gemini-4-neon-high", "cli": "antigravity"})
    s = ag_session("ag-c", "tc", "idle")
    reports.append(p.board, "tc", "launch", "Sent", "you", session="ag-c")
    t, text, paths = server.archive(p, "tc")
    assert "AGY DIGEST" in got["text"], got
    assert s["log"] in paths, paths


def test_s34_alert_titles():
    W.primed = True
    add_task("tz", "Alert me", agent={"id": "ag-z", "started": NOW, "model": "agy:gemini-4-neon-high", "cli": "antigravity"})
    ag_session("ag-z", "tz", "working", busy=True)
    reports.append(p.board, "tz", "launch", "Sent", "you", session="ag-z")
    W.launched["ag-z"] = time.time() - 100
    poll()
    s = agy.SESSIONS["ag-z"]
    s.update(state="idle")
    s.pop("status"), s.pop("pid")
    alerts.clear()
    W.flush_alerts = lambda: [alerts.append((a["title"], a["reason"])) for a in W.alerts.values()]
    poll()
    titles = [a for a, _ in alerts]
    assert "Antigravity needs you" in titles, alerts
    reports.append(p.board, "tz", "done", "All done.", "claude")
    alerts.clear()
    poll()
    assert "Antigravity finished" in [a for a, _ in alerts], alerts
    add_task("tx", "Codex alert", agent={"id": "cdx-z", "started": NOW, "model": "gpt-5.5", "cli": "codex"})
    reports.append(p.board, "tx", "launch", "Sent", "you", session="cdx-z")
    W.launched["cdx-z"] = time.time() - 100
    orig = codex.sessions
    codex.sessions = lambda board: [{"id": "cdx-z", "sessionId": "cdx-z", "cli": "codex", "state": "working",
                                     "status": "busy", "pid": 1, "log": "", "err": "", "detail": ""}]
    poll()
    codex.sessions = lambda board: [{"id": "cdx-z", "sessionId": "cdx-z", "cli": "codex", "state": "idle",
                                     "log": "", "err": "", "detail": ""}]
    alerts.clear()
    poll()
    codex.sessions = orig
    assert "Codex needs you" in [a for a, _ in alerts], alerts


def test_s37_review_on_agy():
    add_task("tv", "Review me", agent={"id": "ag-v", "started": NOW, "model": "agy:gemini-4-neon-high", "cli": "antigravity"})
    ag_session("ag-v", "tv", "idle")
    reports.append(p.board, "tv", "launch", "Sent to Antigravity (session ag-v)", "you", session="ag-v")
    time.sleep(1.1)
    open(os.path.join(proj, "a.txt"), "a").write("two\n")
    git("commit", "-q", "-am", "Do the task")
    sha = git("rev-parse", "--short", "HEAD")
    reports.append(p.board, "tv", "done", f"Did it in commit {sha}.", "claude")
    poll()
    rid = server.review(p, "tv", "agy:gemini-4-neon-high")
    prompt = agy.STARTS[-1]["prompt"]
    assert rid.startswith("ag-") and "SKILL.md" in prompt and "the Skill tool, skill" not in prompt, prompt[:400]
    assert "Another Antigravity session did this task" in prompt, prompt[:600]
    assert prompt.endswith(server.agy_note(p)), prompt[-200:]


def _review_card(tid, worker_model, worker_cli, worker_id, rev_id, rev_cli):
    add_task(tid, "Hand back " + tid, agent={"id": worker_id, "started": NOW, "model": worker_model,
                                              **({"cli": worker_cli} if worker_cli != "claude" else {})})
    reports.append(p.board, tid, "launch", "Sent", "you", session=worker_id)
    reports.append(p.board, tid, "done", "Did it.", "claude")
    reports.append(p.board, tid, "review", "Code review started", "you", session=rev_id)
    reports.append(p.board, tid, "done", "FINDING 1: off by one", "claude")


def test_s38_handback_any_two_clis():
    # Antigravity reviewer -> Claude worker
    _review_card("h1", "opus", "claude", "cl-1", "ag-r1", "antigravity")
    ag_session("ag-r1", "h1", "idle")
    rows = [{"id": "cl-1", "sessionId": "uuid-cl-1", "state": "idle", "name": "task"}]
    server.list_sessions = lambda cwd: [dict(r) for r in rows]
    poll()
    assert card("h1")["handback"] == "Claude", card("h1")["handback"]
    # Claude reviewer -> Antigravity worker
    _review_card("h2", "agy:gemini-4-neon-high", "antigravity", "ag-w2", "cl-r2", "claude")
    ag_session("ag-w2", "h2", "idle")
    rows.append({"id": "cl-r2", "sessionId": "uuid-cl-r2", "state": "idle", "name": "review"})
    poll()
    assert card("h2")["handback"] == "Antigravity", card("h2")["handback"]
    server.handback(p, "h2", "fix 1")
    assert agy.RESUMES[-1][0] == "ag-w2" and "FINDING 1" in agy.RESUMES[-1][1], agy.RESUMES[-1]
    last = reports.read(p.board, "h2")[-1]
    assert last["message"] == "Sent the review findings back to Antigravity: fix 1", last
    # Codex reviewer -> Antigravity worker
    _review_card("h3", "agy:gemini-4-neon-high", "antigravity", "ag-w3", "cdx-r3", "codex")
    ag_session("ag-w3", "h3", "idle")
    orig = codex.sessions
    codex.sessions = lambda board: [{"id": "cdx-r3", "sessionId": "cdx-r3", "cli": "codex", "state": "idle",
                                     "log": "", "err": "", "detail": "", "board": board}]
    try:
        poll()
        assert card("h3")["handback"] == "Antigravity", card("h3")["handback"]
        # Claude worker, Antigravity review, then a Codex review: the findings go to Claude, not the first reviewer
        _review_card("h4", "opus", "claude", "cl-4", "ag-r4", "antigravity")
        ag_session("ag-r4", "h4", "idle")
        reports.append(p.board, "h4", "review", "Code review started", "you", session="cdx-r4")
        reports.append(p.board, "h4", "done", "FINDING 1: still off by one", "claude")
        rows.append({"id": "cl-4", "sessionId": "uuid-cl-4", "state": "idle", "name": "task"})
        codex.sessions = lambda board: [{"id": "cdx-r4", "sessionId": "cdx-r4", "cli": "codex", "state": "idle",
                                         "log": "", "err": "", "detail": "", "board": board}]
        assert server.review_worker(reports.read(p.board, "h4"), {"agent": {"id": "cdx-r4"}}) == "cl-4"
        poll()
        assert card("h4")["handback"] == "Claude", card("h4")["handback"]
    finally:
        codex.sessions = orig
    server.list_sessions = lambda cwd: []


def test_s47_answer_only_start():
    add_task("to", "Answer only", answerOnly=True)
    os.makedirs(os.path.join(proj, "skill-observations"), exist_ok=True)
    server.start_session(p, "to", "task: x", "THE BRIEF", "agy:gemini-4-neon-high")
    st = agy.STARTS[-1]
    assert st["writable"] == server.answer_only_roots(p), st
    assert st["prompt"].endswith(server.agy_note(p, True)), st["prompt"][-300:]
    assert "This card is answer-only: the project is read-only for you" in st["prompt"], st["prompt"][-400:]


def test_s77_observer_line_reaches_codex_and_agy():
    server.observations.installed = lambda: True
    state = server.load_tasks(p)
    t = dict(state["tasks"][0])
    for model in ("agy:gemini-4-neon-high", "gpt-5.5", "opus"):
        text = server.build_prompt(p, state, t, None, model)
        assert "Invoke the task-observer skill at the start" in text, model


def test_denied_step_shows_choices():
    """A turn that ended on a step it needed a yes for: the card gets it as a permission ask with its own alert, and
    the card's answer starts the next turn (agy.answer) with a reply line naming the choice."""
    W.primed = True
    add_task("tp", "Needs a yes", agent={"id": "ag-p", "started": NOW, "model": "agy:gemini-4-neon-high", "cli": "antigravity"})
    ag_session("ag-p", "tp", "working", busy=True)
    reports.append(p.board, "tp", "launch", "Sent", "you", session="ag-p")
    W.launched["ag-p"] = time.time() - 100
    poll()
    ask = "Antigravity asks permission: write_file: /private/tmp/q.gd"
    s = agy.SESSIONS["ag-p"]
    s.pop("status"), s.pop("pid")
    s.update(state="blocked", detail="Antigravity stopped: a step needed a yes it could not get", ask=ask)
    alerts.clear()
    W.flush_alerts = lambda: [alerts.append((a["title"], a["reason"])) for a in W.alerts.values()]
    poll()
    c = card("tp")
    assert c["phase"] == "needs_you" and c["permission"] == ask and c["reason"] == ask, c
    assert ("Antigravity asks permission", ask) in alerts, alerts
    try:
        server.answer_permission(p, "tp", "allow", ask + " (old)")
        raise AssertionError("a stale ask was answered")
    except LookupError:
        pass
    n = len(agy.RESUMES)
    r = server.answer_permission(p, "tp", "always", ask)
    assert r["resumed"] and agy.ANSWERS[-1] == ("ag-p", "always"), (r, agy.ANSWERS)
    assert len(agy.RESUMES) == n + 1 and agy.RESUMES[-1][0] == "ag-p", agy.RESUMES[-1:]
    msg = agy.RESUMES[-1][1]
    assert msg.startswith("Allowed for this session: write the file /private/tmp/q.gd") and "NEXT TURN: always" in msg, msg[:200]
    said = [e for e in reports.read(p.board, "tp") if e.get("status") == "reply"]
    assert said and said[-1]["message"] == "Allowed for this session: write the file /private/tmp/q.gd", said[-1:]
    assert "NEXT TURN" not in said[-1]["message"]


if __name__ == "__main__":
    if IMPORT_ERROR:
        print("FAIL import: %r" % (IMPORT_ERROR,))
        n = sum(1 for k in list(globals()) if k.startswith("test_"))
        print("\n%d of %d failed (nothing could run)" % (n, n))
        sys.exit(1)
    setup()
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            run(fn)
    print("\n%d of %d failed: %s" % (len(FAILS), len(RESULTS), ", ".join(FAILS)))
    sys.exit(1 if FAILS else 0)
