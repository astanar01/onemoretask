"""A reply that reaches a card in Done moves it to In Progress on the server, whoever sent it.

The board page moved the card itself before posting (index.html sendReply); mage's cutscene editor posts straight to
/api/agent/reply, so Claude went back to work on a card left in Done (2026-11-20, card mwh29qyelpu03).

Run: python3 tests/test_reply_out_of_done.py  (stdlib only; a fresh temp HOME, every session call faked)."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="reply-out-of-done-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print("ok  " if ok else "FAIL", name, "" if ok else f"(got {got!r}, want {want!r})")


def board(name, columns, tasks):
    folder = os.path.join(TMP, name)
    os.makedirs(folder)
    p = server.Project(folder)
    os.makedirs(p.board)
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "columns": columns, "tasks": tasks, "milestones": [{"id": "m"}]}, f)
    return p


def card(p, tid):
    return next(t for t in server.load_tasks(p)["tasks"] if t["id"] == tid)


COLS = [{"id": "todo", "name": "To Do"}, {"id": "prog", "name": "In Progress", "role": "progress"},
        {"id": "rev", "name": "Review", "role": "review"}, {"id": "done", "name": "Done", "done": True}]
agent = {"id": "a0", "started": ""}
p = board("roles", COLS, [
    {"id": "archived", "title": "Closed card", "column": "done", "order": 3, "agent": dict(agent)},
    {"id": "sub", "title": "Its subtask", "column": "done", "order": 4, "parent": "archived"},
    {"id": "subsub", "title": "Subtask of the subtask", "column": "done", "order": 5, "parent": "sub"},
    {"id": "other_done", "title": "Another done card", "column": "done", "order": 1},
    {"id": "working", "title": "Done but still working", "column": "done", "order": 2, "agent": dict(agent, id="a1")},
    {"id": "busy", "title": "Already in progress", "column": "prog", "order": 0, "agent": dict(agent, id="a2")},
    {"id": "nosession", "title": "Done, never sent", "column": "done", "order": 6},
])
reports.append(p.board, "archived", "recap", "## Goal\nDo the thing.", "claude")

infos = {"archived": {"id": "a0", "phase": "idle", "archived": True},
         "working": {"id": "a1", "sessionId": "s1", "phase": "working"},
         "busy": {"id": "a2", "sessionId": "s2", "phase": "working"}}
server.WATCH.snapshot = lambda pid: dict(infos)
reopened = []
server.reopen = lambda p_, tid, text, images=(): reopened.append(tid) or "new1"
server.resume = lambda *a, **k: (_ for _ in ()).throw(AssertionError("resume must not run in this test"))
server.run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))

# A closed card (the cutscene editor's case): reopened, and now in progress, at the top, its subtasks under it.
server.reply(p, "archived", "Apply my comments")
check("closed card: reopened", reopened, ["archived"])
check("closed card: in progress", card(p, "archived")["column"], "prog")
check("closed card: its subtasks came along", [card(p, x)["column"] for x in ("sub", "subsub")], ["prog", "prog"])
order = sorted(server.load_tasks(p)["tasks"], key=lambda t: t["order"])
check("closed card: top of the column, subtasks right under it",
      [t["id"] for t in order if t["column"] == "prog"], ["archived", "sub", "subsub", "busy"])
check("other done card untouched", card(p, "other_done")["column"], "done")
check("milestones kept", server.load_tasks(p)["milestones"], [{"id": "m"}])

# A done card whose session still works: the reply is a note, and the card still moves.
check("working card: queued as a note", server.reply(p, "working", "One more thing"), True)
check("working card: in progress", card(p, "working")["column"], "prog")

# A card already in progress: no move, order kept.
before = card(p, "busy")["order"]
server.reply(p, "busy", "Note")
check("in-progress card stays", (card(p, "busy")["column"], card(p, "busy")["order"]), ("prog", before))

# No session for the card: the reply fails and the card does not move.
try:
    server.reply(p, "nosession", "Hello")
    check("no session: refused", "no error", "LookupError")
except LookupError:
    check("no session: refused", "LookupError", "LookupError")
check("no session: card stays in done", card(p, "nosession")["column"], "done")

# An old board with no roles: the column named like "In Progress" (index.html colRole).
q = board("names", [{"id": "t", "name": "Backlog"}, {"id": "d", "name": "Doing"}, {"id": "x", "name": "Done", "done": True}],
          [{"id": "c", "title": "Old board card", "column": "x", "order": 0, "agent": dict(agent)}])
check("no roles: moved to the column named Doing", (server.out_of_done(q, "c"), card(q, "c")["column"]), (True, "d"))
r = board("nomatch", [{"id": "t", "name": "Backlog"}, {"id": "x", "name": "Done", "done": True}],
          [{"id": "c", "title": "Nowhere to go", "column": "x", "order": 0}])
check("no progress column: stays", (server.out_of_done(r, "c"), card(r, "c")["column"]), (False, "x"))

print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
