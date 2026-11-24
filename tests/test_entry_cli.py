"""Each card message names the CLI that wrote it: a Codex review on Claude's work reads as Codex, the replies to a
session stay with its CLI, and old logs (no "cli" on the launch or review line, session long gone) are read from the
line's text.

Run: python3 tests/test_entry_cli.py  (stdlib only; a fresh temp HOME, no session calls)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["HOME"] = tempfile.mkdtemp(prefix="entry-cli-test-")  # before importing server: it reads ~ at import
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("PASS" if ok else "FAIL"), name, "->", got, "" if ok else f"(want {want})")


def e(frm, status, msg="m", **kw):
    return {"from": frm, "status": status, "message": msg, "at": "2026-11-23T10:00:00", **kw}


def clis(log, sessions=None, t=None):
    out = server.with_clis(log, sessions or {}, t or {"agent": {"id": "a1", "model": "opus"}})
    return [x.get("cli") for x in out]


# Old log, every session gone: a Claude task, then a Codex review, then a reply that resumes the Claude worker.
old = [e("you", "launch", "Sent to Claude (session a1, model opus)", session="a1"),
       e("claude", "progress"), e("claude", "done"),
       e("you", "review", "Code review started (gpt-5.5, session r1)", session="r1"),
       e("claude", "done", "findings"),
       e("you", "reply", "fix 1", session="a1"), e("claude", "done", "fixed")]
check("old log: task, codex review, back to claude", clis(old),
      [None, "claude", "claude", None, "codex", None, "claude"])

# The same with the review on the task model of an Antigravity card: the card's CLI.
ag = [e("you", "launch", "Sent to Antigravity (session a1, model agy:gemini)", session="a1"), e("claude", "done"),
      e("you", "review", "Code review started (task model, session r1)", session="r1"), e("claude", "done")]
check("antigravity card, review on the task model", clis(ag, t={"agent": {"id": "a1", "model": "agy:gemini", "cli": "antigravity"}}),
      [None, "antigravity", None, "antigravity"])

# New log: the board stores the CLI on the launch and review lines; a live session record wins over the text.
new = [e("you", "launch", "Sent to Claude (session a1)", session="a1", cli="claude"), e("claude", "done"),
       e("you", "review", "Code review started (opus, session r1)", session="r1"), e("claude", "done")]
check("live session record", clis(new, sessions={"r1": {"id": "r1", "cli": "codex"}}),
      ["claude", "claude", None, "codex"])   # the launch line keeps the cli stored on it

# An entry that already names its CLI keeps it; entries from you and the board get none.
check("entry's own cli kept", clis([e("you", "launch", "Sent to Claude", session="a1"), e("claude", "done", cli="codex"),
                                     e("board", "ran")]), [None, "codex", None])

# The log on disk is not changed: the server adds "cli" to copies.
check("input not mutated", "cli" in old[1], False)

# reports.append stores the cli it is given, and none when it is not.
board = tempfile.mkdtemp(prefix="entry-cli-board-")
reports.append(board, "t1", "review", "Code review started", "you", session="r1", cli="codex")
reports.append(board, "t1", "done", "ok", "claude")
got = reports.read(board, "t1")
check("append stores cli", [x.get("cli") for x in got], ["codex", None])

print("FAILED" if fails else "OK", f"({fails} failures)")
sys.exit(1 if fails else 0)
