"""Merge asks Claude to merge and clean up; the board does neither itself.

Run: python3 tests/test_merge_local.py (temporary repo and HOME; session calls faked)."""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="merge-local-test-")
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


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok  " if ok else "FAIL") + f" {name}: {got!r}" + ("" if ok else f" (want {want!r})"))


proj = os.path.join(TMP, "proj")
git(TMP, "init", "-q", "-b", "main", proj)
write(os.path.join(proj, ".gitignore"), "__pycache__/\n.task_board/\n")
write(os.path.join(proj, ".gitattributes"), "whats-new.md merge=union\n")
write(os.path.join(proj, "app.py"), "print('hi')\n")
write(os.path.join(proj, "whats-new.md"), "# What's new\n\n## 2026-10-27\n\n- Old line.\n")
git(proj, "add", ".")
git(proj, "commit", "-q", "-m", "base")
p = server.Project(proj)
os.makedirs(p.board)
tasks = {"columns": [{"id": "review", "name": "Review"}, {"id": "done", "name": "Done", "done": True}], "tasks": []}


def add_task(tid, files, archived=False):
    """A card sent with a worktree whose branch commits `files` ({path: text}); Claude reported done. `archived`:
    moved to done, its session removed and the recap posted."""
    t = {"id": tid, "title": tid, "column": "done" if archived else "review",
         "agent": {"id": "a-" + tid, "started": ""}}
    wt = server.make_worktree(p, t)
    t["agent"]["worktree"] = wt
    for rel, text in files.items():
        write(os.path.join(wt["path"], rel), text)
    git(wt["path"], "add", ".")
    git(wt["path"], "commit", "-q", "-m", tid)
    reports.append(p.board, tid, "launch", "Sent to Claude", "you", session="a-" + tid)
    reports.append(p.board, tid, "done", "Work done.", "claude")
    if archived:
        reports.append(p.board, tid, "recap", "The recap.", "claude")
    tasks["tasks"].append(t)
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump(tasks, f)
    return wt


replies = []
server.reply = lambda p_, tid, text, images=(), extra="": replies.append((tid, text, extra))
server.projects = lambda: [p]
server.list_sessions = lambda cwd: [{"id": "a-" + t["id"], "sessionId": "s-" + t["id"], "state": "done",
                                     "status": "idle"} for t in tasks["tasks"] if t["column"] != "done"]
server.run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))
W = server.WATCH


def last(tid):
    return reports.read(p.board, tid)[-1]


def has_branch(wt):
    return bool(git(proj, "branch", "--list", wt["branch"]))


# A clean merge goes to Claude without changing either checkout.
clean = add_task("t1", {"feature.py": "x = 1\n"})
W.poll()
head = git(proj, "rev-parse", "HEAD")
check("merge asks Claude", server.worktree_action(p, "t1", "merge"), {})
tid, text, extra = replies[-1]
check("merge label", (tid, text), ("t1", "Merge worktree to main"))
check("merge and cleanup instructions",
      all(x in extra for x in ("3. There, merge", "git worktree remove", "git branch -d",
                              "Do not force removal", "Never push")), True)
check("main untouched", git(proj, "rev-parse", "HEAD"), head)
check("copy and branch kept", (os.path.isdir(clean["path"]), has_branch(clean)), (True, True))
check("no board report", last("t1")["message"], "Work done.")

# Old merge reports must not trigger deletion, even after a restart.
git(proj, "merge", "-q", clean["branch"])
reports.append(p.board, "t1", "reply", "Merge worktree to main", "you")
reports.append(p.board, "t1", "done", "Merged.", "claude")
n = len(reports.read(p.board, "t1"))
W.poll()
server.Watcher().poll()
check("old merged copy stays", (os.path.isdir(clean["path"]), has_branch(clean)), (True, True))
check("no cleanup report", len(reports.read(p.board, "t1")), n)

dirty = add_task("t2", {"draft.py": "old\n"})
write(os.path.join(dirty["path"], "draft.py"), "only draft\n")
W.poll()
check("dirty merge asks Claude", server.worktree_action(p, "t2", "merge"), {})
check("draft stays", read(os.path.join(dirty["path"], "draft.py")), "only draft\n")

detached = {**clean, "base": head}
steps = "\n".join(server.worktree_steps(p, "t1", detached, "merge"))
check("detached base asks then cleans up",
      all(x in steps for x in ("which branch", "After the answer", "git worktree remove", "git branch -d")), True)

proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                        cwd=dirty["path"], start_new_session=True)
server.RUNS[(p.id, "t2")] = proc
try:
    server.worktree_action(p, "t2", "merge")
    check("board-started app stopped", proc.poll() is not None, True)
finally:
    if proc.poll() is None:
        proc.kill()
        proc.wait()

# Manual deletion still asks before throwing away hidden new work.
git(proj, "config", "status.showUntrackedFiles", "no")
write(os.path.join(clean["path"], "private.txt"), "keep me")
try:
    server.delete_worktree(p, "t1")
    check("manual deletion needs confirmation", False, True)
except server.UnsavedWorkError:
    check("manual deletion preserves new work", read(os.path.join(clean["path"], "private.txt")), "keep me")
finally:
    git(proj, "config", "--unset", "status.showUntrackedFiles")

log = [{"from": "you", "status": "launch", "message": "go"}, {"from": "claude", "status": "done", "message": "ok"},
       {"from": "board", "status": "merged", "message": "merged"}]
check("spoken skips old board messages", [e["message"] for e in server.spoken(log)], ["go", "ok"])
check("old board report keeps session finished",
      server.phase_of({"id": "a", "state": "done", "status": "idle"}, log, 0), ("finished", "ok"))

print("FAILED" if fails else "all passed")
sys.exit(1 if fails else 0)
