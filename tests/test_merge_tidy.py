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
             "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1",
             # A local path as a submodule URL needs this since git 2.38.
             "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "protocol.file.allow", "GIT_CONFIG_VALUE_0": "always"}.items():
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
check("merged, only caches left: folder removed", os.path.isdir(clean["path"]), False)
check("merged: branch removed", has_branch(clean), False)
check("merged: card says so", last("t1"), server.MERGE_TIDIED)
check("merged: still finished", W.snapshot(p.id)["t1"]["phase"], "finished")
check("not merged: folder kept", os.path.isdir(unmerged["path"]), True)
check("not merged: branch kept", has_branch(unmerged), True)
check("not merged: card says why", last("t2").startswith(server.MERGE_KEPT + "some of its commits"), True)
check("ignored file only in the worktree: kept", os.path.isdir(leftover["path"]), True)
check("ignored file: card names it", last("t3"), server.MERGE_KEPT + "it still holds more than its saved work: .env. "
      "Delete worktree throws them away.")
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
asked = [{"from": "you", "status": "reply", "message": "Merge worktree to main"},
         {"from": "claude", "status": "question", "message": "A clash in x. What now?"},
         {"from": "you", "status": "reply", "message": "A. Keep mine"}]
check("an answer to Claude's question keeps the ask", server.merge_reported(asked + log[1:]), True)
check("merge pressed while a question is open", server.merge_reported(
    [{"from": "claude", "status": "question", "message": "?"}] + log), True)

# A program still working in a merged worktree keeps it.
busy = add_task("t6", "merged, program inside")
proc = subprocess.Popen(["sleep", "30"], cwd=busy["path"])
try:
    t6 = tasks["tasks"][-1]
    check("program inside: kept", (server.tidy_merged(p, t6), os.path.isdir(busy["path"])),
          ("a program still runs from it. Stop it, then use Delete worktree.", True))
finally:
    proc.kill()
    proc.wait()
check("program gone: removed", (server.tidy_merged(p, t6), os.path.isdir(busy["path"])), ("", False))

# A repo that hides untracked files from `git status` must not hide them from the tidy check.
hidden = add_task("t8", "merged, untracked hidden by config")
git(proj, "config", "status.showUntrackedFiles", "no")
write(os.path.join(hidden["path"], "notes", "idea.txt"), "only here\n")
t8 = tasks["tasks"][-1]
try:
    check("untracked hidden by config: kept", (server.tidy_merged(p, t8), os.path.isdir(hidden["path"])),
          ("it still holds more than its saved work: notes/. Delete worktree throws them away.", True))
finally:
    git(proj, "config", "--unset", "status.showUntrackedFiles")

# Nothing is compared with the main checkout: an extra file or link keeps the worktree even when the main checkout
# has the same one.
linked = add_task("t9", "merged, nested folder link")
os.makedirs(os.path.join(linked["path"], "media"))
os.symlink(TMP, os.path.join(linked["path"], "media", "shots"))
os.makedirs(os.path.join(proj, "media"))
os.symlink(TMP, os.path.join(proj, "media", "shots"))
check("folder link, same one in the main checkout: kept",
      (server.tidy_merged(p, tasks["tasks"][-1]), os.path.isdir(linked["path"])),
      ("it still holds more than its saved work: media/. Delete worktree throws them away.", True))
copied = add_task("t17", "merged, copy of a main file")
write(os.path.join(copied["path"], "app.py.copy"), "x")
write(os.path.join(proj, "app.py.copy"), "x")
check("file identical to the main checkout's: kept",
      (server.tidy_merged(p, tasks["tasks"][-1]), os.path.isdir(copied["path"])),
      ("it still holds more than its saved work: app.py.copy. Delete worktree throws them away.", True))

# git status never shows edits to a file marked assume-unchanged or skip-worktree: they are work too.
for tid, flag in (("t10", "--assume-unchanged"), ("t11", "--skip-worktree")):
    marked = add_task(tid, "merged, edit hidden by " + flag)
    git(marked["path"], "update-index", flag, "app.py")
    write(os.path.join(marked["path"], "app.py"), "print('only here')\n")
    check(f"edit hidden by {flag}: kept", (server.tidy_merged(p, tasks["tasks"][-1]), os.path.isdir(marked["path"])),
          ("it still holds more than its saved work: app.py. Delete worktree throws them away.", True))
    write(os.path.join(marked["path"], "app.py"), "print('hi')\n")
    check(f"{flag} file back to its saved text: still kept",
          (server.tidy_merged(p, tasks["tasks"][-1]), os.path.isdir(marked["path"])),
          ("it still holds more than its saved work: app.py. Delete worktree throws them away.", True))
    git(marked["path"], "update-index", flag.replace("--", "--no-"), "app.py")
    check(f"{flag} mark taken off: removed",
          (server.tidy_merged(p, tasks["tasks"][-1]), os.path.isdir(marked["path"])), ("", False))

# A staged draft is work even when the file on disk is back to the main copy's text.
draft = add_task("t13", "merged, staged draft")
write(os.path.join(draft["path"], "app.py"), "print('draft')\n")
git(draft["path"], "add", "app.py")
write(os.path.join(draft["path"], "app.py"), "print('hi')\n")
t13 = tasks["tasks"][-1]
check("staged draft only in the worktree: kept", (server.tidy_merged(p, t13), os.path.isdir(draft["path"])),
      ("it still holds more than its saved work: app.py. Delete worktree throws them away.", True))
git(draft["path"], "add", "app.py")
check("draft unstaged back to the main copy's text: removed",
      (server.tidy_merged(p, t13), os.path.isdir(draft["path"])), ("", False))

# A link in the main checkout pointing back into the worktree is no second copy: it dies with the worktree.
for tid, where in (("t15", "file"), ("t16", "parent folder")):
    back = add_task(tid, "merged, main links into it by " + where)
    write(os.path.join(back["path"], "notes", "n.txt"), "only here\n")
    if where == "file":
        os.makedirs(os.path.join(proj, "notes"))
        os.symlink(os.path.join(back["path"], "notes", "n.txt"), os.path.join(proj, "notes", "n.txt"))
    else:
        os.symlink(os.path.join(back["path"], "notes"), os.path.join(proj, "notes"))
    check(f"main links back into it by {where}: kept",
          (server.tidy_merged(p, tasks["tasks"][-1]), os.path.isdir(back["path"])),
          ("it still holds more than its saved work: notes/. Delete worktree throws them away.", True))
    if where == "file":
        os.remove(os.path.join(proj, "notes", "n.txt"))
        os.rmdir(os.path.join(proj, "notes"))
    else:
        os.remove(os.path.join(proj, "notes"))

# When the process check cannot run, keep the worktree.
nolsof = add_task("t12", "merged, no lsof")
real_run = server.subprocess.run


def no_lsof(cmd, *a, **k):
    if cmd[0] == "lsof":
        raise FileNotFoundError("lsof")
    return real_run(cmd, *a, **k)


server.subprocess.run = no_lsof
try:
    check("process check cannot run: kept", (server.tidy_merged(p, tasks["tasks"][-1]), os.path.isdir(nolsof["path"])),
          ("the board could not check whether a program still runs from it. Use Delete worktree once none does.", True))
finally:
    server.subprocess.run = real_run

# A submodule commit made only inside the worktree lives in the worktree's own git dir: keep it.
lib = os.path.join(TMP, "lib")
git(TMP, "init", "-q", "-b", "main", lib)
write(os.path.join(lib, "a"), "1\n")
git(lib, "add", ".")
git(lib, "commit", "-q", "-m", "lib")
git(proj, "submodule", "add", "-q", lib, "lib")
git(proj, "commit", "-q", "-m", "add lib")
# An ignored file inside a submodule goes with the worktree too: the outer status does not list it.
private = add_task("t14", "merged, ignored file in a submodule")
git(private["path"], "submodule", "update", "--init", "-q")
sub_exclude = git(os.path.join(private["path"], "lib"), "rev-parse", "--path-format=absolute", "--git-path",
                  "info/exclude")
write(sub_exclude, ".env\n")
write(os.path.join(private["path"], "lib", ".env"), "SECRET=1\n")
t14 = tasks["tasks"][-1]
INNER = "a project inside it was filled in, and its own copy goes with it: lib. Delete worktree throws it away."
check("ignored file in a submodule: kept", (server.tidy_merged(p, t14), os.path.isdir(private["path"])),
      (INNER, True))
git(private["path"], "submodule", "deinit", "-q", "-f", "lib")
check("submodule emptied again: still kept (its git data stays in the worktree's git dir)",
      (server.tidy_merged(p, t14), os.path.isdir(private["path"])), (INNER, True))
inner = add_task("t7", "merged, inner commit")
git(inner["path"], "submodule", "update", "--init", "-q")
write(os.path.join(inner["path"], "lib", "b"), "only here\n")
git(os.path.join(inner["path"], "lib"), "add", ".")
git(os.path.join(inner["path"], "lib"), "commit", "-q", "-m", "inner work")
git(inner["path"], "commit", "-q", "-am", "bump lib")
git(proj, "merge", "-q", "--no-ff", "-m", "merge bump", inner["branch"])
t7 = tasks["tasks"][-1]
check("inner commit only in the worktree: kept", (server.tidy_merged(p, t7), os.path.isdir(inner["path"])),
      (INNER, True))

# The merge steps no longer tell Claude to remove the worktree.
steps = "\n".join(server.worktree_steps(p, "t2", unmerged, "merge"))
check("merge steps leave removal to the board", ("worktree remove" in steps, server.LEAVE_WORKTREE in steps),
      (False, True))
print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
