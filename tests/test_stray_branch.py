"""The card warns about commits on a branch named after the task that the board does not track.

Run: python3 tests/test_stray_branch.py  (stdlib + git; temp repos, a fresh temp HOME)."""
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="stray-test-")
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


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok  " if ok else "FAIL") + f" {name}: {got!r}" + ("" if ok else f" (want {want!r})"))


repo = os.path.join(TMP, "repo")
os.makedirs(repo)
git(repo, "init", "-q", "-b", "main")
git(repo, "commit", "-q", "--allow-empty", "-m", "base")
p = SimpleNamespace(path=repo)
TID = "mv9h67w60aate"

check("no branches", server._stray_branches(p, TID, "main", "HEAD"), [])

git(repo, "branch", f"list-view-{TID}")
check("branch with nothing new", server._stray_branches(p, TID, "main", "HEAD"), [])

git(repo, "checkout", "-q", f"list-view-{TID}")
git(repo, "commit", "-q", "--allow-empty", "-m", "answer")
git(repo, "checkout", "-q", "main")
git(repo, "branch", "other-mv000000000000")
check("stray branch with a commit", server._stray_branches(p, TID, "main", "HEAD"),
      [{"branch": f"list-view-{TID}", "ahead": 1}])
check("tracked branch is skipped", server._stray_branches(p, TID, f"list-view-{TID}", "HEAD"), [])

git(repo, "merge", "-q", "--no-edit", f"list-view-{TID}")
check("merged branch is quiet", server._stray_branches(p, TID, "main", "HEAD"), [])

print("FAILED" if fails else "ALL OK")
sys.exit(1 if fails else 0)
