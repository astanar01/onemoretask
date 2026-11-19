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
import re
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


def installed():
    return os.path.exists(CODEX_CMD[0])


MODELS_TTL = 3600  # refresh Codex's model list once its cache file is this old (seconds)
MODELS_RETRY = 600  # and try again no sooner than this after a refresh that changed nothing
_models_lock = threading.Lock()
_models_refreshing = False
_models_tried = 0.0


def _models_cache():
    return os.path.join(os.environ.get("CODEX_HOME") or os.path.expanduser("~/.codex"), "models_cache.json")


def _refresh_models():
    # Codex only rewrites models_cache.json when Codex itself runs; `codex debug models` fetches the catalog
    # and rewrites the cache when it is older than Codex's own few-minute limit (measured on 0.169.1).
    global _models_refreshing
    try:
        subprocess.run(CODEX_CMD + ["debug", "models"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError) as e:
        print("codex model refresh:", e)
    finally:
        _models_refreshing = False


def refreshing():
    return _models_refreshing


def models():
    """The OpenAI models Codex lists, from its own cache; a fixed list when there is none.
    A cache over MODELS_TTL old starts one background refresh, so new models show up without a Codex run."""
    global _models_refreshing, _models_tried
    path = _models_cache()
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        age = float("inf")
    if age > MODELS_TTL and installed():
        with _models_lock:
            if not _models_refreshing and time.time() - _models_tried > MODELS_RETRY:
                _models_refreshing, _models_tried = True, time.time()
                threading.Thread(target=_refresh_models, daemon=True).start()
    try:
        with open(path, encoding="utf-8") as f:
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


def _signal(proc, kill=False):
    """SIGTERM (SIGKILL with `kill`) to a turn. On POSIX to its whole process group: _spawn starts it in its own
    session, so the group also holds the commands codex started, which must not outlive it."""
    if POSIX:
        try:
            os.killpg(proc.pid, signal.SIGKILL if kill else signal.SIGTERM)
            return
        except OSError:
            pass
    (proc.kill if kill else proc.terminate)()


def group_alive(pgid):
    """True while any process of group `pgid` is left (a zombie counts: reap the leader first)."""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def stop_group(proc, grace=5):
    """POSIX: SIGTERM to the process group `proc` leads (started with start_new_session), then SIGKILL to whatever is
    left of it after `grace` seconds. Waits for the whole group, not only `proc`: a leader that quits at once can leave
    a command it started that ignores SIGTERM. False when `proc` leads no group (nothing was sent)."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except OSError:
        return False
    end = time.time() + grace
    while time.time() < end:
        if proc.poll() is not None and not group_alive(proc.pid):
            return True
        time.sleep(0.05)
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    proc.wait()
    for _ in range(40):  # SIGKILL cannot be ignored; the rest of the group is gone within moments
        if not group_alive(proc.pid):
            break
        time.sleep(0.05)
    return True


def _kill(proc):
    if POSIX and stop_group(proc):
        return
    proc.terminate()
    try:
        proc.wait(5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def lock_args(roots):
    """-c overrides that keep a session's writes to `roots` (and the temp dir), with no way to ask for more: the
    user's config may send such requests to an automatic reviewer that says yes. Only -c, since `exec resume`
    takes no -s or --add-dir; it is passed again on every resume."""
    return ["-c", 'sandbox_mode="workspace-write"', "-c", 'approval_policy="never"',
            "-c", "sandbox_workspace_write.writable_roots=" + json.dumps(roots)]


def answer_dir(task_id):
    """The working folder of an answer-only session: an empty one outside the project, since Codex can always write
    in its working folder."""
    return os.path.join(tempfile.gettempdir(), "task-board-answer", task_id)


def start(board, task_id, name, prompt, model, cwd, effort="", writable=None):
    """Launch a new Codex session for a card; returns its thread id once codex has named it. `writable`: the only
    folders it may write in (an answer-only card); it then works from answer_dir, so `cwd` stays read-only."""
    base = os.path.join(folder(board), f"{task_id}-{int(time.time() * 1000)}")
    log, err = base + ".jsonl", base + ".err"
    lock = lock_args(writable) if writable is not None else []
    if lock:
        cwd = answer_dir(task_id)
        os.makedirs(cwd, exist_ok=True)
    argv = CODEX_CMD + ["exec", "--json", "--skip-git-repo-check", "-s", "workspace-write", *lock]
    if model:
        argv += ["-m", model]
    if effort:
        argv += ["-c", f'model_reasoning_effort="{effort}"']
    argv += ["-C", cwd, "-"]
    proc = _spawn(argv, cwd, prompt, log, err)
    thread_id = _wait_event(proc, log, 0, True)
    if not thread_id:
        raise _fail(proc, err)
    PROCS[thread_id] = proc
    _write_meta(board, {"id": thread_id, "task": task_id, "name": name, "model": model, "effort": effort, "cwd": cwd, "log": log,
                        "err": err, "started": time.time(), "pid": proc.pid, "pid_start": _pid_start(proc.pid), "turns": 1,
                        "exit": None, "removed": False, **({"lock": lock} if lock else {})})
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
    argv = CODEX_CMD + ["exec", "resume", "--json", "--skip-git-repo-check", *(meta.get("lock") or [])]
    if meta.get("lock") and meta.get("cwd"):
        os.makedirs(meta["cwd"], exist_ok=True)  # a temp folder: a restart may have emptied it
    if meta.get("model"):
        argv += ["-m", meta["model"]]
    if meta.get("effort"):
        argv += ["-c", f'model_reasoning_effort="{meta["effort"]}"']
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
    texts = [str(e["item"].get("text") or "").strip() for e in _last_turn(log_path)
             if e.get("type") == "item.completed" and isinstance(e.get("item"), dict)
             and e["item"].get("type") == "agent_message"]
    return "\n\n".join(t for t in texts if t)


def _n(v):
    """A token count from a log; 0 when it is not a number."""
    try:
        return int(v or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


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
            cached = _n(u.get("cached_input_tokens"))
            total[0] += max(0, _n(u.get("input_tokens")) - cached)
            total[1] += _n(u.get("cache_write_input_tokens"))
            total[2] += cached
            total[3] += _n(u.get("output_tokens"))
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
            "usage": None, "context": 0, "peak": 0, "window": None, "children": [], "done": set()}
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
                if isinstance(p["info"].get("total_token_usage"), dict):
                    info["usage"] = p["info"]["total_token_usage"] or info["usage"]
                last = p["info"].get("last_token_usage")
                if isinstance(last, dict):  # its input count includes the cached part: the whole context
                    info["context"] = _n(last.get("input_tokens"))
                    info["peak"] = max(info["peak"], info["context"])
                info["window"] = p["info"].get("model_context_window") or info["window"]
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
                     "tokens": _n(usage.get("total_tokens"))})
    rows.sort(key=lambda r: r["started"])
    return rows


def subagent_usage(log_path):
    """([input, cache write, cache read, output] summed over the session's workers, worker count); same split as
    usage()."""
    total, workers = [0, 0, 0, 0], _workers(log_path)
    for _, info, _ in workers:
        u = (info or {}).get("usage") or {}
        cached = _n(u.get("cached_input_tokens"))
        total[0] += max(0, _n(u.get("input_tokens")) - cached)
        total[1] += _n(u.get("cache_write_input_tokens"))
        total[2] += cached
        total[3] += _n(u.get("output_tokens"))
    return total, len(workers)


# ---------------------------------------------------------------- Codex workers of a Claude session (codex_worker.py)
# A Claude card whose subagent model is an OpenAI one runs each part through codex_worker.py, which writes
# <board>/agent_reports/codex-workers/<task>-<epoch ms>.{jsonl,err,json}: outside folder(), so sessions() never lists one.
_worker_memo = {}  # worker log path -> ((mtime, size), (finished, action))


def worker_folder(board):
    path = os.path.join(board, "agent_reports", "codex-workers")
    os.makedirs(path, exist_ok=True)
    return path


def _worker_metas(board, task_id, since=0):
    folder_ = os.path.join(board, "agent_reports", "codex-workers")
    try:
        names = sorted(os.listdir(folder_))
    except OSError:
        return []
    out = []
    for n in names:
        if not (n.startswith(task_id + "-") and n.endswith(".json")):
            continue
        try:
            with open(os.path.join(folder_, n), encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            continue  # being written: next pass
        if isinstance(meta, dict) and meta.get("task") == task_id and (meta.get("started") or 0) >= (since or 0):
            out.append(meta)
    return out


def _worker_state(log):
    """(a turn.completed seen, last command or file change started or done) from a worker's event log."""
    st = os.stat(log)
    memo = _worker_memo.get(log)
    if memo and memo[0] == (st.st_mtime, st.st_size):
        return memo[1]
    finished, action = False, ""
    for e in _events(log):
        if e.get("type") == "turn.started":
            finished = False
        elif e.get("type") == "turn.completed":
            finished = True
        item = e.get("item")
        if e.get("type") not in ("item.started", "item.completed") or not isinstance(item, dict):
            continue
        if item.get("type") == "command_execution":
            action = (str(item.get("command") or "").strip().splitlines() or [""])[0][:80]
        elif item.get("type") == "file_change" and item.get("changes"):
            c = item["changes"][0] if isinstance(item["changes"], list) else next(iter(item["changes"]))
            action = "edit " + os.path.basename(str(c.get("path") if isinstance(c, dict) else c))
    _worker_memo[log] = ((st.st_mtime, st.st_size), (finished, action))
    return finished, action


def worker_rows(board, task_id, since=0):
    """The card's Codex workers started at/after `since`, as subagents() rows plus "cli": "codex", oldest first.
    tokens = input (cached part included) + output, like a rollout's total_tokens."""
    rows = []
    for meta in _worker_metas(board, task_id, since):
        log = meta.get("log") or ""
        try:
            finished, action = _worker_state(log)
            updated = os.path.getmtime(log)
        except OSError:
            finished, action, updated = False, "", meta.get("started") or 0
        u = usage(log)
        code = meta.get("exit")
        finished = finished or code == 0
        # ended without finishing: a non-zero exit, or no exit written and its codex is gone (helper killed hard)
        stopped = not finished and (code is not None or (POSIX and not _pid_alive(meta.get("pid"),
                                                                                    meta.get("pid_start"))))
        rows.append({"id": os.path.basename(log).rsplit(".", 1)[0], "name": meta.get("name") or "Codex worker",
                     "type": meta.get("model") or "codex", "started": meta.get("started") or updated,
                     "updated": updated, "finished": finished, "stopped": stopped, "action": action,
                     "skills": [], "tokens": u[0] + u[2] + u[3], "cli": "codex"})
    rows.sort(key=lambda r: r["started"])
    return rows


def worker_usage(board, task_id, since=0):
    """([input, cache write, cache read, output] summed over the card's Codex workers, worker count), like
    subagent_usage()."""
    total, metas = [0, 0, 0, 0], _worker_metas(board, task_id, since)
    for meta in metas:
        total = [a + b for a, b in zip(total, usage(meta.get("log") or ""))]
    return total, len(metas)


def context(log_path):
    """How full the session's context is, from its own rollout: {"tokens", "peak", "model", "max"} like
    server.context, or None before its first reply."""
    thread = _spawned(log_path)[2]
    path = thread and _rollout(thread)
    info = path and _rollout_info(path)
    if not info or not info["context"]:
        return None
    return {"tokens": info["context"], "peak": info["peak"], "model": info["model"], "max": info["window"]}


_MCP_WHAT_KEYS = ("query", "queries", "pattern", "name_pattern", "qualified_name", "function_name", "semantic_query",
                  "context", "concept", "intent", "path", "file_path", "repo_path", "url", "code", "id")
_MCP_WHAT_SKIP = ("project", "mode", "language")
MCP_WHAT_MAX = 120


def mcp_what(args):
    """What an MCP call looked for, in one short line, from its arguments; "" when nothing fits. Lives here because
    server.py imports this module and not the reverse."""
    if not isinstance(args, dict):
        return ""
    value = next((args[k] for k in _MCP_WHAT_KEYS if args.get(k)), None)
    if value is None:
        value = next((v for k, v in args.items() if k not in _MCP_WHAT_SKIP and v and isinstance(v, str)), "")
    text = ", ".join(str(x) for x in value) if isinstance(value, (list, tuple)) else str(value)
    text = " ".join(text.split())
    return text[:MCP_WHAT_MAX - 1] + "…" if len(text) > MCP_WHAT_MAX else text


_mcp_seen = {}  # (log path, turn, item id) -> the time a call is shown at (its events carry none)
_mcp_logs_read = set()  # logs read since the board started: on the first read their calls get the log's write time
_mcp_use_memo = {}  # log path -> ((mtime, size), rows)


_SHELL_WRAP = re.compile(r"^\S*/(?:zsh|bash|sh) -l?c (['\"])(.*)\1$", re.S)
SAY_MAX = 300  # as server.SAY_MAX


def _step(item):
    """A Codex item as the row fields of one step ({tool, server, what, kind}), or None for an item that is not one."""
    kind = item.get("type")
    if kind == "mcp_tool_call":
        return {"server": str(item.get("server") or ""), "tool": str(item.get("tool") or ""),
                "what": mcp_what(item.get("arguments")), "kind": "mcp"}
    if kind == "command_execution":
        cmd = str(item.get("command") or "").strip()
        m = _SHELL_WRAP.match(cmd)
        cmd = (m.group(2) if m else cmd).strip()
        return {"server": "", "tool": "shell", "what": mcp_what({"query": (cmd.splitlines() or [""])[0]}), "kind": "tool"}
    if kind == "file_change":
        changes = item.get("changes") if isinstance(item.get("changes"), list) else []
        paths = ["/".join(str(c.get("path") or "").split("/")[-2:]) for c in changes if isinstance(c, dict)]
        return {"server": "", "tool": "edit", "what": mcp_what({"query": ", ".join(p for p in paths if p)}), "kind": "tool"}
    if kind == "agent_message":
        text = " ".join(str(item.get("text") or "").split())
        text = text[:SAY_MAX - 1] + "…" if len(text) > SAY_MAX else text
        return {"server": "", "tool": "", "what": text, "kind": "say"} if text else None
    return None


def mcp_uses(log_path):
    """Each step in a Codex log (MCP calls, commands, file edits, Codex's own words), oldest first, in
    server.mcp_uses's row shape. Item ids start again each turn, so the turn is part of a row's id. A step first seen
    live gets a time within one pass; steps from before a board restart all get the log's last write time."""
    try:
        st = os.stat(log_path)
    except OSError:
        return []
    memo = _mcp_use_memo.get(log_path)
    if memo and memo[0] == (st.st_mtime, st.st_size):
        return [dict(r) for r in memo[1]]
    now = st.st_mtime if log_path not in _mcp_logs_read else time.time()
    _mcp_logs_read.add(log_path)
    rows, turn, name = {}, 0, os.path.basename(log_path)
    for e in _events(log_path):
        if e.get("type") == "turn.started":
            turn += 1
        item = e.get("item")
        if e.get("type") not in ("item.started", "item.completed") or not isinstance(item, dict):
            continue
        step = _step(item)
        if not step:
            continue
        key = (log_path, turn, item.get("id"))
        row = rows.get(key)
        if not row:
            row = rows[key] = {"id": f"{name}:{turn}:{item.get('id')}", **step,
                               "at": _mcp_seen.setdefault(key, now), "state": "open", "sub": False}
        elif step["what"] and not row["what"]:
            row["what"] = step["what"]  # a file change names its files only once it is done
        if e.get("type") == "item.completed":
            # A message has no status; a command that ran but exited non-zero did not work either.
            ok = item.get("status", "completed") == "completed" and item.get("exit_code") in (None, 0, "0")
            row["state"] = "done" if ok else "failed"
    found = list(rows.values())
    _mcp_use_memo[log_path] = ((st.st_mtime, st.st_size), found)
    return [dict(r) for r in found]


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
