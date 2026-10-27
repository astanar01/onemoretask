"""The Merge button without Claude: the board merges the task's branch into its base branch itself, removes the
worktree, and says so on the card as a board message. Anything in the way (conflicts, uncommitted work in the
worktree, the main checkout on another branch) leaves the main checkout as it was and hands the job to Claude with
the reason. A union-merged changelog (whats-new.md merge=union) merges without Claude.

Run: python3 tests/test_merge_local.py  (stdlib + git; a temp repo, a fresh temp HOME, every session call faked)."""
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


# 1. A clean merge: no Claude, the work is on main, the worktree and branch are gone, one board message says it.
clean = add_task("t1", {"feature.py": "x = 1\n"})
W.poll()
res = server.worktree_action(p, "t1", "merge")
check("clean: merged by the board", sorted(res), ["merged"])
check("clean: no Claude", replies, [])
check("clean: work on main", os.path.exists(os.path.join(proj, "feature.py")), True)
check("clean: worktree and branch removed", (os.path.isdir(clean["path"]), has_branch(clean)), (False, False))
e = last("t1")
check("clean: board message", (e["from"], e["status"], e["message"]),
      ("board", "merged", server.MERGED_HERE.format(base="main", sha=res["merged"]) + " " + server.MERGE_TIDIED))
W.poll()
check("clean: card still finished with Claude's report", (W.snapshot(p.id)["t1"]["phase"], W.snapshot(p.id)["t1"]["reason"]),
      ("finished", "Work done."))
check("clean: the watcher adds nothing", last("t1")["status"], "merged")

# 2. A conflict: the board undoes it, main is as it was, Claude gets the job with the reason.
clash = add_task("t2", {"app.py": "print('branch')\n"})
write(os.path.join(proj, "app.py"), "print('main')\n")
git(proj, "commit", "-q", "-am", "main moved")
head = git(proj, "rev-parse", "HEAD")
W.poll()
res = server.worktree_action(p, "t2", "merge")
check("conflict: not merged here", res, {})
check("conflict: main unchanged and clean", (git(proj, "rev-parse", "HEAD"), git(proj, "status", "--porcelain"),
                                              read(os.path.join(proj, "app.py"))), (head, "", "print('main')\n"))
tid, text, extra = replies[-1]
check("conflict: Claude asked, with why", (tid, text, "the merge hit conflicts in app.py and was undone" in extra,
                                           "3. There, merge" in extra), ("t2", "Merge worktree to main", True, True))
check("conflict: worktree kept", os.path.isdir(clash["path"]), True)

# 3. Two changelog lines under the same day: the union rule keeps both, main's first, no Claude.
log_wt = add_task("t3", {"whats-new.md": "# What's new\n\n## 2026-10-27\n\n- Old line.\n- Branch line.\n"})
write(os.path.join(proj, "whats-new.md"), "# What's new\n\n## 2026-10-27\n\n- Old line.\n- Main line.\n")
git(proj, "commit", "-q", "-am", "main changelog")
n = len(replies)
W.poll()
res = server.worktree_action(p, "t3", "merge")
check("changelog: merged by the board", (sorted(res), len(replies)), (["merged"], n))
check("changelog: both lines, main's first", read(os.path.join(proj, "whats-new.md")),
      "# What's new\n\n## 2026-10-27\n\n- Old line.\n- Main line.\n- Branch line.\n")

# 4. Uncommitted work in the worktree: Claude commits it, so the board does not merge.
dirty = add_task("t4", {"d.py": "d = 1\n"})
write(os.path.join(dirty["path"], "d.py"), "d = 2\n")
head = git(proj, "rev-parse", "HEAD")
W.poll()
check("dirty worktree: left to Claude", (server.worktree_action(p, "t4", "merge"),
                                         "not committed" in replies[-1][2], git(proj, "rev-parse", "HEAD")), ({}, True, head))

# 5. The main checkout on another branch: left to Claude.
other = add_task("t5", {"o.py": "o = 1\n"})
git(proj, "checkout", "-q", "-b", "side")
W.poll()
check("main on another branch: left to Claude", (server.worktree_action(p, "t5", "merge"),
                                                 "is on `side`, not `main`" in replies[-1][2]), ({}, True))
git(proj, "checkout", "-q", "main")

# 6. An archived card (moved to done, session removed): still archived after the board's merge, so a reply reopens it.
arch = add_task("t6", {"a.py": "a = 1\n"}, archived=True)
W.poll()
check("archived before", W.snapshot(p.id)["t6"]["archived"], True)
res = server.worktree_action(p, "t6", "merge")
W.poll()
check("archived: merged by the board", sorted(res), ["merged"])
check("archived: still archived, still finished", (W.snapshot(p.id)["t6"]["archived"], W.snapshot(p.id)["t6"]["phase"]),
      (True, "finished"))

# spoken() and phase_of skip board messages.
log = [{"from": "you", "status": "launch", "message": "go"}, {"from": "claude", "status": "done", "message": "ok"},
       {"from": "board", "status": "merged", "message": "merged"}]
check("spoken skips board messages", [e["message"] for e in server.spoken(log)], ["go", "ok"])
check("phase_of: a board message after done keeps it finished",
      server.phase_of({"id": "a", "state": "done", "status": "idle"}, log, 0), ("finished", "ok"))

print("FAILED" if fails else "all passed")
sys.exit(1 if fails else 0)
