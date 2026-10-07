"""OpenAI Codex CLI as a second session backend: the board runs `codex exec --json` itself.

Each Codex session (a "thread") lives in <board>/agent_reports/codex/:
- <task_id>-<epoch ms>.jsonl   the JSONL event stream of every turn (stdout; resumes append to it)
- <task_id>-<epoch ms>.err     stderr of every turn (progress text, errors)
- <thread_id>.json             the meta: task, name, model, cwd, log, err, pid, turns, exit, removed

A turn is one process: it runs while the session works and exits when the turn ends, so the state comes from
the process (alive: working; exited non-zero: blocked; else idle). A reply is `codex exec resume`.
"""
import json
import os
import shutil
import signal
import subprocess
import threading
import time

POSIX = os.name == "posix"
CODEX_CMD = [shutil.which("codex") or os.path.expanduser("~/.local/bin/codex")]
MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "codex")
FALLBACK_MODELS = [{"id": "gpt-6-astra", "name": "GPT-6-Astra"}, {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol"},
                   {"id": "gpt-5.5", "name": "GPT-5.5"}]
START_WAIT = 30  # seconds to wait for the first event of a turn
DIGEST_HEAD, DIGEST_TAIL, DIGEST_BLOCK = 30_000, 150_000, 4_000  # same as server.py
PROCS = {}  # thread id -> Popen of its running (or not yet reaped) turn
_usage_memo = {}  # log path -> ((mtime, size), [input, cache write, cache read, output])


def is_model(model):
    return (model or "").lower().startswith(MODEL_PREFIXES)


def models():
    """The OpenAI models Codex lists, from its own cache; a fixed list when there is none."""
    try:
        home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
        with open(os.path.join(home, "models_cache.json"), encoding="utf-8") as f:
            out = [{"id": m["slug"], "name": m.get("display_name") or m["slug"]}
                   for m in json.load(f)["models"] if m.get("visibility") == "list"]
        return out or list(FALLBACK_MODELS)
    except (OSError, ValueError, KeyError, TypeError):
        return list(FALLBACK_MODELS)


def folder(board):
    path = os.path.join(board, "agent_reports", "codex")
    os.makedirs(path, exist_ok=True)
    return path


def is_log(path):
    parent = os.path.dirname(os.path.normpath(path))
    return os.path.basename(parent) == "codex" and os.path.basename(os.path.dirname(parent)) == "agent_reports"


# ---------------------------------------------------------------- meta + process state
def _meta_path(board, thread_id):
    return os.path.join(folder(board), thread_id + ".json")


def _read_meta(board, thread_id):
    try:
        with open(_meta_path(board, thread_id), encoding="utf-8") as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else None
    except (OSError, ValueError):
        return None


def _write_meta(board, meta):
    path = _meta_path(board, meta["id"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=1)
    os.replace(tmp, path)


def _pid_alive(pid):
    if not pid or not POSIX:  # os.kill(pid, 0) terminates the process on Windows
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _alive(meta):
    """(alive, exit code if the turn's Popen just finished else None)."""
    proc = PROCS.get(meta.get("id"))
    if proc is not None:
        code = proc.poll()
        return code is None, code
    return _pid_alive(meta.get("pid")) if meta.get("exit") is None else False, None


def _reap(board, meta):
    """Alive or not; an exit seen now is written to the meta. Returns alive."""
    alive, code = _alive(meta)
    if alive:
        return True
    if code is not None:
        PROCS.pop(meta["id"], None)
        meta["exit"] = code
        _write_meta(board, meta)
    elif meta.get("exit") is None:  # process gone with no Popen (board restarted): assume it ended cleanly
        meta["exit"] = 0
        _write_meta(board, meta)
    return False


def _tail(path, n=300):
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - n))
            return f.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _events(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    yield e
    except OSError:
        return


def _last_turn(path):
    """The events of the last turn (after the last turn.started)."""
    turn = []
    for e in _events(path):
        if e.get("type") == "turn.started":
            turn = []
        turn.append(e)
    return turn


def _detail(meta):
    for e in reversed(_last_turn(meta.get("log", ""))):
        if e.get("type") == "turn.failed":
            msg = (e.get("error") or {}).get("message") if isinstance(e.get("error"), dict) else e.get("error")
            if msg:
                return str(msg).strip()
    return _tail(meta.get("err", ""))


def _record(board, meta):
    alive = _reap(board, meta)
    exit_code = meta.get("exit")
    blocked = not alive and exit_code not in (None, 0)
    s = {"id": meta["id"], "sessionId": meta["id"], "cli": "codex", "name": meta.get("name", ""),
         "model": meta.get("model", ""), "cwd": meta.get("cwd", ""), "log": meta.get("log", ""),
         "err": meta.get("err", ""), "task": meta.get("task", ""),
         "state": "working" if alive else "blocked" if blocked else "idle",
         "detail": _detail(meta) if blocked else ""}
    if alive:
        s["status"] = "busy"
        s["pid"] = meta.get("pid")
    return s


def sessions(board):
    out = []
    for n in sorted(os.listdir(folder(board))):
        if n.endswith(".json"):
            meta = _read_meta(board, n[:-len(".json")])
            if meta and meta.get("id") and not meta.get("removed"):
                out.append(_record(board, meta))
    return out


def session(board, thread_id):
    meta = _read_meta(board, thread_id) if thread_id else None
    return _record(board, meta) if meta and not meta.get("removed") else None


def is_session(board, thread_id):
    return session(board, thread_id) is not None


# ---------------------------------------------------------------- running turns
def _spawn(argv, cwd, text, log, err):
    """Start one turn: stdout and stderr appended to the session's files, `text` fed on stdin from a thread
    (a 50 KB prompt would fill the pipe buffer before codex reads it)."""
    with open(log, "ab") as out, open(err, "ab") as errf:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE, stdout=out, stderr=errf,
                                start_new_session=POSIX)  # the board's Ctrl-C must not stop a running turn

    def feed():
        try:
            proc.stdin.write(text.encode("utf-8"))
        except (BrokenPipeError, OSError):
            pass
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
    threading.Thread(target=feed, daemon=True).start()
    return proc


def _wait_event(proc, log, offset, want_thread):
    """Poll the log from `offset` for the turn's first event: the thread id when `want_thread`, else True.
    None when the process exits first or the wait runs out."""
    end = time.time() + START_WAIT
    while True:
        done = proc.poll() is not None
        try:
            with open(log, "rb") as f:
                f.seek(offset)
                lines = f.read().splitlines()
        except OSError:
            lines = []
        for line in lines:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not want_thread:
                return True
            if isinstance(e, dict) and e.get("type") == "thread.started" and e.get("thread_id"):
                return e["thread_id"]
        if done or time.time() > end:
            return None
        time.sleep(0.1)


def _fail(proc, err, offset=0):
    if proc.poll() is None:
        _kill(proc)
    msg = _tail_from(err, offset) or f"codex exited {proc.returncode}"
    return RuntimeError(msg)


def _tail_from(path, offset):
    """The stderr this turn wrote (after `offset`), last ~300 chars."""
    try:
        with open(path, "rb") as f:
            f.seek(offset)
            return f.read()[-300:].decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _kill(proc):
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def start(board, task_id, name, prompt, model, cwd):
    """Launch a new Codex session for a card; returns its thread id once codex has named it."""
    base = os.path.join(folder(board), f"{task_id}-{int(time.time() * 1000)}")
    log, err = base + ".jsonl", base + ".err"
    argv = CODEX_CMD + ["exec", "--json", "--skip-git-repo-check", "-s", "workspace-write"]
    if model:
        argv += ["-m", model]
    argv += ["-C", cwd, "-"]
    proc = _spawn(argv, cwd, prompt, log, err)
    thread_id = _wait_event(proc, log, 0, True)
    if not thread_id:
        raise _fail(proc, err)
    PROCS[thread_id] = proc
    _write_meta(board, {"id": thread_id, "task": task_id, "name": name, "model": model, "cwd": cwd, "log": log,
                        "err": err, "started": time.time(), "pid": proc.pid, "turns": 1, "exit": None,
                        "removed": False})
    return thread_id


def resume(board, thread_id, message):
    """Send a reply to an idle Codex session (a new turn appended to the same log); returns the thread id."""
    meta = _read_meta(board, thread_id)
    if not meta:
        raise KeyError(thread_id)
    if _reap(board, meta):
        raise PermissionError("codex is still working")
    log, err = meta["log"], meta["err"]
    offset = os.path.getsize(log) if os.path.exists(log) else 0
    err_offset = os.path.getsize(err) if os.path.exists(err) else 0
    argv = CODEX_CMD + ["exec", "resume", "--json", "--skip-git-repo-check"]
    if meta.get("model"):
        argv += ["-m", meta["model"]]
    argv += [thread_id, "-"]  # resume takes no -C: the cwd goes to Popen
    proc = _spawn(argv, meta.get("cwd") or None, message, log, err)
    PROCS[thread_id] = proc
    meta.update(pid=proc.pid, turns=int(meta.get("turns") or 0) + 1, exit=None, started_turn=time.time())
    _write_meta(board, meta)
    if not _wait_event(proc, log, offset, False) and proc.poll() not in (None, 0):
        _reap(board, meta)
        raise _fail(proc, err, err_offset)
    return thread_id


def remove(board, thread_id):
    """Stop the session if it works (SIGTERM, SIGKILL after 5 s) and hide it; its files stay (the recap lists
    the log)."""
    meta = _read_meta(board, thread_id)
    if not meta:
        return
    proc = PROCS.pop(thread_id, None)
    if proc is not None:
        if proc.poll() is None:
            _kill(proc)
        meta["exit"] = proc.returncode
    elif meta.get("exit") is None and _pid_alive(meta.get("pid")):
        pid = meta["pid"]
        for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 0)):
            try:
                os.killpg(pid, sig)  # started in its own session: the group holds codex and its commands
            except OSError:
                break
            end = time.time() + wait
            while time.time() < end and _pid_alive(pid):
                time.sleep(0.1)
            if not _pid_alive(pid):
                break
    meta["removed"] = True
    _write_meta(board, meta)


# ---------------------------------------------------------------- reading the log
def last_answer(log_path, reply_text=None):
    """Codex's chat text of the last turn ('' if none)."""
    texts = [e["item"].get("text", "").strip() for e in _last_turn(log_path)
             if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)
             and e["item"].get("type") == "agent_message"]
    return "\n\n".join(t for t in texts if t)


def usage(log_path):
    """[input, cache write, cache read, output] summed over every turn. Codex's input count includes the
    cached part, so input here is the uncached rest."""
    try:
        st = os.stat(log_path)
    except OSError:
        return [0, 0, 0, 0]
    key = (st.st_mtime, st.st_size)
    memo = _usage_memo.get(log_path)
    if memo and memo[0] == key:
        return list(memo[1])
    total = [0, 0, 0, 0]
    for e in _events(log_path):
        u = e.get("usage") if e.get("type") == "turn.completed" else None
        if isinstance(u, dict):
            cached = int(u.get("cached_input_tokens") or 0)
            total[0] += max(0, int(u.get("input_tokens") or 0) - cached)
            total[1] += int(u.get("cache_write_input_tokens") or 0)
            total[2] += cached
            total[3] += int(u.get("output_tokens") or 0)
    _usage_memo[log_path] = (key, total)
    return list(total)


def digest(log_path):
    """The session for a recap: one 'TURN n' per turn, Codex's messages, one line per action."""
    out, turn = [], 0
    for e in _events(log_path):
        if e.get("type") == "turn.started":
            turn += 1
            out.append(f"TURN {turn}")
            continue
        item = e.get("item")
        if e.get("type") != "item.completed" or not isinstance(item, dict):
            continue
        kind = item.get("type")
        if kind == "agent_message" and (item.get("text") or "").strip():
            text = item["text"].strip()
            if len(text) > DIGEST_BLOCK:
                text = text[:DIGEST_BLOCK] + " […]"
            out.append(f"CODEX: {text}")
        elif kind == "command_execution":
            out.append("  · " + (str(item.get("command") or "").strip().splitlines() or [""])[0][:80])
        elif kind == "file_change":
            out.append("  · edit")
        elif kind == "mcp_tool_call":
            what = "/".join(str(item[k]) for k in ("server", "tool") if item.get(k))
            out.append("  · mcp" + (f": {what}" if what else ""))
    text = "\n\n".join(out)
    if len(text) > DIGEST_HEAD + DIGEST_TAIL:
        text = text[:DIGEST_HEAD] + "\n\n[… middle of the session left out …]\n\n" + text[-DIGEST_TAIL:]
    return text


def turn_ended_at(meta_or_session):
    """When the last turn ended (the log's mtime), None while it runs."""
    s = meta_or_session or {}
    if s.get("status") == "busy" or ("sessionId" not in s and _alive(s)[0]):
        return None
    try:
        return os.path.getmtime(s.get("log") or "")
    except OSError:
        return None
