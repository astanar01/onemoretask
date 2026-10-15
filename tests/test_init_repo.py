"""Create repo recovers from a first save that was stopped halfway (it leaves .git/index.lock behind).

Run: python3 tests/test_init_repo.py  (stdlib + git; temp folders, a fresh temp HOME)."""
import os
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="init-repo-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
             "GIT_COMMITTER_EMAIL": "t@t", "GIT_CONFIG_NOSYSTEM": "1"}.items():
    os.environ[k] = v
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok  " if ok else "FAIL") + f" {name}: {got!r}" + ("" if ok else f" (want {want!r})"))


def project(name, nfiles):
    path = os.path.join(TMP, name)
    os.makedirs(os.path.join(path, ".task_board"))
    for i in range(nfiles):
        with open(os.path.join(path, f"f{i}.bin"), "wb") as f:
            f.write(os.urandom(64 * 1024))
    return SimpleNamespace(path=path, board=os.path.join(path, ".task_board"), id=name)


# The real failure: git add is killed by the timeout and leaves index.lock behind.
p = project("big", 1500)
server.git_run(p.path, "init", "-q")
try:
    server.git_run(p.path, "add", "-A", timeout=0.05)
    print("SKIP could not stop git add in time; making the lock by hand")
    open(os.path.join(p.path, ".git", "index.lock"), "w").close()
except RuntimeError as e:
    check("git add stopped", str(e), "git add timed out")
check("stopped run leaves index.lock", os.path.isfile(os.path.join(p.path, ".git", "index.lock")), True)

info = server.init_repo(p)
check("init after stopped run has a commit", info["commits"], True)
check("lock gone", os.path.isfile(os.path.join(p.path, ".git", "index.lock")), False)
tracked = subprocess.run(["git", "-C", p.path, "ls-files"], capture_output=True, text=True).stdout.split()
check("all files saved", len(tracked), 1500)

# A second click while the first is still running gets a clear message, not git's lock error.
q = project("busy", 1)
key = os.path.realpath(q.path)
server._init_busy.add(key)
try:
    server.init_repo(q)
    check("second click refused", "no error", "error")
except RuntimeError as e:
    check("second click refused", "still being created" in str(e), True)
server._init_busy.discard(key)
check("works once the first is done", server.init_repo(q)["commits"], True)

# Two at once: one creates, the other is refused; no lock is left.
r = project("race", 300)
out = []
ts = [threading.Thread(target=lambda: out.append(_try(r))) for _ in range(2)]


def _try(pp):
    try:
        return server.init_repo(pp)["commits"]
    except RuntimeError as e:
        return str(e)


for t in ts:
    t.start()
for t in ts:
    t.join()
check("race: one created", out.count(True) >= 1, True)
check("race: no lock left", os.path.isfile(os.path.join(r.path, ".git", "index.lock")), False)

# The scan offers the heavy folders; only the ticked ones stay out of the first commit.
server.BIG = 1000
w = project("wp", 0)
for d, name, n in [("wp-content/uploads/2024", "a.jpg", 3000), ("wp-content/updraft", "backup.zip", 4000),
                   ("wp-content/themes/t", "style.css", 10), ("", "wp-config.php", 10),
                   ("nested", "big.bin", 9000), ("odd[1]", "x", 2000)]:
    os.makedirs(os.path.join(w.path, d), exist_ok=True)
    open(os.path.join(w.path, d, name), "w").write("x" * n)
nested = os.path.join(w.path, "nested")   # a repo of its own: committed as a pointer, so not offered
subprocess.run(["git", "-C", nested, "init", "-q"], check=True)
subprocess.run(["git", "-C", nested, "add", "-A"], check=True)
subprocess.run(["git", "-C", nested, "commit", "-qm", "n"], check=True)
scan = server.scan_sizes(w)
check("scan: big parts, biggest first, nested repo and board skipped",
      [(e["path"], e["dir"]) for e in scan["entries"]],
      [("wp-content/updraft", True), ("wp-content/uploads", True), ("odd[1]", True)])
check("scan: total", scan["total"], 3000 + 4000 + 10 + 10 + 2000)
server.init_repo(w, ["wp-content/updraft", "odd[1]", "../outside", "/etc", "missing"])
tracked = sorted(subprocess.run(["git", "-C", w.path, "ls-files"], capture_output=True, text=True).stdout.split())
check("ticked left out, the rest saved", tracked,
      ["nested", "wp-config.php", "wp-content/themes/t/style.css", "wp-content/uploads/2024/a.jpg"])
excl = open(os.path.join(w.path, ".git", "info", "exclude")).read()
check("nothing outside the folder excluded", ("outside" in excl, "etc" in excl, "missing" in excl), (False, False, False))

print("FAILED" if fails else "PASSED", f"({fails} failing)")
sys.exit(1 if fails else 0)
