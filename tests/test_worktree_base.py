"""A new task worktree starts from the repo's default branch, not from whatever the shared folder has checked out.

Run: python3 tests/test_worktree_base.py  (stdlib + git; temp repos, a fresh temp HOME)."""
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="worktree-base-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}.items():
    os.environ[k] = v
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

fails = 0


def git(cwd, *a):
    r = subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"git {a} failed: {r.stderr}")
    return r.stdout.strip()


def commit(cwd, msg):
    git(cwd, "commit", "-q", "--allow-empty", "-m", msg)


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok  " if ok else "FAIL") + f" {name}: {got!r}" + ("" if ok else f" (want {want!r})"))


def proj(path):
    return SimpleNamespace(path=path, name=os.path.basename(path), board=os.path.join(path, ".task_board"))


def task(tid):
    return {"id": tid, "title": "check base"}


# (a) origin with main; the shared folder is switched to another branch
origin, app = os.path.join(TMP, "a-origin.git"), os.path.join(TMP, "a-app")
git(TMP, "init", "-q", "--bare", "-b", "main", origin)
git(TMP, "clone", "-q", origin, app)
commit(app, "base")
git(app, "push", "-q", "-u", "origin", "main")
main_sha = git(app, "rev-parse", "main")
git(app, "checkout", "-q", "-b", "other")
commit(app, "other work")
p = proj(app)
check("origin/main: default", server.default_branch(app), "main")
wt = server.make_worktree(p, task("a1"))
check("origin/main: base", wt["base"], "main")
check("origin/main: worktree at main", git(wt["path"], "rev-parse", "HEAD") == main_sha, True)
check("origin/main: folder untouched", git(app, "symbolic-ref", "--short", "HEAD"), "other")

# (b) no origin, the only default-like branch is master; folder on another branch
solo = os.path.join(TMP, "b-solo")
git(TMP, "init", "-q", "-b", "master", solo)
commit(solo, "base")
master_sha = git(solo, "rev-parse", "master")
git(solo, "checkout", "-q", "-b", "other")
commit(solo, "other work")
check("no origin: default", server.default_branch(solo), "master")
wt = server.make_worktree(proj(solo), task("b1"))
check("no origin: base", wt["base"], "master")
check("no origin: worktree at master", git(wt["path"], "rev-parse", "HEAD") == master_sha, True)

# (c) origin's default is trunk, not main
trunk_origin, trunk_app = os.path.join(TMP, "c-origin.git"), os.path.join(TMP, "c-app")
git(TMP, "init", "-q", "--bare", "-b", "trunk", trunk_origin)
git(TMP, "clone", "-q", trunk_origin, trunk_app)
commit(trunk_app, "base")
git(trunk_app, "push", "-q", "-u", "origin", "trunk")
git(trunk_app, "remote", "set-head", "origin", "-a")  # a clone of an empty repo has no origin/HEAD yet
git(trunk_app, "checkout", "-q", "-b", "other")
commit(trunk_app, "other work")
check("origin/trunk: default", server.default_branch(trunk_app), "trunk")
wt = server.make_worktree(proj(trunk_app), task("c1"))
check("origin/trunk: base", wt["base"], "trunk")

# (d) no branch we know about: falls back to the checked-out HEAD (old behaviour)
odd = os.path.join(TMP, "d-odd")
git(TMP, "init", "-q", "-b", "dev", odd)
commit(odd, "base")
check("no known default: default", server.default_branch(odd), None)
wt = server.make_worktree(proj(odd), task("d1"))
check("no known default: base", wt["base"], "dev")

print("PASS" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
