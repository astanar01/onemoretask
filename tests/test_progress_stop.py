"""inbox.py blocks a turn that ends right after a progress report (the card would look stopped while it says it
is still working), once, and lets every other stop through.

Run: python3 tests/test_progress_stop.py  (stdlib only; a temp board)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import reports  # noqa: E402

fails = []


def check(name, ok):
    print(("ok  " if ok else "FAIL") + " " + name)
    if not ok:
        fails.append(name)


def stop(board, task, **extra):
    event = {"hook_event_name": "Stop", "session_id": "s1", **extra}
    r = subprocess.run([sys.executable, os.path.join(ROOT, "inbox.py"), "--board", board, task],
                       input=json.dumps(event), capture_output=True, text=True)
    assert r.returncode == 0 and "Traceback" not in r.stderr, r.stderr
    return json.loads(r.stdout) if r.stdout else None


board = tempfile.mkdtemp(prefix="progress-stop-")

check("no reports: the stop goes through", stop(board, "t0") is None)

reports.append(board, "t1", "launch", "Sent to Claude", "you")
reports.append(board, "t1", "progress", "Working on fix 1", "claude")
out = stop(board, "t1")
check("ends right after progress: the stop is blocked", out is not None and out.get("decision") == "block")
check("the reason says to carry on or post done or question",
      out is not None and "still working" in out["reason"] and "`done`" in out["reason"]
      and "`question`" in out["reason"])
check("a second stop in a row (stop_hook_active) goes through", stop(board, "t1", stop_hook_active=True) is None)
check("PostToolUse after progress adds nothing",
      subprocess.run([sys.executable, os.path.join(ROOT, "inbox.py"), "--board", board, "t1"],
                     input=json.dumps({"hook_event_name": "PostToolUse"}), capture_output=True,
                     text=True).stdout == "")
check("a subagent's stop is skipped", stop(board, "t1", agent_id="a1") is None)

for status in ("done", "question", "decision", "card"):
    reports.append(board, "t2", "progress", "Working", "claude")
    reports.append(board, "t2", status, "Finished" if status == "done" else "x", "claude")
    check(f"ends after {status}: the stop goes through", stop(board, "t2") is None)

reports.append(board, "t3", "progress", "Working", "claude")
reports.append(board, "t3", "reply", "Looks good", "you")
check("a user reply after the progress: not this check's business", stop(board, "t3") is None)

reports.append(board, "t4", "progress", "Working", "claude")
reports.append(board, "t4", "note", "Also check the footer", "you")
out = stop(board, "t4")
check("a waiting card note still wins: the block carries the note",
      out is not None and out.get("decision") == "block" and "footer" in out["reason"])

print("\n" + (f"{len(fails)} failed" if fails else "all passed"))
sys.exit(1 if fails else 0)
