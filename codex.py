"""OpenAI Codex CLI as a second session backend: the board runs `codex exec --json` itself.

Each Codex session (a "thread") lives in <board>/agent_reports/codex/:
- <task_id>-<epoch ms>.jsonl   the JSONL event stream of every turn (stdout; resumes append to it)
- <task_id>-<epoch ms>.err     stderr of every turn (progress text, errors)
- <thread_id>.json             the meta: task, name, model, cwd, log, err, pid, turns, exit, removed

A turn is one process: it runs while the session works and exits when the turn ends, so the state comes from
the process (alive: working; exited non-zero: blocked; else idle). A reply is `codex exec resume`.
"""
import glob
import json
import os
import shutil
import signal
import subprocess
import tempfile
import threading
import time
from datetime import datetime

POSIX = os.name == "posix"
CODEX_CMD = [shutil.which("codex") or os.path.expanduser("~/.local/bin/codex")]
MODEL_PREFIXES = ("gpt-", "o1", "o3", "o4", "codex")
FALLBACK_MODELS = [{"id": "gpt-6-astra", "name": "GPT-6-Astra"}, {"id": "gpt-5.6-sol", "name": "GPT-5.6-Sol"},
                   {"id": "gpt-5.5", "name": "GPT-5.5"}]
START_WAIT = 30  # seconds to wait for the first event of a turn
DIGEST_HEAD, DIGEST_TAIL, DIGEST_BLOCK = 30_000, 150_000, 4_000  # same as server.py
PROCS = {}  # thread id -> Popen of its running (or not yet reaped) turn
_usage_memo = {}  # log path -> ((mtime, size), [input, cache write, cache read, output])
_spawn_memo = {}  # log path -> ((mtime, size), (worker ids, ids a wait saw completed))
_rollout_memo = {}  # rollout path -> ((mtime, size), info)
_rollout_paths = {}  # worker thread id -> its rollout path, once found
_parent_memo = {}  # rollout path -> (parent thread id, thread id) from its line 1, which never changes


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
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=meta["id"] + ".", suffix=".tmp")
    try:  # a temp per write: the watcher and a request thread may write the same meta at once
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(meta, f, indent=1)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _pid_start(pid):
    """The process's start time as ps prints it, '' when there is no such process."""
    if not pid or not POSIX:
        return ""
    try:
        return subprocess.run(["ps", "-o", "lstart=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _pid_alive(pid, started=None):
    """`started` (_pid_start at spawn) tells a reused pid apart; a meta without it trusts the pid."""
    if not pid or not POSIX:  # os.kill(pid, 0) terminates the process on Windows
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    return not started or _pid_start(pid) == started


def _alive(meta):
    """(alive, exit code if the turn's Popen just finished else None)."""
    proc = PROCS.get(meta.get("id"))
    if proc is not None:
        code = proc.poll()
        return code is None, code
    return _pid_alive(meta.get("pid"), meta.get("pid_start")) if meta.get("exit") is None else False, None


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
                        "err": err, "started": time.time(), "pid": proc.pid, "pid_start": _pid_start(proc.pid), "turns": 1,
                        "exit": None, "removed": False})
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
    meta.update(pid=proc.pid, pid_start=_pid_start(proc.pid), turns=int(meta.get("turns") or 0) + 1, exit=None,
                started_turn=time.time())
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
    elif meta.get("exit") is None and _pid_alive(meta.get("pid"), meta.get("pid_start")):
        pid, started = meta["pid"], meta.get("pid_start")
        for sig, wait in ((signal.SIGTERM, 5), (signal.SIGKILL, 0)):
            try:
                os.killpg(pid, sig)  # started in its own session: the group holds codex and its commands
            except OSError:
                break
            end = time.time() + wait
            while time.time() < end and _pid_alive(pid, started):
                time.sleep(0.1)
            if not _pid_alive(pid, started):
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


# ---------------------------------------------------------------- workers (multi-agent sub-agents)
# The exec stream only names a worker (spawn_agent / wait collab_tool_call items); its nickname, model, actions and
# tokens are in its own rollout, $CODEX_HOME/sessions/YYYY/MM/DD/rollout-<local time>-<thread id>.jsonl.
# Codex 0.155 no longer logs those items in the exec stream: there a worker is found by the parent_thread_id in
# its rollout's first line (session_meta).
def _ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _spawned(log_path):
    """(worker ids in spawn order, ids a wait reported completed, the session's thread id) from its event log."""
    try:
        st = os.stat(log_path)
    except OSError:
        return [], set(), ""
    memo = _spawn_memo.get(log_path)
    if memo and memo[0] == (st.st_mtime, st.st_size):
        return memo[1]
    ids, done, thread = [], set(), ""
    for e in _events(log_path):
        if e.get("type") == "thread.started" and not thread:
            thread = e.get("thread_id") or ""
        item = e.get("item")
        if e.get("type") != "item.completed" or not isinstance(item, dict) or item.get("type") != "collab_tool_call":
            continue
        if item.get("tool") == "spawn_agent":
            ids += [x for x in item.get("receiver_thread_ids") or [] if x not in ids]
        done |= {k for k, v in (item.get("agents_states") or {}).items()
                 if isinstance(v, dict) and v.get("status") == "completed"}
    _spawn_memo[log_path] = ((st.st_mtime, st.st_size), (ids, done, thread))
    return ids, done, thread


def _start_of(log_path):
    """When the session started: the epoch ms in its log name (<task>-<ms>.jsonl), else the log's mtime."""
    try:
        return int(os.path.basename(log_path).rsplit(".", 1)[0].rsplit("-", 1)[1]) / 1000
    except (IndexError, ValueError):
        pass
    try:
        return os.path.getmtime(log_path)
    except OSError:
        return time.time()


def _children(since):
    """{parent thread id: [child thread ids, oldest first]} from line 1 of each rollout in the date folders from
    `since`'s local day on."""
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    first = time.strftime("%Y/%m/%d", time.localtime(since))
    out = {}
    for day in sorted(glob.glob(os.path.join(home, "sessions", "*", "*", "*"))):
        if "/".join(day.split(os.sep)[-3:]) < first:
            continue
        for n in sorted(os.listdir(day)):
            path = os.path.join(day, n)
            if not (n.startswith("rollout-") and n.endswith(".jsonl")):
                continue
            if path not in _parent_memo:
                try:
                    with open(path, encoding="utf-8", errors="replace") as f:
                        e = json.loads(f.readline())
                except (OSError, ValueError):
                    continue  # line 1 still being written: read it again next time
                p = e.get("payload") if isinstance(e, dict) and isinstance(e.get("payload"), dict) else {}
                _parent_memo[path] = (p.get("parent_thread_id") or "", p.get("id") or "")
            parent, tid = _parent_memo[path]
            if parent and tid:
                out.setdefault(parent, []).append(tid)
                _rollout_paths.setdefault(tid, path)
    return out


def _rollout(thread_id):
    path = _rollout_paths.get(thread_id)
    if path and os.path.exists(path):
        return path
    home = os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex")
    found = glob.glob(os.path.join(home, "sessions", "*", "*", "*", f"rollout-*-{thread_id}.jsonl"))
    if found:
        _rollout_paths[thread_id] = found[0]
        return found[0]
    return None


def _rollout_info(path):
    """What a worker's rollout says: name, model, started, updated, finished, action, usage, its own workers."""
    st = os.stat(path)
    memo = _rollout_memo.get(path)
    if memo and memo[0] == (st.st_mtime, st.st_size):
        return memo[1]
    info = {"name": "", "model": "", "started": None, "updated": None, "finished": False, "action": "",
            "usage": None, "children": [], "done": set()}
    with open(path, encoding="utf-8", errors="replace") as f:
        for n, line in enumerate(f):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            info["updated"] = _ts(e.get("timestamp")) or info["updated"]
            p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            if n == 0 and e.get("type") == "session_meta":
                info["name"] = p.get("agent_nickname") or ""
                info["started"] = _ts(p.get("timestamp") or e.get("timestamp"))
            elif e.get("type") == "turn_context" and p.get("model"):
                info["model"] = p["model"]
            elif e.get("type") != "event_msg":
                continue
            kind = p.get("type")
            if kind == "task_started":
                info["finished"] = False
            elif kind == "task_complete":
                info["finished"] = True
            elif kind == "token_count" and isinstance(p.get("info"), dict):
                info["usage"] = p["info"].get("total_token_usage") or info["usage"]
            elif kind == "item_completed" and isinstance(p.get("item"), dict):
                item = p["item"]
                if item.get("type") == "CommandExecution":
                    cmd = item.get("command")
                    cmd = " ".join(map(str, cmd)) if isinstance(cmd, list) else str(cmd or "")
                    info["action"] = (cmd.strip().splitlines() or [""])[0][:80]
                elif item.get("type") == "FileChange" and item.get("changes"):
                    info["action"] = "edit " + os.path.basename(str(next(iter(item["changes"]))))
                elif item.get("type") == "CollabAgentToolCall":
                    if item.get("tool") == "spawn_agent":
                        info["children"] += item.get("receiver_thread_ids") or []
                    info["done"] |= {k for k, v in (item.get("agents_states") or {}).items()
                                     if isinstance(v, dict) and v.get("status") == "completed"}
    _rollout_memo[path] = ((st.st_mtime, st.st_size), info)
    return info


def _workers(log_path):
    """[(thread id, rollout info or None, finished)] for every worker of the session, workers' workers included."""
    ids, done, thread = _spawned(log_path)
    kids = _children(_start_of(log_path)) if thread else {}
    queue, out, seen = list(ids) + kids.get(thread, []), [], set()
    done = set(done)
    while queue:
        tid = queue.pop(0)
        if tid in seen:
            continue
        seen.add(tid)
        path = _rollout(tid)
        try:
            info = _rollout_info(path) if path else None
        except OSError:
            info = None
        if info:
            queue += info["children"]
            done |= info["done"]
        queue += kids.get(tid, [])
        out.append((tid, info))
    return [(tid, info, bool(info and info["finished"]) or tid in done) for tid, info in out]


def subagents(log_path):
    """The session's workers as server.subagents rows: [{id, name, type, started, updated, finished, action, skills,
    tokens}], oldest first. A worker whose rollout is missing still shows, with what the event log says."""
    try:
        fallback = os.path.getmtime(log_path)
    except OSError:
        fallback = time.time()
    rows = []
    for tid, info, finished in _workers(log_path):
        info = info or {}
        usage = info.get("usage") or {}
        rows.append({"id": tid, "name": info.get("name") or "Worker", "type": info.get("model") or "codex",
                     "started": info.get("started") or fallback, "updated": info.get("updated") or fallback,
                     "finished": finished, "action": info.get("action") or "", "skills": [],
                     "tokens": int(usage.get("total_tokens") or 0)})
    rows.sort(key=lambda r: r["started"])
    return rows


def subagent_usage(log_path):
    """([input, cache write, cache read, output] summed over the session's workers, worker count); same split as
    usage()."""
    total, workers = [0, 0, 0, 0], _workers(log_path)
    for _, info, _ in workers:
        u = (info or {}).get("usage") or {}
        cached = int(u.get("cached_input_tokens") or 0)
        total[0] += max(0, int(u.get("input_tokens") or 0) - cached)
        total[1] += int(u.get("cache_write_input_tokens") or 0)
        total[2] += cached
        total[3] += int(u.get("output_tokens") or 0)
    return total, len(workers)


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
        elif kind == "collab_tool_call" and item.get("tool") == "spawn_agent":
            out.append("  · spawn worker: " + (str(item.get("prompt") or "").strip().splitlines() or [""])[0][:80])
        elif kind == "collab_tool_call" and item.get("tool") == "wait":
            out.append("  · wait for workers")
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
