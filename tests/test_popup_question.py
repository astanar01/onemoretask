"""A question asked in Claude's AskUserQuestion pop-up reaches the card, and new sessions cannot open that pop-up.

The pop-up shows only inside the session: the board saw "blocked" and the CLI's one-line summary ("awaiting 4
decisions: ..."), never the questions or the choices. The job's state.json shape below is the one a real
`claude --bg` session wrote while on the pop-up (CLI v2.1.300).

Run: python3 tests/test_popup_question.py  (stdlib only; a fresh temp HOME, sessions faked)."""
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="popup-question-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

STATE = {"state": "working", "detail": "awaiting 2 decisions: color, size", "needs": "answer: Pick a color (Red · Blue)",
         "block": {"questions": [
             {"question": "Pick a color", "options": [{"label": "Red", "description": "Choose red"},
                                                      {"label": "Blue", "description": "Choose blue"}]},
             {"question": "Pick a size", "options": [{"label": "Small", "description": "Choose small"},
                                                     {"label": "Large", "description": ""}]}]}}
WANT = [{"q": "Pick a color", "options": ["Red", "Blue"]}, {"q": "Pick a size", "options": ["Small", "Large"]}]

try:
    proj = os.path.join(TMP, "proj")
    os.makedirs(proj)
    p = server.Project(proj)
    os.makedirs(p.board)
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump({"columns": [{"id": "c", "name": "To Do"}],
                   "tasks": [{"id": "t1", "title": "Asks", "column": "c", "agent": {"id": "a1", "started": ""}},
                             {"id": "t2", "title": "Plain stop", "column": "c", "agent": {"id": "a2", "started": ""}}]}, f)
    for t in ("t1", "t2"):
        reports.append(p.board, t, "launch", "Sent to Claude", "you")
    for job, state in (("a1", STATE), ("a2", {"state": "working", "detail": "awaiting your review"})):
        os.makedirs(os.path.join(os.environ["HOME"], ".claude", "jobs", job))
        with open(os.path.join(os.environ["HOME"], ".claude", "jobs", job, "state.json"), "w", encoding="utf-8") as f:
            json.dump(state, f)
    sessions = [{"id": "a1", "sessionId": "s1", "status": "waiting", "waitingFor": "input needed", "state": "blocked"},
                {"id": "a2", "sessionId": "s2", "status": "idle", "state": "blocked"}]
    server.projects = lambda: [p]
    server.list_sessions = lambda cwd: [dict(s) for s in sessions]
    server.attach.available = lambda: False
    fails = []

    for _ in range(2):  # a second pass must not post the question again
        server.WATCH.poll()
    asked = [e for e in reports.read(p.board, "t1") if e.get("status") == "question"]
    if len(asked) != 1:
        fails.append(f"t1: want 1 question entry, got {len(asked)}")
    elif asked[0].get("parts") != WANT or asked[0].get("from") != "claude":
        fails.append(f"t1: wrong entry {asked[0]}")
    elif "- Red: Choose red" not in asked[0]["message"] or "- Large\n" not in asked[0]["message"] + "\n":
        fails.append(f"t1: descriptions missing from the message: {asked[0]['message']!r}")
    snap = server.WATCH.snapshot(p.id)
    if snap["t1"]["phase"] != "needs_you" or "Pick a color" not in snap["t1"]["reason"]:
        fails.append(f"t1: card should need you with the question, got {snap['t1']['phase']} {snap['t1']['reason']!r}")
    # Negative control: blocked but not on a pop-up -> no question invented, the CLI summary shows as before.
    if any(e.get("status") == "question" for e in reports.read(p.board, "t2")):
        fails.append("t2: a question was posted for a session not on a pop-up")
    if "awaiting your review" not in snap["t2"]["reason"]:
        fails.append(f"t2: reason changed: {snap['t2']['reason']!r}")
    print("ok  pop-up relay" if not fails else "")

    # The launch turns the pop-up off, and the prompt stays the last argument (--disallowedTools takes many values).
    seen = []
    server.run_claude = lambda args, **kw: seen.append(args) or types.SimpleNamespace(
        stdout="backgrounded · abc123", stderr="", returncode=0)
    server.start_session(p, "t1", "name", "the prompt", "")
    args = seen[0]
    i = args.index("--disallowedTools") if "--disallowedTools" in args else -1
    if i < 0 or args[i + 1] != "AskUserQuestion" or args[-1] != "the prompt" or not args[i + 2].startswith("-"):
        fails.append(f"launch args: {args}")
    else:
        print("ok  launch turns AskUserQuestion off:", " ".join(args[:-1]))
    if fails:
        raise SystemExit("FAIL " + "; ".join(fails))
finally:
    shutil.rmtree(TMP, ignore_errors=True)
print("PASS")
