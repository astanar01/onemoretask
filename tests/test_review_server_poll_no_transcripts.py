"""A watcher pass survives a missing ~/.claude/projects while a busy card holds a note.

turn_ended_at ran outside the OSError guard every other transcript read has: os.listdir on the missing folder
aborted the whole pass, so no card got fresh state.

Run: python3 tests/test_review_server_poll_no_transcripts.py  (stdlib only; a fresh temp HOME, sessions faked)."""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="poll-no-transcripts-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

try:
    assert not os.path.exists(server.PROJECTS_DIR), server.PROJECTS_DIR
    proj = os.path.join(TMP, "proj")
    os.makedirs(proj)
    p = server.Project(proj)
    os.makedirs(p.board)
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump({"columns": [{"id": "c", "name": "To Do"}],
                   "tasks": [{"id": "t1", "title": "Busy", "column": "c", "agent": {"id": "a1", "started": ""}},
                             {"id": "t2", "title": "Other", "column": "c", "agent": {"id": "a2", "started": ""}}]}, f)
    reports.append(p.board, "t1", "note", "also check the logs", "you")
    sessions = [{"id": "a1", "sessionId": "s1", "state": "working", "status": "busy"},
                {"id": "a2", "sessionId": "s2", "state": "idle", "status": "idle"}]
    server.projects = lambda: [p]
    server.list_sessions = lambda cwd: [dict(s) for s in sessions]
    server.attach.available = lambda: False
    try:
        server.WATCH.poll()
    except OSError as e:
        raise SystemExit(f"FAIL the pass aborted: {e!r}")
    snap = server.WATCH.snapshot(p.id)
    if set(snap) != {"t1", "t2"}:
        raise SystemExit(f"FAIL cards missing from the pass: {sorted(snap)}")
    print("ok  pass finished with no transcripts folder; both cards:", {k: v["phase"] for k, v in snap.items()})
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print("PASS")
