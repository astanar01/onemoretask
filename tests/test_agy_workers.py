"""Antigravity workers of a Claude card (agy_worker.py), the brief lines, worker rows, tokens, the lead's settings.

Run: python3 tests/test_agy_workers.py  (stdlib only; a fresh temp HOME and board, a fake agy; never the real one).
Each check is named after its spec point (plan mwsc8ylwxqq3w, spec.md)."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="agy-workers-test-"))
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.environ["ONEMORETASK_NO_CLI_SETUP"] = "1"
os.makedirs(os.environ["HOME"])
GOT = os.path.join(TMP, "got.jsonl")
CHILD = os.path.join(TMP, "child.pid")
FAKE = os.path.join(TMP, "agy")
with open(FAKE, "w", encoding="utf-8") as f:
    f.write(r'''#!%s
import json, os, subprocess, sys, time
args = sys.argv[1:]
prompt = json.loads(sys.stdin.readline())["message"]["content"]
with open(%r, "a", encoding="utf-8") as g:
    g.write(json.dumps({"argv": args, "prompt": prompt, "cwd": os.getcwd()}) + "\n")
def out(e):
    print(json.dumps(e), flush=True)
out({"event": "init", "conversation_id": "c-1", "init": {"permission_mode": "proceed-in-sandbox"}})
def step(i, kind, state, **kw):
    out({"event": "step_update", "step_update": {"conversation_id": "c-1", "step_index": i, "state": state, "step_type": kind, **kw}})
if prompt.startswith("ERROR"):
    out({"event": "result", "result": {"conversation_id": "c-1", "status": "ERROR", "response": "", "error": "model not found"}})
    sys.exit(1)
if prompt.startswith("EMPTY"):
    out({"event": "result", "result": {"conversation_id": "c-1", "status": "SUCCESS", "response": ""}})
    sys.exit(0)
if prompt.startswith("SLEEP"):
    step(1, "tool", "ACTIVE", tool_name="run_command", tool_info={"name": "run_command", "parameters": {"CommandLine": "sleep 30"}})
    with open(%r, "w") as g:
        g.write(str(subprocess.Popen(["sleep", "60"]).pid))
    time.sleep(60)
step(1, "tool", "DONE", tool_name="run_command", tool_info={"name": "run_command", "parameters": {"CommandLine": "ls"}, "output": "x"})
step(2, "agent_response", "ACTIVE", text_delta="DONE: " + prompt.splitlines()[0])
step(2, "agent_response", "DONE", text_delta="", usage={"input_tokens": 100, "output_tokens": 7, "thinking_tokens": 0, "cache_read_tokens": 40, "total_tokens": 107})
out({"event": "result", "result": {"conversation_id": "c-1", "status": "SUCCESS", "response": "DONE: " + prompt.splitlines()[0]}})
''' % (sys.executable, GOT, CHILD))
os.chmod(FAKE, 0o755)
os.environ["ONEMORETASK_AGY"] = FAKE
ENV = dict(os.environ)
sys.path.insert(0, ROOT)
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


import server  # noqa: E402

proj = os.path.join(TMP, "proj")
os.makedirs(proj)
subprocess.run(["git", "-C", proj, "init", "-q"], check=True)
p = server.Project(proj)
os.makedirs(p.board)
HELPER = os.path.join(ROOT, "agy_worker.py")
WF = os.path.join(p.board, "agent_reports", "agy-workers")


def worker(task, prompt, *extra, timeout=60):
    return subprocess.run([sys.executable, HELPER, "--board", p.board, task, "--model", "agy:gemini-3.8-flash-low",
                           "--name", "Hello", *extra], input=prompt, capture_output=True, text=True, cwd=proj,
                          env=ENV, timeout=timeout)


def calls():
    return [json.loads(x) for x in open(GOT, encoding="utf-8")]


def test_s40_delegate_brief_and_pin():
    cmd = server.script_cmd(p, "t1", "agy_worker.py")
    text = "\n".join(server.delegate_lines("agy:gemini-3.8-flash-low", "R", False, worker=cmd))
    assert f'{cmd} --model agy:gemini-3.8-flash-low --name "<short label>" --prompt-file <file>' in text, text
    assert "run_in_background" in text and 'model: "' not in text and "Antigravity worker" in text, text
    assert "<<" not in text and "Write tool" in text, "S40: one plain line, the prompt in a file (S42 measured)"
    assert "nothing chained before or after it" in text, "S40: a chained command stays in the sandbox (S54(d) run)"
    ro = "\n".join(server.delegate_lines("agy:gemini-3.8-flash-low", "R", True, worker=cmd))
    assert "--read-only --prompt-file <file>" in ro, ro
    cx = "\n".join(server.delegate_lines("gpt-5.5", "R", False, worker=server.script_cmd(p, "t1", "codex_worker.py")))
    assert "<<'EOF'" in cx, "Codex workers keep their heredoc"
    rem = server.delegate_reminder({"delegate": True, "subagentModel": "agy:gemini-3.8-flash-low",
                                    "agent": {"model": "opus"}}, cmd)
    assert "--model agy:gemini-3.8-flash-low" in rem and "Antigravity workers" in rem and "Agent call" not in rem, rem
    assert "--prompt-file <file>" in rem and "<<" not in rem, rem
    assert server.pinned_worker({"delegate": True, "subagentModel": "agy:gemini-3.8-flash-low"}, "opus") == ""
    assert server.worker_model("agy:gemini-3.8-flash-low", False) == "", "an AG pick reached Claude's subagent model"
    state = {"tasks": [], "columns": []}
    t = {"id": "t1", "title": "x", "delegate": True, "subagentModel": "agy:gemini-3.8-flash-low"}
    assert "agy_worker.py" in server.build_prompt(p, state, t, None, "opus")


def test_s41a_argv_and_cwd():
    open(GOT, "w").close()
    r = worker("t1", "make hello\nmore")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    c = calls()[-1]
    a = c["argv"]
    for flag in ("-p=", "--input-format", "--output-format", "--model", "--mode", "--sandbox", "--settings"):
        assert flag in a, (flag, a)
    assert a[a.index("--model") + 1] == "gemini-3.8-flash-low" and "--dangerously-skip-permissions" not in a, a
    assert os.path.realpath(c["cwd"]) == os.path.realpath(proj) and c["prompt"] == "make hello\nmore", c


def test_s41a_cwd_is_the_leads_worktree():
    """A lead on a worktree card runs the helper from its worktree with no --cwd: Antigravity must work there, not in
    the main checkout (review finding 1). A lead shell outside the project falls back to the project folder."""
    wt = os.path.join(proj, ".claude", "worktrees", "card")
    os.makedirs(wt, exist_ok=True)
    outside = tempfile.mkdtemp()
    for where, want in ((wt, wt), (outside, proj)):
        open(GOT, "w").close()
        r = subprocess.run([sys.executable, HELPER, "--board", p.board, "t1", "--model", "agy:gemini-3.8-flash-low",
                            "--name", "Hello"], input="hi", capture_output=True, text=True, cwd=where, env=ENV,
                           timeout=60)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        got = calls()[-1]["cwd"]
        assert os.path.realpath(got) == os.path.realpath(want), (where, got)


def test_s41a_prompt_file():
    open(GOT, "w").close()
    pf = os.path.join(TMP, "part.md")
    open(pf, "w").write("from a file\nline two")
    r = subprocess.run([sys.executable, HELPER, "--board", p.board, "t1", "--model", "agy:gemini-3.8-flash-low",
                        "--name", "Hello", "--prompt-file", pf], capture_output=True, text=True, cwd=proj, env=ENV,
                       timeout=60, stdin=subprocess.DEVNULL)
    assert r.returncode == 0 and r.stdout.strip() == "DONE: from a file", (r.returncode, r.stdout, r.stderr)
    assert calls()[-1]["prompt"] == "from a file\nline two", calls()[-1]


def test_s41b_files():
    names = sorted(os.listdir(WF))
    exts = {n.rsplit(".", 1)[1] for n in names if n.startswith("t1-")}
    assert {"json", "jsonl", "err"} <= exts, names
    meta = json.load(open(os.path.join(WF, [n for n in names if n.endswith(".json")][0]), encoding="utf-8"))
    assert meta["task"] == "t1" and meta["name"] == "Hello" and meta["model"] == "agy:gemini-3.8-flash-low", meta
    assert meta["exit"] == 0 and meta["log"].endswith(".jsonl"), meta


def test_s41c_answer_printed():
    r = worker("t1", "say hi")
    assert r.returncode == 0 and r.stdout.strip() == "DONE: say hi", (r.returncode, r.stdout, r.stderr)


def test_s41d_failure_exits_nonzero():
    r = worker("t1", "ERROR now")
    assert r.returncode != 0 and "ERROR: model not found" in r.stderr, (r.returncode, r.stderr)
    r = worker("t1", "EMPTY now")
    assert r.returncode != 0 and "Antigravity stopped without an answer" in r.stderr, (r.returncode, r.stderr)


def test_s41e_sigterm_ends_group():
    if os.path.exists(CHILD):
        os.remove(CHILD)
    proc = subprocess.Popen([sys.executable, HELPER, "--board", p.board, "t9", "--model", "agy:gemini-3.8-flash-low",
                             "--name", "Sleeper"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, cwd=proj, env=ENV)
    proc.stdin.write("SLEEP please")
    proc.stdin.close()
    end = time.time() + 15
    while not os.path.exists(CHILD) and time.time() < end:
        time.sleep(0.05)
    child = int(open(CHILD).read())
    proc.send_signal(signal.SIGTERM)
    proc.wait(15)
    time.sleep(0.5)
    try:
        os.kill(child, 0)
        alive = True
    except ProcessLookupError:
        alive = False
    assert not alive, "the command the worker's agy started is still running"


def test_s41f_read_only():
    open(GOT, "w").close()
    obs = os.path.join(proj, "skill-observations")
    os.makedirs(obs, exist_ok=True)
    r = worker("t1", "look only", "--read-only")
    assert r.returncode == 0, (r.returncode, r.stderr)
    c = calls()[-1]
    allow = json.loads(c["argv"][c["argv"].index("--settings") + 1])["permissions"]["allow"]
    assert os.path.realpath(c["cwd"]) != os.path.realpath(proj), c["cwd"]
    assert f"read_file({os.path.realpath(proj)})" in allow and f"write_file({os.path.realpath(p.board)})" in allow, allow
    assert not any(x.startswith("write_file(") and ".git" in x for x in allow), allow


def test_s42_lead_settings():
    state = server.load_tasks(p) if os.path.exists(p.data) else {"columns": [], "tasks": []}
    state["tasks"] = [{"id": "ta", "title": "a", "column": "todo", "delegate": True,
                       "subagentModel": "agy:gemini-3.8-flash-low"},
                      {"id": "tb", "title": "b", "column": "todo", "delegate": True, "subagentModel": "sonnet"}]
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump(state, f)
    s = json.loads(server.inbox_settings(p, "ta", ""))
    sb = s.get("sandbox") or {}
    want = server.script_cmd(p, "ta", "agy_worker.py") + " *"
    assert want in (sb.get("excludedCommands") or []), s
    assert "network" not in sb and "enabled" not in sb, ("only the excluded command is needed (S42 measured)", s)
    s2 = json.loads(server.inbox_settings(p, "tb", ""))
    assert "agy_worker.py" not in json.dumps(s2), s2


def test_s43_worker_rows_and_tokens():
    import agy
    rows = agy.worker_rows(p.board, "t1")
    assert rows and all(r["cli"] == "antigravity" and r["type"] == "agy:gemini-3.8-flash-low" for r in rows), rows
    ok = [r for r in rows if r["finished"]]
    assert ok and ok[0]["tokens"] == 147, ok
    t = server.tokens([], (p.board, "t1"))
    assert t["count"] >= len(rows) and t["subagents"][0] >= 100, t


def test_s44_worker_model_on_agy_cards():
    assert server.worker_model("agy:gemini-3.8-flash-low", False, "antigravity") == "agy:gemini-3.8-flash-low"
    assert server.worker_model("gpt-5.5", False, "antigravity") == ""
    assert server.worker_model("opus", False, "antigravity") == ""
    text = "\n".join(server.delegate_lines("agy:gemini-3.8-flash-low", "R", False, cli="antigravity"))
    assert "Run your subagents on gemini-3.8-flash-low." in text and "agy_worker.py" not in text, text
    text = "\n".join(server.delegate_lines("opus", "R", False, cli="antigravity"))
    assert "Run your subagents on" not in text and "own model" in text, text


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            run(fn)
    print("\n%d of %d failed: %s" % (len(FAILS), len(RESULTS), ", ".join(FAILS)))
    sys.exit(1 if FAILS else 0)
