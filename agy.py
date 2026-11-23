"""Google's Antigravity CLI (`agy`) as a third session backend: the board runs `agy -p` itself, one process per turn.

Named agy.py, not antigravity.py: Python's standard library has an `antigravity` module that opens a web browser.

Each session (a "conversation") lives in <board>/agent_reports/agy/:
- <task_id>-<epoch ms>.jsonl          stdout of every turn (stream-json: init, step_update, result)
- <task_id>-<epoch ms>.err            stderr of every turn
- <task_id>-<epoch ms>-<turn>.agy.log agy's own log of that turn (--log-file truncates, so one per turn)
- <conversation_id>.json              the meta: task, name, model, cwd, settings, log, err, pid, turns, turn_offset, exit

Measured on agy 1.5.14 (plan mwsc8ylwxqq3w, agy-facts.md "T1 answers"):
- The prompt goes on stdin as one stream-json line, {"event": "user", "message": {"content": ...}} (`-p` with no text
  is refused); the turn ends when stdin closes.
- --dangerously-skip-permissions overrides the sandbox, so the board never passes it. Instead: --mode accept-edits,
  --sandbox and --settings with an allow list (settings()). A tool call that needs a yes is auto-denied and ends the
  turn with an empty answer.
- step_update.usage is per step and input_tokens leaves out cache reads; result.usage is a running total of the whole
  conversation, so tokens are the sum of the steps.
- MCP calls are tool steps named call_mcp_tool with ServerName / ToolName / Arguments.
- Exit 0 does not mean success (Google issue #902): the state comes from the last turn's result line.
"""
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time

import procs
from procs import DIGEST_BLOCK, DIGEST_HEAD, DIGEST_TAIL, POSIX, _events, _pid_alive, _pid_start, _spawn  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
PREFIX = "agy:"
SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}")  # server.MODEL
LEVELS = ("-low", "-medium", "-high", "-xhigh", "-max")  # a slug ending in one takes no --effort
START_WAIT = 30  # seconds to wait for a turn's init line
MODELS_TTL = 3600  # a model list younger than this is used as is
MODELS_RETRY = 600  # after a failed refresh, the next one waits this long
NOT_LOGGED_IN = "Antigravity is not logged in"
SANDBOX_OFF = "Antigravity's sandbox is not on, so the board stopped it"
DENIED = "Antigravity stopped: a step needed a yes it could not get"
NO_ANSWER = "Antigravity stopped without an answer"
NO_RESULT = "Antigravity ended without a result"
SAFE_MODE = "proceed-in-sandbox"
PROCS = {}  # conversation id -> Popen of its running (or not yet reaped) turn


def _resolve_cmd():
    if os.environ.get("ONEMORETASK_AGY"):
        return [os.environ["ONEMORETASK_AGY"]]
    return [shutil.which("agy") or os.path.expanduser("~/.local/bin/agy")]


AGY_CMD = _resolve_cmd()
_now = time.time


def is_model(model):
    return (model or "").startswith(PREFIX)


def slug(model):
    return model[len(PREFIX):] if is_model(model) else (model or "")


def installed():
    return os.path.isfile(AGY_CMD[0]) and os.access(AGY_CMD[0], os.X_OK)


# ---------------------------------------------------------------- the model list (memory only)
_models_lock = threading.Lock()
_models = None  # [{id, name}] from the last good `agy models`, None before one
_models_at = 0.0  # when that list was read
_models_tried = 0.0  # when the last refresh failed
_models_err = ""
_refreshing = False


def logged_in():
    g = os.path.join(os.path.expanduser("~"), ".gemini")
    return any(os.path.exists(os.path.join(g, n)) for n in ("oauth_creds.json", "google_accounts.json"))


def _parse_models(text):
    out = []
    for line in text.splitlines():  # slug TAB label (agy 1.5.14); a line with no tab is not a model line
        parts = line.strip().split("\t", 1)
        if len(parts) == 2 and SLUG.fullmatch(parts[0].strip()):
            parts[0] = parts[0].strip()
            out.append({"id": PREFIX + parts[0], "name": (parts[1].strip() if len(parts) > 1 else "") or parts[0]})
    return out


def _refresh():
    global _models, _models_at, _models_tried, _models_err, _refreshing
    try:
        r = subprocess.run(AGY_CMD + ["models"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        found = _parse_models(r.stdout) if r.returncode == 0 else []
        if found:
            _models, _models_at, _models_err = found, _now(), ""
        else:
            _models_tried = _now()
            _models_err = (r.stderr or "").strip()[-300:].strip() or f"agy models gave no model (exit {r.returncode})"
    except (OSError, subprocess.SubprocessError) as e:
        _models_tried, _models_err = _now(), str(e)[-300:]
    finally:
        _refreshing = False


def models():
    """The Antigravity models, never waiting for agy: a list younger than MODELS_TTL is returned as is; an older or
    missing one is returned as is (empty when missing) and starts one background refresh."""
    global _refreshing, _models_err
    if not logged_in():
        _models_err = NOT_LOGGED_IN
        return list(_models or [])
    with _models_lock:
        now = _now()
        stale = _models is None or now - _models_at >= MODELS_TTL
        if stale and not _refreshing and installed() and now - _models_tried >= MODELS_RETRY:
            _refreshing = True
            threading.Thread(target=_refresh, daemon=True).start()
    return list(_models or [])


def refreshing():
    return _refreshing


def models_error():
    return _models_err


# ---------------------------------------------------------------- folders, settings, argv
def _dir(board):
    return os.path.join(board, "agent_reports", "agy")


def folder(board):
    path = _dir(board)
    os.makedirs(path, exist_ok=True)
    return path


def worker_folder(board):
    path = os.path.join(board, "agent_reports", "agy-workers")
    os.makedirs(path, exist_ok=True)
    return path


def is_log(path):
    parent = os.path.dirname(os.path.normpath(path))
    return (str(path).endswith(".jsonl") and os.path.basename(parent) == "agy"
            and os.path.basename(os.path.dirname(parent)) == "agent_reports")


def answer_dir(board, task_id):
    """The working folder of an answer-only session: an empty one outside the project, since Antigravity may always
    write in its working folder."""
    return os.path.join(tempfile.gettempdir(), "task-board-answer", "agy-" + task_id)


def _git(cwd, *args):
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def settings(cwd, read=(), write=(), git=True):
    """The --settings of a turn: the terminal sandbox on, sandboxed commands run without asking, and the allow list:
    the repo's git dir (a worktree commit writes there), the card worktrees when the repo top is not `cwd`, the board
    app's folder (report.py) readable, MCP tools and web reads, then `read` / `write` extras."""
    allow = []
    if git:
        common = _git(cwd, "rev-parse", "--git-common-dir")
        if common:
            allow.append(f"write_file({os.path.realpath(os.path.join(cwd, common))})")
        top = _git(cwd, "rev-parse", "--show-toplevel")
        if top and os.path.realpath(top) != os.path.realpath(cwd):
            allow.append(f"write_file({os.path.realpath(os.path.join(top, '.claude', 'worktrees'))})")
    allow.append(f"read_file({os.path.realpath(HERE)})")
    # The global skills: ~/.gemini/config/skills links there, and a skill's helper script (the observer's) runs in the
    # sandbox, which shows nothing it was not given (real run, S81).
    claude = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")
    allow.append(f"read_file({os.path.realpath(os.path.join(claude, 'skills'))})")
    allow += ["mcp(*)", "read_url(*)"]
    allow += [f"read_file({os.path.realpath(x)})" for x in read]
    allow += [f"write_file({os.path.realpath(x)})" for x in write]
    return {"enableTerminalSandbox": True, "toolPermission": SAFE_MODE, "permissions": {"allow": allow}}


def argv(model, settings_json, agy_log, effort="", conversation=None):
    s = slug(model)
    a = AGY_CMD + ["-p=", "--input-format", "stream-json", "--output-format", "stream-json", "--model", s,
                   "--mode", "accept-edits", "--sandbox", "--settings", settings_json, "--log-file", agy_log]
    if effort and not s.endswith(LEVELS):
        a += ["--effort", effort]
    if conversation:
        a += ["--conversation", conversation]
    return a


def stdin_line(prompt):
    return json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"


# ---------------------------------------------------------------- meta + process state
def _meta_path(board, sid):
    return os.path.join(_dir(board), sid + ".json")


def _read_meta(board, sid):
    try:
        with open(_meta_path(board, sid), encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else None
    except (OSError, ValueError):
        return None


def _write_meta(board, meta):
    procs.write_json(_meta_path(board, meta["id"]), meta)


def _alive(meta):
    """(alive, exit code if the turn's Popen just finished else None)."""
    proc = PROCS.get(meta.get("id"))
    if proc is not None:
        code = proc.poll()
        return code is None, code
    return _pid_alive(meta.get("pid"), meta.get("pid_start")) if meta.get("exit") is None else False, None


def _reap(board, meta):
    alive, code = _alive(meta)
    if alive:
        return True
    if code is not None:
        PROCS.pop(meta["id"], None)
        meta["exit"] = code
        _write_meta(board, meta)
    elif meta.get("exit") is None:  # gone with no Popen (board restarted): its result line tells the rest
        meta["exit"] = 0
        _write_meta(board, meta)
    return False


def _from(path, offset):
    try:
        with open(path, "rb") as f:
            f.seek(offset or 0)
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def _turn(log, offset=0):
    """The events of the last turn from `offset` (after its last init line)."""
    turn = []
    for e in _events(log, offset):
        if e.get("event") == "init":
            turn = []
        turn.append(e)
    return turn


def _answer(turn):
    res = next((e["result"] for e in reversed(turn) if e.get("event") == "result" and isinstance(e.get("result"), dict)),
               None)
    if res is not None and "response" in res:
        return str(res.get("response") or "")
    return "".join(str(s.get("text_delta") or "") for s in _steps(turn) if s.get("step_type") == "agent_response")


def _steps(events):
    return [e["step_update"] for e in events if e.get("event") == "step_update" and isinstance(e.get("step_update"), dict)]


def _error_text(err):
    if isinstance(err, dict):
        return str(err.get("message") or "")
    return str(err or "")


def turn_state(meta):
    """(state, detail) of a turn that is no longer running, by the spec's first-match table."""
    if meta.get("stopped"):
        return "blocked", meta["stopped"]
    turn = _turn(meta.get("log", ""), meta.get("turn_offset") or 0)
    err = _from(meta.get("err", ""), meta.get("err_offset") or 0).strip()
    res = next((e["result"] for e in reversed(turn) if e.get("event") == "result" and isinstance(e.get("result"), dict)),
               None)
    code = meta.get("exit")
    if res is not None and res.get("status") != "SUCCESS":
        return "blocked", f"{res.get('status')}: {_error_text(res.get('error'))}"
    if code not in (None, 0):
        return "blocked", err[-300:] or f"agy exited {code}"
    if res is None:
        last = (err.splitlines() or [""])[-1].strip()
        return "blocked", NO_RESULT + (f": {last}" if last else "")
    if not _answer(turn).strip():
        return "blocked", DENIED if "auto-denied" in err else NO_ANSWER
    return "idle", ""


def _record(board, meta):
    alive = _reap(board, meta)
    state, detail = ("working", "") if alive else turn_state(meta)
    s = {"id": meta["id"], "sessionId": meta["id"], "cli": "antigravity", "name": meta.get("name", ""),
         "model": meta.get("model", ""), "cwd": meta.get("cwd", ""), "log": meta.get("log", ""),
         "err": meta.get("err", ""), "task": meta.get("task", ""), "state": state, "detail": detail}
    if alive:
        s["status"] = "busy"
        s["pid"] = meta.get("pid")
    return s


def sessions(board):
    d = _dir(board)
    if not os.path.isdir(d):
        return []
    out = []
    for n in sorted(os.listdir(d)):
        if n.endswith(".json"):
            meta = _read_meta(board, n[:-len(".json")])
            if meta and meta.get("id") and not meta.get("removed"):
                out.append(_record(board, meta))
    return out


def session(board, sid):
    meta = _read_meta(board, sid) if sid else None
    return _record(board, meta) if meta and not meta.get("removed") else None


def is_session(board, sid):
    return session(board, sid) is not None


# ---------------------------------------------------------------- running turns
def _wait_init(proc, log, offset):
    """The turn's init event, read from the log after `offset`; None when the process exits first or START_WAIT runs
    out."""
    end = time.time() + START_WAIT
    while True:
        done = proc.poll() is not None
        for e in _events(log, offset):
            if e.get("event") == "init" and e.get("conversation_id"):
                return e
        if done or time.time() > end:
            return None
        time.sleep(0.1)


def _fail(proc, err, offset=0):
    if proc.poll() is None:
        procs._kill(proc)
    every = _from(err, offset).strip().splitlines()
    lines = every[-20:]
    first_err = next((x for x in every if x.startswith("error:")), None)
    if first_err and first_err not in lines:  # an unknown model lists every model after its error line
        lines = [first_err] + lines
    msg = "\n".join(lines)[-2000:].strip() or f"agy exited {proc.returncode}"
    if "authentication" in msg.lower():
        msg += "\nLog in to Antigravity once in a terminal."
    return RuntimeError(msg)


def _check_mode(board, meta, proc, init):
    """Stop a turn whose init says the safe mode is off (the sandbox missing, or a skip flag from elsewhere)."""
    mode = (init.get("init") or {}).get("permission_mode")
    if mode != SAFE_MODE:
        meta["stopped"] = SANDBOX_OFF  # saved first: a poll that sees the process gone must already read why
        _write_meta(board, meta)
        procs._kill(proc, 2)


def start(board, task_id, name, prompt, model, cwd, effort="", writable=None):
    """Launch a new Antigravity session for a card; returns its conversation id once agy has printed it.
    `writable`: an answer-only card's only write folders; it then works from answer_dir, with `cwd` readable."""
    base = os.path.join(folder(board), f"{task_id}-{int(time.time() * 1000)}")
    log, err = base + ".jsonl", base + ".err"
    if writable is not None:
        project, cwd = cwd, answer_dir(board, task_id)
        os.makedirs(cwd, exist_ok=True)
        st = settings(cwd, read=[project], write=writable, git=False)
    else:
        st = settings(cwd)
    sj = json.dumps(st, separators=(",", ":"))
    agy_log = base + "-1.agy.log"
    proc = _spawn(argv(model, sj, agy_log, effort), cwd, stdin_line(prompt), log, err)
    init = _wait_init(proc, log, 0)
    if not init:
        raise _fail(proc, err)
    sid = init["conversation_id"]
    PROCS[sid] = proc
    meta = {"id": sid, "task": task_id, "name": name, "model": model, "effort": effort, "cwd": cwd, "base": base,
            "log": log, "err": err, "agy_log": agy_log, "settings": sj, "started": time.time(), "pid": proc.pid,
            "pid_start": _pid_start(proc.pid), "turns": 1, "turn_offset": 0, "err_offset": 0, "exit": None,
            "removed": False, **({"lock": True} if writable is not None else {})}
    _write_meta(board, meta)
    _check_mode(board, meta, proc, init)
    return sid


def resume(board, sid, message):
    """Send a reply to an idle Antigravity session (a new turn appended to the same log); returns the id."""
    meta = _read_meta(board, sid)
    if not meta:
        raise KeyError(sid)
    if _reap(board, meta):
        raise PermissionError("Antigravity is still working")
    log, err = meta["log"], meta["err"]
    offset = os.path.getsize(log) if os.path.exists(log) else 0
    err_offset = os.path.getsize(err) if os.path.exists(err) else 0
    turn = int(meta.get("turns") or 1) + 1
    base = meta.get("base") or log[:-len(".jsonl")]
    agy_log = f"{base}-{turn}.agy.log"
    if meta.get("cwd"):
        os.makedirs(meta["cwd"], exist_ok=True)  # an answer-only temp folder: a restart may have emptied it
    proc = _spawn(argv(meta.get("model"), meta.get("settings") or json.dumps(settings(meta.get("cwd") or ".")),
                       agy_log, meta.get("effort"), sid), meta.get("cwd") or None, stdin_line(message), log, err)
    PROCS[sid] = proc
    meta.update(pid=proc.pid, pid_start=_pid_start(proc.pid), turns=turn, exit=None, turn_offset=offset,
                err_offset=err_offset, agy_log=agy_log, started_turn=time.time(), stopped=None)
    _write_meta(board, meta)
    init = _wait_init(proc, log, offset)
    if not init:
        if proc.poll() not in (None, 0):
            _reap(board, meta)
            raise _fail(proc, err, err_offset)
        if proc.poll() is None:  # a slow start: the reply stands, and the sandbox check runs once its init comes
            threading.Thread(target=_late_check, args=(board, sid, proc, log, offset), daemon=True).start()
    else:
        _check_mode(board, meta, proc, init)
    return sid


def _late_check(board, sid, proc, log, offset):
    """Wait for the init of a turn that outran START_WAIT, for as long as it runs, then check its mode."""
    while proc.poll() is None:
        init = next((e for e in _events(log, offset) if e.get("event") == "init" and e.get("conversation_id")), None)
        if init:
            meta = _read_meta(board, sid)
            if meta and not meta.get("removed"):
                _check_mode(board, meta, proc, init)
            return
        time.sleep(0.2)


def remove(board, sid, grace=5):
    """Stop the session if it works (SIGTERM to its process group, SIGKILL after `grace` s) and hide it; its files
    stay (the recap lists the log)."""
    meta = _read_meta(board, sid)
    if not meta:
        return
    proc = PROCS.pop(sid, None)
    if proc is not None:
        if proc.poll() is None:
            procs._kill(proc, grace)
        meta["exit"] = proc.returncode
    elif meta.get("exit") is None and _pid_alive(meta.get("pid"), meta.get("pid_start")):
        procs.kill_pid_group(meta["pid"], meta.get("pid_start"), grace)
    meta["removed"] = True
    _write_meta(board, meta)


def turn_ended_at(meta_or_session):
    """When the last turn ended (the log's mtime), None while it runs."""
    s = meta_or_session or {}
    if s.get("status") == "busy" or ("sessionId" not in s and _alive(s)[0]):
        return None
    try:
        return os.path.getmtime(s.get("log") or "")
    except OSError:
        return None


# ---------------------------------------------------------------- reading the log
def last_answer(log_path, reply_text=None):
    """The last turn's answer text ('' if none)."""
    return _answer(_turn(log_path)).strip()


def _n(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


_usage_memo = {}


def usage(log_path):
    """[input, cache write (none), cache read, output + thinking] summed over every step of every turn."""
    try:
        st = os.stat(log_path)
    except OSError:
        return [0, 0, 0, 0]
    key = (st.st_mtime, st.st_size)
    memo = _usage_memo.get(log_path)
    if memo and memo[0] == key:
        return list(memo[1])
    total = [0, 0, 0, 0]
    for s in _steps(_events(log_path)):
        u = s.get("usage")
        if isinstance(u, dict):
            total[0] += _n(u.get("input_tokens"))
            total[2] += _n(u.get("cache_read_tokens"))
            total[3] += _n(u.get("output_tokens")) + _n(u.get("thinking_tokens"))
    _usage_memo[log_path] = (key, total)
    return list(total)


def context(log_path):
    """No context size is known for Antigravity models (not in init, not in `agy models`): no context bar."""
    return None


def _subagent_steps(log_path):
    out = {}
    for s in _steps(_events(log_path)):
        if s.get("step_type") != "subagent":
            continue
        for a in (s.get("subagent_info") or {}).get("subagents") or []:
            if isinstance(a, dict) and a.get("conversation_id"):
                row = out.setdefault(a["conversation_id"], {"role": a.get("role") or a.get("type_name") or "Subagent",
                                                             "state": s.get("state")})
                row["state"] = s.get("state") or row["state"]
    return out


def subagents(log_path):
    """The session's subagents as server.subagents rows; agy gives no model, tokens or times for them."""
    try:
        at = os.path.getmtime(log_path)
    except OSError:
        at = time.time()
    return [{"id": sid, "name": a["role"], "type": "—", "started": at, "updated": at, "finished": a["state"] == "DONE",
             "action": "", "skills": [], "tokens": 0, "cli": "antigravity"}
            for sid, a in _subagent_steps(log_path).items()]


def subagent_usage(log_path):
    """agy's result usage leaves subagents out and their steps are not in the stream: zeros, with their count."""
    return [0, 0, 0, 0], len(_subagent_steps(log_path))


_FILE_KEYS = ("Path", "AbsolutePath", "TargetFile", "File", "FilePath", "path", "file_path")


def _step_row(s):
    """A tool step as the row fields {server, tool, what, kind}."""
    name = str(s.get("tool_name") or "")
    info = s.get("tool_info") if isinstance(s.get("tool_info"), dict) else {}
    params = info.get("parameters") if isinstance(info.get("parameters"), dict) else {}
    if name == "call_mcp_tool":
        return {"server": str(params.get("ServerName") or ""), "tool": str(params.get("ToolName") or ""),
                "what": procs.mcp_what(params.get("Arguments")), "kind": "mcp"}
    if s.get("step_type") == "subagent":
        roles = [a.get("role") for a in (s.get("subagent_info") or {}).get("subagents") or [] if isinstance(a, dict)]
        return {"server": "", "tool": name or "subagent", "what": procs.mcp_what({"query": ", ".join(r for r in roles if r)}),
                "kind": "tool"}
    if "CommandLine" in params:
        what = procs.command_what(params.get("CommandLine"))
    else:
        key = next((k for k in _FILE_KEYS if params.get(k)), None)
        what = procs.path_what([params[key]]) if key else procs.mcp_what(params)
    return {"server": "", "tool": name, "what": what, "kind": "tool"}


_mcp_seen = {}
_mcp_logs_read = set()
_mcp_use_memo = {}


def mcp_uses(log_path):
    """Each step in an Antigravity log (MCP calls, commands, file tools, subagents, its own words), oldest first, in
    server.mcp_uses's row shape. Step indexes go on across turns of one conversation; the turn is in the id anyway."""
    try:
        st = os.stat(log_path)
    except OSError:
        return []
    memo = _mcp_use_memo.get(log_path)
    if memo and memo[0] == (st.st_mtime, st.st_size):
        return [dict(r) for r in memo[1]]
    now = st.st_mtime if log_path not in _mcp_logs_read else time.time()
    _mcp_logs_read.add(log_path)
    rows, said, turn, name = {}, {}, 0, os.path.basename(log_path)
    for e in _events(log_path):
        if e.get("event") == "init":
            turn += 1
            continue
        if e.get("event") != "step_update" or not isinstance(e.get("step_update"), dict):
            continue
        s = e["step_update"]
        kind, state, idx = s.get("step_type"), s.get("state"), s.get("step_index")
        key = (log_path, turn, idx)
        if kind == "agent_response":
            said[key] = said.get(key, "") + str(s.get("text_delta") or "")
            if state == "DONE":
                text = procs.say_what(said[key])
                if text:
                    rows[key] = {"id": f"{name}:{turn}:{idx}", "server": "", "tool": "", "what": text, "kind": "say",
                                 "at": _mcp_seen.setdefault(key, now), "state": "done", "sub": False}
            continue
        if kind not in ("tool", "subagent"):
            continue
        step = _step_row(s)
        row = rows.get(key)
        if not row:
            row = rows[key] = {"id": f"{name}:{turn}:{idx}", **step, "at": _mcp_seen.setdefault(key, now),
                               "state": "open", "sub": False}
        elif step["what"] and not row["what"]:
            row["what"] = step["what"]
        info = s.get("tool_info") if isinstance(s.get("tool_info"), dict) else {}
        if state == "ERROR" or info.get("error"):
            row["state"] = "failed"
        elif state == "DONE":
            row["state"] = "done"
    found = sorted(rows.values(), key=lambda r: r["at"])  # stable: rows first seen together keep the log's order
    _mcp_use_memo[log_path] = ((st.st_mtime, st.st_size), found)
    return [dict(r) for r in found]


def digest(log_path):
    """The session for a recap: one 'TURN n' per turn, Antigravity's words, one line per action."""
    out, turn, said = [], 0, {}
    for e in _events(log_path):
        if e.get("event") == "init":
            turn += 1
            out.append(f"TURN {turn}")
            continue
        if e.get("event") != "step_update" or not isinstance(e.get("step_update"), dict):
            continue
        s = e["step_update"]
        kind, idx = s.get("step_type"), s.get("step_index")
        if kind == "agent_response":
            said[(turn, idx)] = said.get((turn, idx), "") + str(s.get("text_delta") or "")
            text = said[(turn, idx)].strip()
            if s.get("state") == "DONE" and text:
                out.append("ANTIGRAVITY: " + (text[:DIGEST_BLOCK] + " […]" if len(text) > DIGEST_BLOCK else text))
        elif kind in ("tool", "subagent") and s.get("state") in ("DONE", "ERROR"):
            r = _step_row(s)
            if r["kind"] == "mcp":
                out.append("  · mcp: " + "/".join(x for x in (r["server"], r["tool"]) if x))
            elif r["tool"] == "run_command":
                out.append("  · " + r["what"])
            else:
                out.append("  · " + r["tool"] + (f" {r['what']}" if r["what"] else ""))
    text = "\n\n".join(out)
    if len(text) > DIGEST_HEAD + DIGEST_TAIL:
        text = text[:DIGEST_HEAD] + "\n\n[… middle of the session left out …]\n\n" + text[-DIGEST_TAIL:]
    return text


# ---------------------------------------------------------------- Antigravity workers of a Claude session (agy_worker.py)
def _worker_metas(board, task_id, since=0):
    d = os.path.join(board, "agent_reports", "agy-workers")
    try:
        names = sorted(os.listdir(d))
    except OSError:
        return []
    out = []
    for n in names:
        if not (n.startswith(task_id + "-") and n.endswith(".json")):
            continue
        try:
            with open(os.path.join(d, n), encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(meta, dict) and meta.get("task") == task_id and (meta.get("started") or 0) >= (since or 0):
            out.append(meta)
    return out


def worker_rows(board, task_id, since=0):
    """The card's Antigravity workers started at/after `since`, as subagent rows plus "cli": "antigravity"."""
    rows = []
    for meta in _worker_metas(board, task_id, since):
        log = meta.get("log") or ""
        try:
            updated = os.path.getmtime(log)
        except OSError:
            updated = meta.get("started") or 0
        code = meta.get("exit")
        running = code is None and POSIX and _pid_alive(meta.get("pid"), meta.get("pid_start"))
        state = "working" if running else turn_state({**meta, "exit": code if code is not None else 0})[0]
        steps = [r for r in mcp_uses(log) if r["kind"] != "say"] if log else []
        u = usage(log)
        rows.append({"id": os.path.basename(log).rsplit(".", 1)[0], "name": meta.get("name") or "Antigravity worker",
                     "type": meta.get("model") or "antigravity", "started": meta.get("started") or updated,
                     "updated": updated, "finished": state == "idle", "stopped": state == "blocked",
                     "action": steps[-1]["what"] if steps else "", "skills": [], "tokens": u[0] + u[2] + u[3],
                     "cli": "antigravity"})
    rows.sort(key=lambda r: r["started"])
    return rows


def worker_usage(board, task_id, since=0):
    total, metas = [0, 0, 0, 0], _worker_metas(board, task_id, since)
    for meta in metas:
        total = [a + b for a, b in zip(total, usage(meta.get("log") or ""))]
    return total, len(metas)
