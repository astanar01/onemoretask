"""Run app from worktree without Claude: once Claude saved how it started the app (<board>/run_app.json), the
board starts it from the worktree itself, waits for it, opens its URL and says so on the card. A saved way that
fails is stopped and the job goes to Claude with the end of its output. Nothing saved: Claude is asked, and told to
save it. A merge or Delete worktree stops what the board started.

Run: python3 tests/test_run_app.py  (stdlib + git; a temp repo, a fresh temp HOME, every session call faked)."""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="run-app-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}.items():
    os.environ[k] = v
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

fails = 0


def git(cwd, *a):
    r = subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"git {a} failed: {r.stderr}")
    return r.stdout.strip()


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok  " if ok else "FAIL") + f" {name}: {got!r}" + ("" if ok else f" (want {want!r})"))


proj = os.path.join(TMP, "proj")
git(TMP, "init", "-q", "-b", "main", proj)
write(os.path.join(proj, ".gitignore"), ".task_board/\n")
write(os.path.join(proj, "index.html"), "<p>this task's copy</p>\n")
git(proj, "add", ".")
git(proj, "commit", "-q", "-m", "base")
p = server.Project(proj)
os.makedirs(p.board)
t = {"id": "t1", "title": "t1", "column": "review", "agent": {"id": "a-t1", "started": ""}}
wt = server.make_worktree(p, t)
t["agent"]["worktree"] = wt
write(os.path.join(wt["path"], "index.html"), "<p>the worktree</p>\n")
git(wt["path"], "commit", "-q", "-am", "change")
reports.append(p.board, "t1", "launch", "Sent to Claude", "you", session="a-t1")
reports.append(p.board, "t1", "done", "Work done.", "claude")
with open(p.data, "w", encoding="utf-8") as f:
    json.dump({"columns": [{"id": "review", "name": "Review"}], "tasks": [t]}, f)

replies, opened = [], []
server.reply = lambda p_, tid, text, images=(), extra="": replies.append((tid, text, extra))
server.webbrowser.open = opened.append
server.projects = lambda: [p]
server.list_sessions = lambda cwd: [{"id": "a-t1", "sessionId": "s-t1", "state": "done", "status": "idle"}]
server.run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))
server.RUN_WAIT = 20
server.WATCH.poll()


def recipe(command, url=""):
    write(server.run_recipe_path(p), json.dumps({"command": command, "url": url}))


def wait_for(cond, secs=25):
    end = time.time() + secs
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.1)
    return False


def board_msgs():
    return [e["message"] for e in reports.read(p.board, "t1") if e.get("from") == "board"]


# Nothing saved: Claude is asked, and told where to save how it started the app.
check("nothing saved: Claude asked", server.worktree_action(p, "t1", "run"), {})
check("nothing saved: told to save it", (replies[-1][1], server.run_recipe_path(p) in replies[-1][2], "{port}" in replies[-1][2],
                                         "tried to do this by itself" in replies[-1][2]), ("Run app from worktree", True, True, False))

# A saved web app: started from the worktree on a free port, its URL opened, the card told, no Claude.
recipe(f"{sys.executable} -m http.server {{port}} --bind 127.0.0.1", "http://127.0.0.1:{port}/index.html")
n = len(replies)
check("saved web app: started by the board", server.worktree_action(p, "t1", "run"), {"started": True})
check("saved web app: the card says it runs", wait_for(lambda: board_msgs()), True)
url = opened[-1] if opened else ""
check("saved web app: URL opened and on the card", (bool(url), board_msgs()[-1] == server.RAN_HERE.format(at=f" at {url}")), (True, True))
check("saved web app: serves the worktree's copy", server.urlopen(url, timeout=5).read().decode(), "<p>the worktree</p>\n")
check("saved web app: no Claude", len(replies), n)
first = server.RUNS[(p.id, "t1")]

# Run again restarts it: the old one stops.
server.worktree_action(p, "t1", "run")
check("run again: the old one stopped", first.wait(timeout=10) is not None, True)
check("run again: running again", wait_for(lambda: len(board_msgs()) == 2), True)

# Delete worktree stops it (the delete itself then refuses: the branch holds unmerged work).
proc = server.RUNS[(p.id, "t1")]
try:
    server.delete_worktree(p, "t1")
except server.UnsavedWorkError:
    pass
check("delete worktree: app stopped", (proc.wait(timeout=10) is not None, (p.id, "t1") in server.RUNS), (True, False))

# A saved way that fails: stopped, and Claude gets the job with the end of its output.
recipe("echo boom-from-the-app; exit 3")
server.worktree_action(p, "t1", "run")
check("failing: Claude asked", wait_for(lambda: len(replies) > n), True)
extra = replies[-1][2]
check("failing: why and output given", ("exited with code 3" in extra, "boom-from-the-app" in extra,
                                        "Fix the saved file" in extra), (True, True, True))

# No URL, ends well: a demo; its output goes on the card.
recipe("echo hello from the demo")
k = len(board_msgs())
server.worktree_action(p, "t1", "run")
check("demo: output on the card", wait_for(lambda: len(board_msgs()) > k) and "hello from the demo" in board_msgs()[-1], True)

# No URL, keeps running: a game or desktop app counts as started.
recipe(f"{sys.executable} -c \"import time; time.sleep(60)\"")
k = len(board_msgs())
server.worktree_action(p, "t1", "run")
check("app without URL: started", wait_for(lambda: len(board_msgs()) > k) and board_msgs()[-1] == server.RAN_HERE.format(at=""), True)
server.stop_run(p, "t1")

# The card stays finished with Claude's report through all of it.
server.WATCH.poll()
check("card still finished", server.WATCH.snapshot(p.id)["t1"]["phase"], "finished")

print("FAILED" if fails else "all passed")
sys.exit(1 if fails else 0)
