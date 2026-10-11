"""The Update button installs GitHub's commits even when the app copy has its own commits or edits,
and backs out cleanly when they clash.

Run: python3 tests/test_update_merge.py  (stdlib + git; temp repos, a fresh temp HOME)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="update-merge-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}.items():
    os.environ[k] = v
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402


def git(cwd, *a):
    r = subprocess.run(["git", "-C", cwd, *a], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f"git {a} failed: {r.stderr}")
    return r.stdout.strip()


def write(path, text):
    with open(path, "w") as f:
        f.write(text)


def read(path):
    with open(path) as f:
        return f.read()


def setup(name):
    """origin (bare) + 'gh' (pushes upstream commits) + 'app' (the installed copy), all on main."""
    root = os.path.join(TMP, name)
    origin, gh, app = (os.path.join(root, x) for x in ("origin.git", "gh", "app"))
    os.makedirs(root)
    git(root, "init", "-q", "--bare", "-b", "main", origin)
    git(root, "clone", "-q", origin, gh)
    write(os.path.join(gh, "a.txt"), "a1\n")
    write(os.path.join(gh, "b.txt"), "b1\n")
    git(gh, "add", ".")
    git(gh, "commit", "-qm", "base")
    git(gh, "push", "-q", "origin", "HEAD:main")
    git(root, "clone", "-q", origin, app)
    return gh, os.path.realpath(app)


def upstream_change(gh, file, text):
    write(os.path.join(gh, file), text)
    git(gh, "commit", "-qam", "upstream " + file)
    git(gh, "push", "-q", "origin", "HEAD:main")


def pull(app):
    server.APP = app
    u = server.Updater()
    try:
        return u.pull(), None
    except (PermissionError, RuntimeError) as e:
        return None, str(e)


fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print(("ok  " if ok else "FAIL"), name, "" if ok else detail)


# 1. Own commit + unrelated uncommitted edit: merged, edit kept.
gh, app = setup("ahead-dirty")
upstream_change(gh, "a.txt", "a2\n")
write(os.path.join(app, "b.txt"), "b-local\n")
git(app, "commit", "-qam", "own commit")
write(os.path.join(app, "c.txt"), "untracked\n")
write(os.path.join(app, "b.txt"), "b-edit\n")
server.APP = app
st = server.update_status()
check("status: ahead+dirty can update", st["canUpdate"] and st["ahead"] == 1 and st["behind"] == 1 and st["dirty"], st)
res, err = pull(app)
check("ahead+dirty: merged", err is None and read(os.path.join(app, "a.txt")) == "a2\n", err)
check("ahead+dirty: local edit kept", read(os.path.join(app, "b.txt")) == "b-edit\n")
check("ahead+dirty: own commit kept", "own commit" in git(app, "log", "--format=%s"))
check("ahead+dirty: now up to date", server.update_status()["behind"] == 0)

# 2. Behind only, dirty unrelated: fast-forward, no merge commit.
gh, app = setup("ff-dirty")
upstream_change(gh, "a.txt", "a2\n")
write(os.path.join(app, "b.txt"), "b-edit\n")
res, err = pull(app)
check("ff+dirty: fast-forwarded", err is None and git(app, "rev-parse", "HEAD") == git(gh, "rev-parse", "HEAD"), err)
check("ff+dirty: local edit kept", read(os.path.join(app, "b.txt")) == "b-edit\n")

# 3. Own commit clashes with upstream: backed out, copy unchanged and clean of merge state.
gh, app = setup("clash")
upstream_change(gh, "a.txt", "a-upstream\n")
write(os.path.join(app, "a.txt"), "a-local\n")
git(app, "commit", "-qam", "own clash")
before = git(app, "rev-parse", "HEAD")
res, err = pull(app)
check("clash: reported", err is not None and "clashes" in err, err)
check("clash: HEAD unchanged", git(app, "rev-parse", "HEAD") == before)
check("clash: no merge left open", not os.path.exists(os.path.join(app, ".git", "MERGE_HEAD"))
      and git(app, "status", "--porcelain") == "")

# 4. Uncommitted edit to a file the update changes: refused, edit untouched.
gh, app = setup("dirty-overlap")
upstream_change(gh, "a.txt", "a2\n")
write(os.path.join(app, "a.txt"), "a-edit\n")
before = git(app, "rev-parse", "HEAD")
res, err = pull(app)
check("dirty overlap: reported", err is not None and "edited here" in err, err)
check("dirty overlap: edit and HEAD untouched", read(os.path.join(app, "a.txt")) == "a-edit\n"
      and git(app, "rev-parse", "HEAD") == before)

print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
