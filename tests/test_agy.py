"""The Antigravity backend (agy.py): models, start, resume, state, answer, steps, tokens, digest, against a fake `agy`.

Run: python3 tests/test_agy.py  (stdlib only; a fresh temp HOME, board and git project; never the real agy).
Each check is named after its spec point (plan mwsc8ylwxqq3w, spec.md)."""
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
FIX = os.path.join(HERE, "fixtures", "antigravity")
TMP = os.path.realpath(tempfile.mkdtemp(prefix="agy-test-"))
os.environ["HOME"] = os.path.join(TMP, "home")
os.makedirs(os.path.join(os.environ["HOME"], ".gemini"))
GOT = os.path.join(TMP, "got.jsonl")
MODELS_MODE = os.path.join(TMP, "models_mode")
MODELS_RUNS = os.path.join(TMP, "models_runs")
FAKE = os.path.join(TMP, "fake_agy.py")
with open(FAKE, "w", encoding="utf-8") as f:
    f.write(r'''#!/usr/bin/env python3
import hashlib, json, os, signal, subprocess, sys, time, uuid
args = sys.argv[1:]
TMP = %r
if args[:1] == ["models"]:
    with open(os.path.join(TMP, "models_runs"), "a") as g:
        g.write("x\n")
    mode = open(os.path.join(TMP, "models_mode")).read().strip() if os.path.exists(os.path.join(TMP, "models_mode")) else "ok"
    if mode == "slow":
        time.sleep(2)
    if mode == "fail":
        sys.stderr.write("Fetching available models...\n" + "E" * 400 + "\nmodels failed: no network\n")
        sys.exit(1)
    if mode == "junk":
        print("nothing useful here!")
        sys.exit(0)
    sys.stderr.write("Fetching available models...\n")
    print("gemini-4-neon-high\tGemini 4 Neon (High)")
    print("gemini-3.8-flash-low\tGemini 3.8 Flash (Low)")
    print("bad slug;rm\tJunk")
    print("gemini-pro-orchestrator-neon\tOrchestrator (Neon)")
    sys.exit(0)
line = sys.stdin.readline()
try:
    prompt = json.loads(line)["message"]["content"]
except Exception:
    prompt = "NOSTDIN"
with open(os.path.join(TMP, "got.jsonl"), "a", encoding="utf-8") as g:
    g.write(json.dumps({"argv": args, "cwd": os.getcwd(), "sha": hashlib.sha256(prompt.encode()).hexdigest(),
                        "line": line.endswith("\n"), "first": prompt.splitlines()[0] if prompt else ""}) + "\n")
def out(e):
    print(json.dumps(e), flush=True)
cid = args[args.index("--conversation") + 1] if "--conversation" in args else str(uuid.uuid4())
first = prompt.split()[0] if prompt.split() else ""
if first == "NOINIT":
    sys.stderr.write("line one\nerror: something broke\n")
    sys.exit(1)
if first == "AUTH":
    sys.stderr.write("Authentication required. Please visit the URL to log in:\n  https://x\n")
    sys.stderr.flush()
    time.sleep(30)
    sys.exit(1)
if first == "SLOW":
    time.sleep(5)
if first == "SLOWNOSANDBOX":
    time.sleep(3)
mode = ("always-proceed" if "--dangerously-skip-permissions" in args or first in ("NOSANDBOX", "SLOWNOSANDBOX")
        else "proceed-in-sandbox")
out({"event": "init", "conversation_id": cid, "init": {"model": args[args.index("--model") + 1] if "--model" in args else "",
     "cwd": os.getcwd(), "tools": ["run_command"], "permission_mode": mode}})
def step(i, kind, state, **kw):
    out({"event": "step_update", "step_update": {"conversation_id": cid, "step_index": i, "state": state,
         "step_type": kind, **kw}})
def result(status, response="", **kw):
    out({"event": "result", "result": {"conversation_id": cid, "status": status, "response": response,
         "num_turns": 1, "usage": {"input_tokens": 1000, "output_tokens": 10, "thinking_tokens": 0,
         "cache_read_tokens": 500, "total_tokens": 1010}, **kw}})
step(0, "user_input", "DONE")
if first in ("NOSANDBOX", "SLOWNOSANDBOX"):
    time.sleep(5)
if first == "WAIT":
    time.sleep(4)
if first == "CHILD":
    p = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"])
    open(prompt.split()[1], "w").write(str(p.pid))
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)
if first == "ERROR":
    result("ERROR", error="the model said no")
    sys.exit(1)
if first == "CANCELED":
    result("CANCELED")
    sys.exit(0)
if first == "EMPTY":
    step(1, "tool", "DONE", tool_name="run_command", tool_info={"name": "run_command", "parameters": {"CommandLine": "ls"}})
    result("SUCCESS", "")
    sys.exit(0)
if first == "DENIED":  # the step error is agy 1.5.14's (a real mage review turn, 2026-11-23)
    t = prompt.split()[1] if len(prompt.split()) > 1 else "/private/tmp/x.gd"
    info = {"name": "write_file", "parameters": {"Path": t}}
    step(1, "tool", "ACTIVE", tool_name="write_file", tool_info=info)
    step(1, "tool", "ERROR", tool_name="write_file", tool_info={**info, "error": {"type": "TOOL_ERROR", "message":
         'permission check failed for write_file "%%s": user denied permission for write_file(%%s)\nDo not attempt to '
         'circumvent this denial by rephrasing the command. Proceed without performing this action.' %% (t, t)}})
    sys.stderr.write('jetski: no output produced — a tool required the "write_file" permission that headless mode cannot prompt for, so it was auto-denied.\n')
    result("SUCCESS", "")
    sys.exit(0)
if first == "NORESULT":
    print("this is not json")
    out({"event": "mystery"})
    step(1, "weird_type", "DONE")
    sys.stderr.write("last words\n")
    sys.exit(0)
text = "ECHO: " + (prompt.splitlines()[0] if prompt else "")
step(1, "tool", "ACTIVE", tool_name="run_command", tool_info={"name": "run_command", "parameters": {"CommandLine": "ls -la\nmore"}})
step(1, "tool", "DONE", tool_name="run_command", tool_info={"name": "run_command", "parameters": {"CommandLine": "ls -la\nmore"}, "output": "x"})
step(2, "agent_response", "ACTIVE", text_delta=text)
step(2, "agent_response", "DONE", text_delta="\n", usage={"input_tokens": 100, "output_tokens": 7, "thinking_tokens": 3, "cache_read_tokens": 40, "total_tokens": 110})
result("SUCCESS", text + "\n")
sys.exit(3 if first == "EXIT3" else 0)
''' % TMP)
os.chmod(FAKE, 0o755)

os.environ["ONEMORETASK_AGY"] = FAKE
sys.path.insert(0, APP)

FAILS = []
RESULTS = []


def run(fn):
    try:
        fn()
        RESULTS.append(("ok", fn.__name__))
        print("ok   " + fn.__name__)
    except Exception as e:  # noqa: BLE001 - every failure is reported, the run goes on
        RESULTS.append(("FAIL", fn.__name__))
        FAILS.append(fn.__name__)
        print("FAIL " + fn.__name__ + ": " + (str(e) or type(e).__name__)[:400])
        if os.environ.get("VERBOSE"):
            traceback.print_exc()


def agy():
    import agy as m  # noqa: E402 - imported per test so a missing module fails each test, not the run
    return m


def login(on=True):
    path = os.path.join(os.environ["HOME"], ".gemini", "oauth_creds.json")
    if on:
        open(path, "w").write("{}")
    elif os.path.exists(path):
        os.remove(path)


def git(cwd, *a):
    return subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True, check=True).stdout.strip()


PROJ = os.path.join(TMP, "proj")
os.makedirs(PROJ)
git(PROJ, "init", "-q")
git(PROJ, "config", "user.email", "t@example.com")
git(PROJ, "config", "user.name", "T")
git(PROJ, "config", "commit.gpgsign", "false")
open(os.path.join(PROJ, "a.txt"), "w").write("a\n")
git(PROJ, "add", "a.txt")
git(PROJ, "commit", "-qm", "init")
BOARD = os.path.join(PROJ, ".task_board")
os.makedirs(BOARD)


def calls():
    try:
        return [json.loads(x) for x in open(GOT, encoding="utf-8")]
    except OSError:
        return []


def wait_state(m, sid, want, limit=15):
    end = time.time() + limit
    s = None
    while time.time() < end:
        s = m.session(BOARD, sid)
        if s and s["state"] in want:
            return s
        time.sleep(0.1)
    raise AssertionError(f"session {sid} never reached {want}: {s}")


def settings_of(argv):
    return json.loads(argv[argv.index("--settings") + 1])


# ---------------------------------------------------------------- B. models
def test_s3_installed_and_cmd():
    m = agy()
    assert m.AGY_CMD == [FAKE], m.AGY_CMD
    assert m.installed() is True
    code = ("import os,sys; sys.path.insert(0,%r); import agy; print(agy.AGY_CMD[0]); print(agy.installed())" % APP)
    env = dict(os.environ)
    env.pop("ONEMORETASK_AGY")
    bindir = os.path.join(TMP, "bin")
    os.makedirs(bindir, exist_ok=True)
    shutil.copy(FAKE, os.path.join(bindir, "agy"))
    env["PATH"] = bindir + os.pathsep + "/usr/bin:/bin"
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True).stdout.split()
    assert out == [os.path.join(bindir, "agy"), "True"], ("on PATH", out)
    env["PATH"] = "/usr/bin:/bin"
    home2 = os.path.join(TMP, "home2")
    os.makedirs(os.path.join(home2, ".local", "bin"), exist_ok=True)
    shutil.copy(FAKE, os.path.join(home2, ".local", "bin", "agy"))
    env["HOME"] = home2
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True).stdout.split()
    assert out == [os.path.join(home2, ".local", "bin", "agy"), "True"], ("~/.local/bin", out)
    env["HOME"] = os.path.join(TMP, "home3")
    out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True).stdout.split()
    assert out[-1] == "False", ("none", out)


def _fresh_models(m):
    m._models = None if hasattr(m, "_models") else None
    for k in ("_models", "_models_at", "_models_tried", "_models_err"):
        if hasattr(m, k):
            setattr(m, k, {"_models": None, "_models_at": 0.0, "_models_tried": 0.0, "_models_err": ""}[k])


def _wait_refresh(m, limit=10):
    end = time.time() + limit
    while m.refreshing() and time.time() < end:
        time.sleep(0.05)
    assert not m.refreshing(), "refresh never ended"


def _runs():
    try:
        return len(open(MODELS_RUNS).read().split())
    except OSError:
        return 0


def test_s4_models_parsed():
    m = agy()
    login(True)
    _fresh_models(m)
    open(MODELS_MODE, "w").write("ok")
    m.models()
    _wait_refresh(m)
    got = m.models()
    assert got == [{"id": "agy:gemini-4-neon-high", "name": "Gemini 4 Neon (High)"},
                   {"id": "agy:gemini-3.8-flash-low", "name": "Gemini 3.8 Flash (Low)"},
                   {"id": "agy:gemini-pro-orchestrator-neon", "name": "Orchestrator (Neon)"}], got


def test_s5_models_cache_refresh_rules():
    m = agy()
    login(True)
    _fresh_models(m)
    clock = [1000.0]
    m._now = lambda: clock[0]
    open(MODELS_MODE, "w").write("slow")
    n0 = _runs()
    assert m.models() == [], "a missing list is returned empty, without waiting"
    assert m.refreshing() is True, "a missing list starts a refresh"
    m.models()
    _wait_refresh(m)
    assert _runs() == n0 + 1, ("a second call during a refresh starts nothing", _runs() - n0)
    first = m.models()
    assert len(first) == 3 and _runs() == n0 + 1, "a fresh list is returned with no run"
    clock[0] += 3599
    m.models()
    assert not m.refreshing() and _runs() == n0 + 1, "younger than 1 hour: no run"
    clock[0] += 2
    open(MODELS_MODE, "w").write("fail")
    assert m.models() == first, "an old list is returned as is"
    _wait_refresh(m)
    assert _runs() == n0 + 2 and m.models() == first, "a failed refresh keeps the old list"
    err = m.models_error()
    assert err.endswith("models failed: no network") and len(err) <= 300, err
    clock[0] += 599
    m.models()
    assert not m.refreshing() and _runs() == n0 + 2, "after a failure the next refresh waits 10 minutes"
    clock[0] += 2
    open(MODELS_MODE, "w").write("junk")
    m.models()
    _wait_refresh(m)
    assert _runs() == n0 + 3 and m.models() == first, "no valid line counts as a failure"


def test_s6_no_login_no_refresh():
    m = agy()
    login(False)
    _fresh_models(m)
    n0 = _runs()
    assert m.models() == []
    time.sleep(0.3)
    assert _runs() == n0 and not m.refreshing(), "a refresh ran without a login"
    assert m.models_error() == "Antigravity is not logged in", m.models_error()
    open(os.path.join(os.environ["HOME"], ".gemini", "google_accounts.json"), "w").write("{}")
    try:
        _fresh_models(m)
        m.models()
        assert m.refreshing() or _runs() == n0 + 1, "google_accounts.json alone counts as a login"
        _wait_refresh(m)
    finally:
        os.remove(os.path.join(os.environ["HOME"], ".gemini", "google_accounts.json"))
    login(True)


# ---------------------------------------------------------------- C. starting, replying, stopping
def _start(prompt="hello there", model="agy:gemini-4-neon-high", effort="", writable=None, task="t1"):
    m = agy()
    return m, m.start(BOARD, task, "task: x", prompt, model, PROJ, effort, writable=writable)


def test_s13_argv_settings_cwd():
    open(GOT, "w").close()
    m, sid = _start()
    c = calls()[-1]
    a = c["argv"]
    for flag in ("-p=", "--input-format", "--output-format", "--model", "--mode", "--sandbox", "--settings", "--log-file"):
        assert flag in a, (flag, a)
    assert a[a.index("--input-format") + 1] == "stream-json" and a[a.index("--output-format") + 1] == "stream-json", a
    assert a[a.index("--model") + 1] == "gemini-4-neon-high" and a[a.index("--mode") + 1] == "accept-edits", a
    log = a[a.index("--log-file") + 1]
    assert os.path.dirname(log) == os.path.join(BOARD, "agent_reports", "agy") and log.endswith("-1.agy.log"), log
    assert os.path.realpath(c["cwd"]) == os.path.realpath(PROJ), c["cwd"]
    st = settings_of(a)
    assert st["enableTerminalSandbox"] is True and st["toolPermission"] == "proceed-in-sandbox", st
    gitdir = os.path.realpath(os.path.join(PROJ, ".git"))
    skills = os.path.realpath(os.path.join(os.environ["HOME"], ".claude", "skills"))  # the global skills (S81 real run)
    assert st["permissions"]["allow"] == [f"write_file({gitdir})", f"read_file({os.path.realpath(APP)})",
                                          f"read_file({skills})", "mcp(*)", "read_url(*)"], st
    wait_state(m, sid, ("idle",))


def test_s13_worktrees_outside_project():
    m = agy()
    sub = os.path.join(PROJ, "sub")
    os.makedirs(sub, exist_ok=True)
    allow = m.settings(sub)["permissions"]["allow"]
    assert f"write_file({os.path.realpath(os.path.join(PROJ, '.claude', 'worktrees'))})" in allow, allow


def test_s14_prompt_on_stdin_byte_for_byte():
    open(GOT, "w").close()
    big = "big prompt\n" + "é" * 30000 + "x" * 30000
    m, sid = _start(big)
    c = calls()[-1]
    assert c["sha"] == hashlib.sha256(big.encode()).hexdigest() and c["line"], "the prompt did not arrive whole"
    assert not any(big[:50] in x for x in c["argv"]), "the prompt went in argv"
    wait_state(m, sid, ("idle",))


def test_s15_effort_rules():
    open(GOT, "w").close()
    m, sid = _start(model="agy:gemini-pro-orchestrator-neon", effort="max")
    a = calls()[-1]["argv"]
    assert a[a.index("--effort") + 1] == "max", a
    wait_state(m, sid, ("idle",))
    m, sid = _start(model="agy:gemini-4-neon-high", effort="low")
    assert "--effort" not in calls()[-1]["argv"], calls()[-1]["argv"]
    wait_state(m, sid, ("idle",))


def test_s16_start_failures():
    m = agy()
    before = set(os.listdir(os.path.join(BOARD, "agent_reports", "agy"))) if os.path.isdir(os.path.join(BOARD, "agent_reports", "agy")) else set()
    try:
        m.start(BOARD, "tf", "n", "NOINIT", "agy:gemini-4-neon-high", PROJ)
        raise AssertionError("no error for an exit before init")
    except RuntimeError as e:
        assert "error: something broke" in str(e) and "line one" in str(e), e
    metas = [n for n in os.listdir(os.path.join(BOARD, "agent_reports", "agy")) if n.endswith(".json")]
    assert set(metas) <= before, ("a meta was written for a failed start", set(metas) - before)
    m.START_WAIT, old = 1, m.START_WAIT
    try:
        t0 = time.time()
        try:
            m.start(BOARD, "tf", "n", "AUTH", "agy:gemini-4-neon-high", PROJ)
            raise AssertionError("no error for a silent start")
        except RuntimeError as e:
            assert time.time() - t0 < 10, "it waited past START_WAIT"
            assert str(e).endswith("Log in to Antigravity once in a terminal."), e
        try:
            m.start(BOARD, "tf", "n", "SLOW", "agy:gemini-4-neon-high", PROJ)
            raise AssertionError("no error for a start with no init in time")
        except RuntimeError:
            pass
    finally:
        m.START_WAIT = old


def test_s17_unknown_model_fixture_d():
    m = agy()
    fake = os.path.join(TMP, "fake_d.py")
    with open(fake, "w") as f:
        f.write("import sys\nsys.stdin.readline()\nsys.stdout.write(open(%r).read())\nsys.stderr.write(open(%r).read())\n"
                "sys.exit(%s)\n" % (os.path.join(FIX, "d.stdout"), os.path.join(FIX, "d.stderr"),
                                   open(os.path.join(FIX, "d.exit")).read().strip()))
    old = m.AGY_CMD
    m.AGY_CMD = [sys.executable, fake]
    try:
        m.start(BOARD, "td", "n", "hi", "agy:no-such-model-xyz", PROJ)
        raise AssertionError("an unknown model did not fail the send")
    except RuntimeError as e:
        assert "invalid model selection" in str(e), e
    finally:
        m.AGY_CMD = old


def test_s20_resume_same_argv_new_turn():
    open(GOT, "w").close()
    m, sid = _start("first turn")
    wait_state(m, sid, ("idle",))
    first = calls()[-1]["argv"]
    meta = json.load(open(os.path.join(BOARD, "agent_reports", "agy", sid + ".json")))
    size = os.path.getsize(meta["log"])
    assert m.resume(BOARD, sid, "WAIT second turn") == sid
    try:
        m.resume(BOARD, sid, "third")
        raise AssertionError("a resume while working did not refuse")
    except PermissionError as e:
        assert "Antigravity is still working" in str(e), e
    wait_state(m, sid, ("idle",))
    got = calls()
    assert len(got) == 2, ("a refused resume still ran agy", len(got))
    second = got[-1]["argv"]
    assert "--conversation" in second and second[second.index("--conversation") + 1] == sid, second
    assert "-c" not in second and "--continue" not in second, second
    strip = lambda a: [x for i, x in enumerate(a) if not (x.endswith(".agy.log") or x in ("--conversation", sid))]
    assert strip(first) == strip(second), (first, second)
    assert second[second.index("--log-file") + 1].endswith("-2.agy.log"), second
    meta = json.load(open(os.path.join(BOARD, "agent_reports", "agy", sid + ".json")))
    assert meta["turn_offset"] == size and meta["turns"] == 2, meta
    assert m.last_answer(meta["log"]) == "ECHO: WAIT second turn", m.last_answer(meta["log"])


def test_s22_remove_kills_group():
    m = agy()
    pidfile = os.path.join(TMP, "child.pid")
    sid = m.start(BOARD, "tk", "n", "CHILD " + pidfile, "agy:gemini-4-neon-high", PROJ)
    end = time.time() + 10
    while not os.path.exists(pidfile) and time.time() < end:
        time.sleep(0.05)
    child = int(open(pidfile).read())
    m.remove(BOARD, sid, grace=0.5)
    time.sleep(0.3)
    try:
        os.kill(child, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, "the child that ignores SIGTERM is still alive"
    meta = json.load(open(os.path.join(BOARD, "agent_reports", "agy", sid + ".json")))
    assert meta["removed"] is True and os.path.exists(meta["log"]), meta
    assert m.session(BOARD, sid) is None


def test_s24_states():
    m = agy()
    sid = m.start(BOARD, "ts", "n", "WAIT a", "agy:gemini-4-neon-high", PROJ)
    s = m.session(BOARD, sid)
    assert s["state"] == "working" and s["status"] == "busy" and s["pid"] and s["cli"] == "antigravity", ("row 1", s)
    for k in ("id", "sessionId", "name", "model", "cwd", "log", "err", "task", "detail"):
        assert k in s, k
    assert s["model"] == "agy:gemini-4-neon-high", s
    wait_state(m, sid, ("idle",))
    want = {"ERROR": ("blocked", "ERROR: the model said no"), "CANCELED": ("blocked", "CANCELED: "),
            "EXIT3": ("blocked", None), "NORESULT": ("blocked", "Antigravity ended without a result"),
            "DENIED": ("blocked", "Antigravity stopped: a step needed a yes it could not get"),
            "EMPTY": ("blocked", "Antigravity stopped without an answer"), "fine": ("idle", "")}
    for word, (state, detail) in want.items():
        sid = m.start(BOARD, "ts", "n", word + " go", "agy:gemini-4-neon-high", PROJ)
        s = wait_state(m, sid, ("idle", "blocked"))
        assert s["state"] == state, (word, s)
        if word == "NORESULT":
            assert s["detail"].startswith(detail) and "last words" in s["detail"], (word, s["detail"])
        elif detail is not None:
            assert s["detail"] == detail, (word, s["detail"])


def test_s25_unknown_lines_skipped():
    m = agy()
    log = os.path.join(TMP, "s25.jsonl")
    with open(log, "w") as f:
        f.write(open(os.path.join(FIX, "a.stdout")).read())
        f.write("not json at all\n" + json.dumps({"event": "mystery"}) + "\n")
        f.write(json.dumps({"event": "step_update", "step_update": {"step_type": "new_kind", "state": "ACTIVE"}}) + "\n")
    assert m.last_answer(log) == "PONG", m.last_answer(log)
    assert [r["kind"] for r in m.mcp_uses(log)] == ["say"], m.mcp_uses(log)


def test_s27_last_answer_fixture_a():
    m = agy()
    a = m.last_answer(os.path.join(FIX, "a.stdout"))
    assert a == "PONG" == open(os.path.join(FIX, "a-text.stdout")).read().strip(), a
    two = os.path.join(TMP, "s27.jsonl")
    with open(two, "w") as f:
        f.write(open(os.path.join(FIX, "b.stdout")).read() + open(os.path.join(FIX, "c.stdout")).read())
    assert m.last_answer(two) == "world", m.last_answer(two)
    assert m.last_answer(os.path.join(FIX, "h.stdout")) == "", "an empty answer must copy nothing"


def test_s29_mcp_uses_rows():
    m = agy()
    rows = m.mcp_uses(os.path.join(FIX, "b.stdout"))
    tools = [(r["kind"], r["tool"], r["what"], r["state"]) for r in rows if r["kind"] == "tool"]
    assert ("tool", "run_command", "ls src", "done") in tools, tools
    assert ("tool", "read_file", "src/a.txt", "done") in tools, tools
    assert ("tool", "edit_file", "src/a.txt", "done") in tools, tools
    for r in rows:
        assert set(r) >= {"id", "server", "tool", "what", "kind", "at", "state", "sub"}, r
    says = [r["what"] for r in rows if r["kind"] == "say"]
    assert says and says[-1] == "DONE", says
    e = [r for r in m.mcp_uses(os.path.join(FIX, "e.stdout")) if r["kind"] == "mcp"]
    assert e and e[0]["server"] == "muninn" and e[0]["tool"] == "muninn_status" and e[0]["state"] == "done", e
    h = [r for r in m.mcp_uses(os.path.join(FIX, "h.stdout")) if r["tool"] == "write_file"]
    assert h and h[0]["state"] == "failed", h
    log = os.path.join(TMP, "s29.jsonl")
    with open(log, "w") as f:
        f.write(json.dumps({"event": "step_update", "step_update": {"step_index": 3, "state": "ACTIVE", "step_type": "tool",
                            "tool_name": "run_command", "tool_info": {"name": "run_command",
                            "parameters": {"CommandLine": "pytest -q\nsecond line"}}}}) + "\n")
    r = m.mcp_uses(log)
    assert r and r[0]["state"] == "open" and r[0]["what"] == "pytest -q", r


def test_s30_usage_sums_steps():
    m = agy()
    two = os.path.join(TMP, "s30.jsonl")
    with open(two, "w") as f:
        f.write(open(os.path.join(FIX, "b.stdout")).read() + open(os.path.join(FIX, "c.stdout")).read())
    assert m.usage(two) == [18375, 0, 30720, 150], m.usage(two)
    bad = os.path.join(TMP, "s30b.jsonl")
    with open(bad, "w") as f:
        f.write(json.dumps({"event": "step_update", "step_update": {"step_type": "agent_response", "state": "DONE",
                            "usage": {"input_tokens": "x", "output_tokens": 5, "thinking_tokens": 2}}}) + "\n")
    assert m.usage(bad) == [0, 0, 0, 7], m.usage(bad)


def test_s31_no_context():
    m = agy()
    assert m.context(os.path.join(FIX, "b.stdout")) is None


def test_s32_subagents_fixture_f():
    m = agy()
    rows = m.subagents(os.path.join(FIX, "f.stdout"))
    assert len(rows) == 1, rows
    r = rows[0]
    assert r["name"] == "Line Counter" and r["id"] == "4c99fa64-6c18-4588-aa8e-e7966c1963b4" and r["finished"], r
    assert r["type"] == "—" and r["tokens"] in (0, None, "—"), r
    assert m.subagents(os.path.join(FIX, "b.stdout")) == []
    assert m.subagent_usage(os.path.join(FIX, "f.stdout"))[0] == [0, 0, 0, 0]


def test_s33_digest():
    m = agy()
    d = m.digest(os.path.join(FIX, "b.stdout"))
    assert d.startswith("TURN 1") and "ANTIGRAVITY: DONE" in d and "· ls src" in d, d
    assert (m.DIGEST_HEAD, m.DIGEST_TAIL, m.DIGEST_BLOCK) == (30_000, 150_000, 4_000)


def test_s45_never_skip_permissions():
    got = calls()
    assert got, "no recorded runs"
    for c in got:
        assert "--dangerously-skip-permissions" not in c["argv"], c["argv"]


def test_s46_sandbox_off_stops_turn():
    m = agy()
    sid = m.start(BOARD, "tn", "n", "NOSANDBOX go", "agy:gemini-4-neon-high", PROJ)
    s = wait_state(m, sid, ("blocked",), limit=4)
    assert s["detail"] == "Antigravity's sandbox is not on, so the board stopped it", s


def test_s46_slow_reply_still_checked():
    """A reply whose init comes after START_WAIT still gets the sandbox check (Codex review finding 2)."""
    m, sid = _start("first turn", task="ts")
    wait_state(m, sid, ("idle",))
    m.START_WAIT, old = 1, m.START_WAIT
    try:
        assert m.resume(BOARD, sid, "SLOWNOSANDBOX go") == sid
    finally:
        m.START_WAIT = old
    s = wait_state(m, sid, ("blocked",), limit=8)
    assert s["detail"] == m.SANDBOX_OFF, s


def test_s29_steps_keep_log_order():
    """Eleven steps read for the first time share one time: they keep the order agy wrote them (finding 3)."""
    m = agy()
    log = os.path.join(TMP, "s29order.jsonl")
    with open(log, "w") as f:
        f.write(json.dumps({"event": "init", "conversation_id": "c"}) + "\n")
        for i in range(1, 12):
            f.write(json.dumps({"event": "step_update", "step_update": {"step_index": i, "state": "DONE", "step_type": "tool",
                                "tool_name": "run_command", "tool_info": {"parameters": {"CommandLine": f"echo {i}"}}}}) + "\n")
    order = [int(r["what"].split()[1]) for r in m.mcp_uses(log)]
    assert order == list(range(1, 12)), order


def test_s47_answer_only_lock():
    open(GOT, "w").close()
    obs = os.path.join(PROJ, "skill-observations")
    os.makedirs(obs, exist_ok=True)
    m, sid = _start("ro", writable=[os.path.realpath(BOARD), os.path.realpath(obs)], task="tro")
    c = calls()[-1]
    assert os.path.realpath(c["cwd"]) == os.path.realpath(m.answer_dir(BOARD, "tro")), c["cwd"]
    assert os.path.realpath(c["cwd"]) != os.path.realpath(PROJ)
    allow = settings_of(c["argv"])["permissions"]["allow"]
    assert f"read_file({os.path.realpath(PROJ)})" in allow, allow
    assert f"write_file({os.path.realpath(BOARD)})" in allow and f"write_file({os.path.realpath(obs)})" in allow, allow
    assert not any(x.startswith("write_file(") and ".git" in x for x in allow), allow
    wait_state(m, sid, ("idle",))
    m.resume(BOARD, sid, "again")
    c2 = calls()[-1]
    assert os.path.realpath(c2["cwd"]) == os.path.realpath(c["cwd"]) and settings_of(c2["argv"]) == settings_of(c["argv"])
    wait_state(m, sid, ("idle",))


def test_denied_step_choices():
    """A turn that ended on a step it needed a yes for: the session asks it, and allow / always / deny go into the
    next turn (allow for that turn only, always for every later one, deny adds no rule)."""
    open(GOT, "w").close()
    m, sid = _start("DENIED /private/tmp/q.gd", task="tden")
    s = wait_state(m, sid, ("idle", "blocked"))
    want = "Antigravity asks permission: write_file: /private/tmp/q.gd"
    assert s["state"] == "blocked" and s["detail"] == m.DENIED and s["ask"] == want, s
    rule = "write_file(/private/tmp/q.gd)"
    try:
        m.answer(BOARD, sid, "allow", want + "x")
        raise AssertionError("a stale ask was answered")
    except LookupError:
        pass
    tool, target, msg = m.answer(BOARD, sid, "allow", want)
    assert (tool, target) == ("write_file", "/private/tmp/q.gd") and "allowed" in msg and target in msg, msg
    m.resume(BOARD, sid, msg)
    assert rule in settings_of(calls()[-1]["argv"])["permissions"]["allow"], "allow: rule missing in the next turn"
    s = wait_state(m, sid, ("idle", "blocked"))
    assert s["state"] == "idle" and "ask" not in s, s
    m.resume(BOARD, sid, "go on")
    assert rule not in settings_of(calls()[-1]["argv"])["permissions"]["allow"], "allow: rule kept past one turn"
    wait_state(m, sid, ("idle",))
    m.resume(BOARD, sid, "DENIED /private/tmp/q.gd")
    wait_state(m, sid, ("blocked",))
    try:
        m.answer(BOARD, sid, "maybe", want)
        raise AssertionError("an unknown choice was taken")
    except ValueError:
        pass
    m.answer(BOARD, sid, "always", want)
    for text in ("go on", "and on"):
        m.resume(BOARD, sid, text)
        assert rule in settings_of(calls()[-1]["argv"])["permissions"]["allow"], "always: rule dropped"
        wait_state(m, sid, ("idle",))
    m2, sid2 = _start("DENIED /private/tmp/r.gd", task="tden2")
    wait_state(m2, sid2, ("blocked",))
    tool, target, msg = m2.answer(BOARD, sid2, "deny", "Antigravity asks permission: write_file: /private/tmp/r.gd")
    assert "said no" in msg, msg
    m2.resume(BOARD, sid2, msg)
    assert not any("r.gd" in r for r in settings_of(calls()[-1]["argv"])["permissions"]["allow"]), "deny added a rule"
    wait_state(m2, sid2, ("idle",))
    # Allow whose turn never started (a failed start), then Deny: the next turn must not carry the allow.
    m3, sid3 = _start("DENIED /private/tmp/s.gd", task="tden3")
    wait_state(m3, sid3, ("blocked",))
    ask3 = "Antigravity asks permission: write_file: /private/tmp/s.gd"
    m3.answer(BOARD, sid3, "allow", ask3)
    m3.answer(BOARD, sid3, "deny", ask3)
    m3.resume(BOARD, sid3, "go on")
    assert not any("s.gd" in r for r in settings_of(calls()[-1]["argv"])["permissions"]["allow"]), \
        "deny after an unused allow: the step is still allowed"
    wait_state(m3, sid3, ("idle",))
    # Answers given before a turn starts replace each other whole (Codex review of 234a6ab).
    ask4 = "Antigravity asks permission: write_file: /private/tmp/t.gd"
    rule4 = "write_file(/private/tmp/t.gd)"
    allowed = lambda: settings_of(calls()[-1]["argv"])["permissions"]["allow"]
    for first, then, now, later in (("always", "deny", False, False), ("always", "allow", True, False),
                                    ("allow", "always", True, True)):
        m4, sid4 = _start("DENIED /private/tmp/t.gd", task="tden4" + first + then)
        wait_state(m4, sid4, ("blocked",))
        m4.answer(BOARD, sid4, first, ask4)
        m4.answer(BOARD, sid4, then, ask4)
        m4.resume(BOARD, sid4, "go on")
        assert (rule4 in allowed()) == now, (first, then, "next turn", allowed())
        wait_state(m4, sid4, ("idle",))
        m4.resume(BOARD, sid4, "and on")
        assert (rule4 in allowed()) == later, (first, then, "later turns", allowed())
        wait_state(m4, sid4, ("idle",))
    # A turn that fails to start saves nothing: Allow always stays pending, out of the session's settings.
    m5, sid5 = _start("DENIED /private/tmp/u.gd", task="tden5")
    wait_state(m5, sid5, ("blocked",))
    m5.answer(BOARD, sid5, "always", "Antigravity asks permission: write_file: /private/tmp/u.gd")
    try:
        m5.resume(BOARD, sid5, "NOINIT")
        raise AssertionError("a failed start did not raise")
    except RuntimeError:
        pass
    meta5 = m5._read_meta(BOARD, sid5)
    assert "u.gd" not in meta5["settings"] and meta5["pending"]["keep"], meta5
    assert m.words("write_file", "/a") == "write the file /a" and m.words("odd_tool", "/a") == "odd_tool /a"


def test_sessions_no_folder_made():
    m = agy()
    other = os.path.join(TMP, "other_board")
    os.makedirs(other)
    assert m.sessions(other) == [] and not os.path.exists(os.path.join(other, "agent_reports")), "reading made a folder"
    assert m.is_model("agy:x") and not m.is_model("gpt-5.5") and not m.is_model("")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            run(fn)
    print("\n%d of %d failed: %s" % (len(FAILS), len(RESULTS), ", ".join(FAILS)))
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if FAILS else 0)
