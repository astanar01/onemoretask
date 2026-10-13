"""GET /api/unpushed lists the project's commits its remote does not have yet.

Run: python3 tests/test_unpushed.py  (stdlib + git; temp repos, a fresh temp HOME)."""
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="unpushed-test-")
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


origin, app = os.path.join(TMP, "origin.git"), os.path.join(TMP, "app")
git(TMP, "init", "-q", "--bare", "-b", "main", origin)
git(TMP, "clone", "-q", origin, app)
p = SimpleNamespace(path=app)

# (a) no commits, no upstream: nothing to compare with
u = server.unpushed_commits(p)
check("empty repo upstream", u["upstream"], None)

commit(app, "base")
git(app, "push", "-q", "-u", "origin", "main")
u = server.unpushed_commits(p)
check("pushed: upstream", u["upstream"], "origin/main")
check("pushed: count", (u["count"], u["commits"]), (0, []))

# (b) two local commits, newest first, with subject and date
commit(app, "first: local")
commit(app, "second\tlocal")
u = server.unpushed_commits(p)
check("ahead: count", u["count"], 2)
check("ahead: subjects", [c["subject"] for c in u["commits"]], ["second\tlocal", "first: local"])
check("ahead: sha is short HEAD", u["commits"][0]["sha"], git(app, "rev-parse", "--short", "HEAD"))
check("ahead: date set", bool(u["commits"][0]["at"]), True)

# (c) pushing clears it
git(app, "push", "-q")
check("after push", server.unpushed_commits(p)["count"], 0)

# (d) a branch with no tracking ref but origin/<branch> exists still compares
git(app, "checkout", "-q", "-b", "feat")
commit(app, "feat 1")
git(app, "push", "-q", "origin", "feat")   # no -u: no upstream set
commit(app, "feat 2")
u = server.unpushed_commits(p)
check("no tracking, origin/feat", (u["branch"], u["upstream"], u["count"]), ("feat", "origin/feat", 1))

# (e) a branch never pushed: nothing to compare with
git(app, "checkout", "-q", "-b", "local-only")
check("never pushed", server.unpushed_commits(p)["upstream"], None)

# (f) detached HEAD
git(app, "checkout", "-q", "--detach")
check("detached", (server.unpushed_commits(p)["branch"], server.unpushed_commits(p)["upstream"]), (None, None))

# (g) more than UNPUSHED_MAX: count is the full number, the list is capped
git(app, "checkout", "-q", "main")
for i in range(server.UNPUSHED_MAX + 3):
    commit(app, f"c{i}")
u = server.unpushed_commits(p)
check("capped", (u["count"], len(u["commits"])), (server.UNPUSHED_MAX + 3, server.UNPUSHED_MAX))

# (h) not a git repo
plain = os.path.join(TMP, "plain")
os.makedirs(plain)
check("not a repo", server.unpushed_commits(SimpleNamespace(path=plain))["upstream"], None)

print("PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
