"""Card logs and report.py: a broken log is kept aside, an image is never left cut off, `-` reads stdin,
`--file` reads a file, and a read-only board gets a retry hint.

    python3 tests/test_review_scripts_logs.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import reports  # noqa: E402

tmp = tempfile.mkdtemp()
fails = []


def check(name, ok):
    print(("ok  " if ok else "FAIL") + " " + name)
    if not ok:
        fails.append(name)


def run(*args, stdin=""):
    return subprocess.run([sys.executable, os.path.join(ROOT, "report.py"), "--board", board, *args], input=stdin,
                          capture_output=True, text=True, cwd=tmp, env=dict(os.environ, HOME=tmp))


try:
    board = os.path.join(tmp, "board")
    reports.ensure_board(board)
    folder = os.path.join(board, "agent_reports")
    os.makedirs(folder)

    # 1. A log that does not parse is moved aside, not overwritten; a `{}` log does not crash append.
    for tid, raw in (("bad", "[{\"at\": \"x\", \"message\": \"old work\"}, "), ("obj", "{}")):
        path = reports._path(board, tid)
        with open(path, "w", encoding="utf-8") as f:
            f.write(raw)
        try:
            reports.append(board, tid, "progress", "new", "claude")
        except Exception as e:
            check(f"append on a {raw[:6]!r} log ({e!r})", False)
            continue
        log = reports.read(board, tid)
        check(f"append on a {raw[:6]!r} log keeps the new entry", [e["message"] for e in log] == ["new"])
        check(f"the {raw[:6]!r} log is kept at .bad",
              os.path.exists(path + ".bad") and open(path + ".bad", encoding="utf-8").read() == raw)
    path = reports._path(board, "bad")
    with open(path, "w", encoding="utf-8") as f:
        f.write("broken again")
    reports.append(board, "bad", "progress", "newer", "claude")
    check("a second broken log does not clobber the first .bad",
          os.path.exists(path + ".bad2") and open(path + ".bad", encoding="utf-8").read().startswith("[{")
          and open(path + ".bad2", encoding="utf-8").read() == "broken again")
    with open(reports._path(board, "obj2"), "w", encoding="utf-8") as f:
        f.write('{"a": 1}')
    check("read() treats a non-list log as empty", reports.read(board, "obj2") == [])

    # 2. A write that dies half way leaves no image under the final name.
    data = b"\x89PNG" + b"x" * 5000
    imgs = os.path.join(tmp, "imgs")
    real_open, real_fdopen = open, os.fdopen

    class Dying:
        def __init__(self, f):
            self.f = f

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.f.close()

        def write(self, b):
            self.f.write(b[:100])
            raise OSError("disk full")

    reports.open = lambda *a, **k: Dying(real_open(*a, **k))
    os.fdopen = lambda *a, **k: Dying(real_fdopen(*a, **k))
    try:
        reports.store_image(data, imgs)
        check("store_image raised on the failing write", False)
    except OSError:
        pass
    finally:
        del reports.open
        os.fdopen = real_fdopen
    check("no cut-off image or temp file left behind", os.listdir(imgs) == [])
    name = reports.store_image(data, imgs)
    check("the next store writes the whole image", open(os.path.join(imgs, name), "rb").read() == data)

    # 3. A lone `-` reads the message from stdin for every status.
    for status in ("progress", "question", "done"):
        r = run("--lang", "English", "t1", status, "-", stdin=f"{status} from stdin\n")
        check(f"{status} - posts the stdin text", r.returncode == 0
              and reports.read(board, "t1")[-1]["message"] == f"{status} from stdin")
    r = run("t1", "progress", "-", stdin="  \n")
    check("progress - with empty stdin is refused", r.returncode != 0 and "Traceback" not in r.stderr)
    dec = {"query": "Which?", "options": [{"label": "A"}, {"label": "B"}], "chosen": 0}
    r = run("t1", "decision", "-", stdin=json.dumps(dec))
    check("decision - still reads JSON from stdin",
          r.returncode == 0 and reports.read(board, "t1")[-1].get("decision", {}).get("query") == "Which?")
    r = run("t1", "decision", json.dumps(dec))
    check("decision with inline JSON still works", r.returncode == 0)
    n = len(reports.read(board, "t1"))
    r = run("t1", "progress", "plain words")
    check("a normal message is posted as given",
          r.returncode == 0 and len(reports.read(board, "t1")) == n + 1)

    # 4. `--file` reads the message from a file: the form an answer-only sandbox lets through.
    msg = os.path.join(tmp, "answer.md")
    with open(msg, "w", encoding="utf-8") as f:
        f.write("Use `git worktree add` and $(x) here.\n\n- **bold** < more\n")
    r = run("--lang", "English", "t1", "done", "--file", msg)
    check("done --file posts the file's text as is", r.returncode == 0
          and reports.read(board, "t1")[-1]["message"] == "Use `git worktree add` and $(x) here.\n\n- **bold** < more")
    with open(msg, "w", encoding="utf-8") as f:
        json.dump(dec | {"query": "From a file?"}, f)
    r = run("t1", "decision", "--file", msg)
    check("decision --file reads the JSON",
          r.returncode == 0 and reports.read(board, "t1")[-1].get("decision", {}).get("query") == "From a file?")
    for args in (("t1", "done", "--file", os.path.join(tmp, "missing.md")), ("t1", "done", "text", "--file", msg),
                 ("t1", "done", "--file")):
        r = run(*args)
        check(f"refused without a traceback: {args[2:]}", r.returncode != 0 and "Traceback" not in r.stderr)

    # 5. A board the shell may not write to (the answer-only sandbox) says how to retry, not a traceback.
    n = len(reports.read(board, "t1"))
    os.chmod(folder, 0o555)
    try:
        r = run("--lang", "English", "t1", "done", "the answer")
    finally:
        os.chmod(folder, 0o755)
    check("a refused write explains the --file retry", r.returncode != 0 and "Traceback" not in r.stderr
          and "--file <that file>" in r.stderr and f"--board {board} t1 done" in r.stderr)
    check("nothing was posted", len(reports.read(board, "t1")) == n)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

sys.exit(1 if fails else 0)
