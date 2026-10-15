"""Odd inputs to the agent-side scripts: report.py, inbox.py and codex.last_answer.

    python3 tests/test_review_scripts_inputs.py
"""
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import codex  # noqa: E402
import reports  # noqa: E402

tmp = tempfile.mkdtemp()
fails = []


def check(name, ok):
    print(("ok  " if ok else "FAIL") + " " + name)
    if not ok:
        fails.append(name)


def run(script, *args, stdin=""):
    return subprocess.run([sys.executable, os.path.join(ROOT, script), *args], input=stdin, capture_output=True,
                          text=True, cwd=tmp, env=dict(os.environ, HOME=tmp))


try:
    board = os.path.join(tmp, "board")
    reports.ensure_board(board)

    log = os.path.join(tmp, "s.jsonl")
    with open(log, "w") as f:
        f.write('{"type":"turn.started"}\n'
                '{"type":"item.completed","item":{"type":"agent_message","text":null}}\n'
                '{"type":"item.completed","item":{"type":"agent_message","text":"hi"}}\n{"type":"item.comp')
    try:
        check("codex.last_answer skips a null text", codex.last_answer(log) == "hi")
    except Exception as e:
        check(f"codex.last_answer skips a null text ({e!r})", False)

    for raw in ("null", "[]", '"x"', "not json"):
        r = run("inbox.py", "--board", board, "abc", stdin=raw)
        check(f"inbox.py exits 0 on stdin {raw!r}", r.returncode == 0 and "Traceback" not in r.stderr)

    png = os.path.join(tmp, "a.png")
    with open(png, "wb") as f:
        f.write(b"\x89PNG" + b"0" * 10)
    r = run("report.py", "--board", board, "--lang", "English", "--image", png, "Bad-Id", "progress", "hello")
    check("report.py refuses a bad task id without a traceback",
          r.returncode != 0 and "bad task id" in r.stderr and "Traceback" not in r.stderr)
    check("report.py stores no image for a bad task id", not os.path.isdir(reports.log_images(board)))

    for msg in ("", "   "):
        r = run("report.py", "--board", board, "t1", "done", msg)
        check(f"report.py refuses message {msg!r}", r.returncode != 0 and "Traceback" not in r.stderr)
    check("nothing posted for empty messages", reports.read(board, "t1") == [])

    r = run("report.py", "--board", board, "t1", "progress", "working on it")
    check("report.py still posts a normal report", r.returncode == 0 and len(reports.read(board, "t1")) == 1)
finally:
    shutil.rmtree(tmp, ignore_errors=True)

sys.exit(1 if fails else 0)
