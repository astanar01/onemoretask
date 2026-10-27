"""After Claude reports a card's merge done, the board removes the task's worktree and branch itself, but only when
nothing in them would be lost; else it keeps them and says why on the card, once.

Run: python3 tests/test_merge_tidy.py  (stdlib + git; a temp repo, a fresh temp HOME, every session call faked)."""
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="merge-tidy-test-")
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
write(os.path.join(proj, ".gitignore"), ".env\n__pycache__/\n.task_board/\n")
write(os.path.join(proj, "app.py"), "print('hi')\n")
git(proj, "add", ".")
git(proj, "commit", "-q", "-m", "base")
p = server.Project(proj)
os.makedirs(p.board)
tasks = {"columns": [{"id": "review", "name": "Review"}, {"id": "done", "name": "Done", "done": True}], "tasks": []}


def add_task(tid, title, merge=True, ask=True, done=True):
    """A card sent with a worktree that committed one file; `merge` merges its branch into main, `ask` logs the
    card's Merge button, `done` logs Claude's done report after it."""
    t = {"id": tid, "title": title, "column": "review", "agent": {"id": "a-" + tid, "started": ""}}
    wt = server.make_worktree(p, t)
    t["agent"]["worktree"] = wt
    write(os.path.join(wt["path"], tid + ".txt"), tid + "\n")
    git(wt["path"], "add", ".")
    git(wt["path"], "commit", "-q", "-m", tid)
    if merge:
        git(proj, "merge", "-q", "--no-ff", "-m", "merge " + tid, wt["branch"])
    reports.append(p.board, tid, "launch", "Sent to Claude", "you", session="a-" + tid)
    reports.append(p.board, tid, "done", "Work done.", "claude")
    if ask:
        reports.append(p.board, tid, "reply", "Merge worktree to main", "you", session="a-" + tid)
    if ask and done:
        reports.append(p.board, tid, "done", "Merged.", "claude")
    tasks["tasks"].append(t)
    return wt


clean = add_task("t1", "merged clean")
os.makedirs(os.path.join(clean["path"], "__pycache__"))
write(os.path.join(clean["path"], "__pycache__", "app.cpython-312.pyc"), "bytecode")
write(os.path.join(clean["path"], "app.py.copy"), "x")  # an untracked file...
write(os.path.join(proj, "app.py.copy"), "x")  # ...identical to the main checkout's
unmerged = add_task("t2", "not merged", merge=False)
leftover = add_task("t3", "merged with env")
write(os.path.join(leftover["path"], ".env"), "SECRET=1\n")  # ignored, but only here: work to lose
noask = add_task("t4", "never asked", ask=False)
asked_only = add_task("t5", "asked not done", done=False)
with open(p.data, "w", encoding="utf-8") as f:
    json.dump(tasks, f)

server.projects = lambda: [p]
server.list_sessions = lambda cwd: [{"id": "a-" + t["id"], "sessionId": "s-" + t["id"], "state": "done",
                                     "status": "idle"} for t in tasks["tasks"]]
server.run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))
W = server.WATCH


def settle():
    for _ in range(500):
        if not W.tidying:
            return
        time.sleep(0.01)
    raise SystemExit("FAIL a tidy thread hung")


def last(tid):
    return reports.read(p.board, tid)[-1]["message"]


def has_branch(wt):
    return bool(git(proj, "branch", "--list", wt["branch"]))


W.poll()
settle()
check("merged, only caches and copies left: folder removed", os.path.isdir(clean["path"]), False)
check("merged: branch removed", has_branch(clean), False)
check("merged: card says so", last("t1"), server.MERGE_TIDIED)
check("merged: still finished", W.snapshot(p.id)["t1"]["phase"], "finished")
check("not merged: folder kept", os.path.isdir(unmerged["path"]), True)
check("not merged: branch kept", has_branch(unmerged), True)
check("not merged: card says why", last("t2").startswith(server.MERGE_KEPT + "some of its commits"), True)
check("ignored file only in the worktree: kept", os.path.isdir(leftover["path"]), True)
check("ignored file: card names it", last("t3"), server.MERGE_KEPT + "it still holds changes that are not in the "
      "main copy: .env. Delete worktree throws them away.")
check("no merge asked: kept", os.path.isdir(noask["path"]), True)
check("no merge asked: no message", last("t4"), "Work done.")
check("merge asked, no done yet: kept", os.path.isdir(asked_only["path"]), True)
check("merge asked, no done yet: no message", last("t5"), "Merge worktree to main")

n2, n3 = len(reports.read(p.board, "t2")), len(reports.read(p.board, "t3"))
W.poll()
settle()
W.poll()
settle()
check("kept worktrees are said once, not on every pass",
      (len(reports.read(p.board, "t2")), len(reports.read(p.board, "t3"))), (n2, n3))

# A new merge ask after a kept one tries again.
os.remove(os.path.join(leftover["path"], ".env"))
reports.append(p.board, "t3", "reply", "Merge worktree to main", "you", session="a-t3")
reports.append(p.board, "t3", "done", "Merged.", "claude")
W.poll()
settle()
check("asked again once the leftover is gone: removed", os.path.isdir(leftover["path"]), False)

# merge_reported on its own: a later reply that is not a merge ends the window.
log = [{"from": "you", "status": "reply", "message": "Merge worktree to dev"},
       {"from": "claude", "status": "done", "message": "Merged."}]
check("merge then done", server.merge_reported(log), True)
check("a newer reply", server.merge_reported(log + [{"from": "you", "status": "reply", "message": "fix X"},
                                                    {"from": "claude", "status": "done", "message": "ok"}]), False)
check("a note does not end it", server.merge_reported(log + [{"from": "you", "status": "note", "message": "x"}]), True)

# The merge steps no longer tell Claude to remove the worktree.
steps = "\n".join(server.worktree_steps(p, "t2", unmerged, "merge"))
check("merge steps leave removal to the board", ("worktree remove" in steps, server.LEAVE_WORKTREE in steps),
      (False, True))
print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
