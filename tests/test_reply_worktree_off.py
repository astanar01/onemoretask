"""A reply sent with "worktree": false switches the card's "Use a worktree" off, and Claude goes on in the project folder.

mage's cutscene editor sends its notes to a card; the user previews the result in Godot, which opens the project
folder, so work done in a worktree is invisible to them (2026-11-20). Before, a reply to a closed card made a fresh
worktree whatever the card's switch said (renew_worktree read only the launch record).

Run: python3 tests/test_reply_worktree_off.py  (stdlib only; a fresh temp HOME, every session call faked)."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="reply-worktree-off-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print("ok  " if ok else "FAIL", name, "" if ok else detail)


proj = os.path.join(TMP, "proj")
os.makedirs(proj)
p = server.Project(proj)
os.makedirs(p.board)


def wt(name, exists):
    path = os.path.join(TMP, "wt", name)
    if exists:
        os.makedirs(path)
    return {"path": path, "dir": path, "branch": "br-" + name, "base": "main"}


def agent(aid, w):
    return {"id": aid, "started": "", "worktree": w}


cards = [
    {"id": "closed", "title": "Closed, worktree still there", "column": "done", "order": 0, "agent": agent("a0", wt("closed", True))},
    {"id": "gone", "title": "Closed, worktree merged away", "column": "done", "order": 1, "agent": agent("a1", wt("gone", False))},
    {"id": "control", "title": "Closed, worktree merged away, plain reply", "column": "done", "order": 2,
     "agent": agent("a2", wt("control", False))},
    {"id": "working", "title": "Working in its worktree", "column": "prog", "order": 0, "agent": agent("a3", wt("working", True))},
    {"id": "idle", "title": "Idle in its worktree", "column": "prog", "order": 1, "agent": agent("a4", wt("idle", True))},
]
with open(p.data, "w", encoding="utf-8") as f:
    json.dump({"version": 1, "tasks": cards, "columns": [
        {"id": "todo", "name": "To Do"}, {"id": "prog", "name": "In Progress", "role": "progress"},
        {"id": "done", "name": "Done", "done": True}]}, f)
for tid in ("closed", "gone", "control"):
    reports.append(p.board, tid, "recap", "## Goal\nDo the thing.", "claude")
    with open(reports.recap_path(p.board, tid), "w", encoding="utf-8") as f:
        f.write("## Goal\nDo the thing.\n")

server.WATCH.snapshot = lambda pid: {
    "closed": {"id": "a0", "phase": "idle", "archived": True},
    "gone": {"id": "a1", "phase": "idle", "archived": True},
    "control": {"id": "a2", "phase": "idle", "archived": True},
    "working": {"id": "a3", "sessionId": "s3", "phase": "working"},
    "idle": {"id": "a4", "sessionId": "s4", "phase": "finished"},
}
prompts, made, resumed = {}, [], {}
server.start_session = lambda p_, tid, name, prompt, *a, **k: prompts.__setitem__(tid, prompt) or "new-" + tid
server.make_worktree = lambda p_, t, made_=None, name=None: made.append(t["id"]) or wt("fresh-" + t["id"], True)
server.resume = lambda p_, info, message: resumed.__setitem__(info["id"], message) or "res-" + info["id"]
server.run_claude = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))
server.git_try = lambda *a, **k: None
server.default_branch = lambda path: "main"


def card(tid):
    return next(t for t in server.load_tasks(p)["tasks"] if t["id"] == tid)


OFF = "\"Use a worktree\" is now off for this card"

# A closed card whose worktree still holds its branch: the new session works in the project folder and is told to
# merge that branch first.
server.reply(p, "closed", "Apply my comments", worktree=False)
check("closed: the card's switch is off", card("closed").get("worktree") is False)
pr = prompts.get("closed", "")
check("closed: new session told to work in the project folder", "Work in the project folder itself" in pr, pr[-600:])
check("closed: not told to work in its worktree", "Commit your work in the worktree" not in pr)
check("closed: told to merge branch br-closed into the project folder", OFF in pr and "br-closed" in pr)

# A closed card whose worktree was merged away: no fresh worktree is made.
server.reply(p, "gone", "Apply my comments", worktree=False)
check("merged away: no fresh worktree", made == [], f"made {made}")
check("merged away: works in the project folder", "Work in the project folder itself" in prompts.get("gone", ""))
check("merged away: nothing to merge, no merge line", OFF not in prompts.get("gone", ""))

# Control: the same card with a plain reply still gets its fresh worktree (the fake is reached).
server.reply(p, "control", "Apply my comments")
check("control: a plain reply renews the worktree", made == ["control"], f"made {made}")
check("control: switch left on", card("control").get("worktree", True) is True)

# A working session in its worktree: the note carries the switch once.
server.reply(p, "working", "Apply my comments", worktree=False)
server.reply(p, "working", "And this", worktree=False)
notes = [e["message"] for e in reports.read(p.board, "working") if e.get("status") == "note"]
check("working: first note tells it to merge and move", len(notes) == 2 and OFF in notes[0] and "br-working" in notes[0], notes)
check("working: second note does not repeat it", len(notes) == 2 and OFF not in notes[1], notes)

# An idle session in its worktree: the resume message carries it exactly once (board_tail, not twice).
server.reply(p, "idle", "Apply my comments", worktree=False)
msg = resumed.get("a4", "")
check("idle: resume tells it once", msg.count(OFF) == 1, msg.count(OFF))
check("idle: switch off", card("idle").get("worktree") is False)

print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
