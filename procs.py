"""Process and log helpers shared by the board-run CLIs (codex.py, agy.py) and their worker scripts.

A board-run CLI runs one process per turn, in its own process group, with stdout and stderr appended to the session's
files. These helpers start, watch and stop such a process, read its JSON lines, and cut step text for the card."""
import json
import os
import re
import signal
import subprocess
import tempfile
import threading
import time

POSIX = os.name == "posix"
DIGEST_HEAD, DIGEST_TAIL, DIGEST_BLOCK = 30_000, 150_000, 4_000  # same as server.py
SAY_MAX = 300  # as server.SAY_MAX


def write_json(path, obj):
    """Replace `path` with `obj` as JSON in one rename; a temp per write, since the watcher and a request thread
    may write the same file at once."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1)
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


def _tail(path, n=300):
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - n))
            return f.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""


def _events(path, offset=0):
    """The JSON objects of a log, one per line, from byte `offset`; other lines are skipped."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            if offset:
                f.seek(offset)
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict):
                    yield e
    except OSError:
        return


def _spawn(argv, cwd, text, log, err):
    """Start one turn: stdout and stderr appended to the session's files, `text` fed on stdin from a thread
    (a 50 KB prompt would fill the pipe buffer before the CLI reads it)."""
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


def _signal(proc, kill=False):
    """SIGTERM (SIGKILL with `kill`) to a turn. On POSIX to its whole process group: _spawn starts it in its own
    session, so the group also holds the commands the CLI started, which must not outlive it."""
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


def _kill(proc, grace=5):
    if POSIX and stop_group(proc, grace):
        return
    proc.terminate()
    try:
        proc.wait(grace)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()


def kill_pid_group(pid, started=None, grace=5):
    """Stop a turn the board no longer holds a Popen for (a restart): SIGTERM to its group, SIGKILL after `grace`."""
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, 0)):
        try:
            os.killpg(pid, sig)  # started in its own session: the group holds the CLI and its commands
        except OSError:
            break
        end = time.time() + wait
        while time.time() < end and _pid_alive(pid, started):
            time.sleep(0.1)
        if not _pid_alive(pid, started):
            break


_MCP_WHAT_KEYS = ("query", "queries", "pattern", "name_pattern", "qualified_name", "function_name", "semantic_query",
                  "context", "concept", "intent", "path", "file_path", "repo_path", "url", "code", "id")
_MCP_WHAT_SKIP = ("project", "mode", "language")
MCP_WHAT_MAX = 120


def mcp_what(args):
    """What an MCP call looked for, in one short line, from its arguments; "" when nothing fits."""
    if not isinstance(args, dict):
        return ""
    value = next((args[k] for k in _MCP_WHAT_KEYS if args.get(k)), None)
    if value is None:
        value = next((v for k, v in args.items() if k not in _MCP_WHAT_SKIP and v and isinstance(v, str)), "")
    text = ", ".join(str(x) for x in value) if isinstance(value, (list, tuple)) else str(value)
    text = " ".join(text.split())
    return text[:MCP_WHAT_MAX - 1] + "…" if len(text) > MCP_WHAT_MAX else text


_SHELL_WRAP = re.compile(r"^\S*/(?:zsh|bash|sh) -l?c (['\"])(.*)\1$", re.S)


def command_what(cmd):
    """A shell command as a step row's text: its first line, without a `zsh -lc '...'` wrapper."""
    cmd = str(cmd or "").strip()
    m = _SHELL_WRAP.match(cmd)
    cmd = (m.group(2) if m else cmd).strip()
    return mcp_what({"query": (cmd.splitlines() or [""])[0]})


def path_what(paths):
    """File paths as a step row's text: the last two parts of each."""
    parts = ["/".join(str(x or "").split("/")[-2:]) for x in paths]
    return mcp_what({"query": ", ".join(x for x in parts if x)})


def say_what(text):
    text = " ".join(str(text or "").split())
    return text[:SAY_MAX - 1] + "…" if len(text) > SAY_MAX else text
