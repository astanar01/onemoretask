"""A card written on the wrong project's board: POST /api/which-project (Claude compares the card with every
remembered project), POST /api/task/move (the card, its subtasks and pictures go to the other board), and the
board's Send, which asks before starting a card Claude places elsewhere.

Run: python3 tests/test_which_project.py  (stdlib; Claude is faked; node for the board's Send)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="which-project-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += not ok
    print(("ok  " if ok else "FAIL") + f" {name}" + ("" if ok else f": got {got!r}, want {want!r}"))


def fake_claude(answer):
    asks = []

    def run(args, **kw):
        asks.append((args[-1], kw.get("input", "")))
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": answer}), stderr="")
    server.run_claude = run
    return asks


def make_project(name, readme, cards):
    path = os.path.join(TMP, name)
    os.makedirs(path)
    with open(os.path.join(path, "README.md"), "w") as f:
        f.write(readme)
    p = server.open_project(path)
    if cards is not None:
        cols = [{"id": "todo", "name": "To Do", "done": False}, {"id": "prog", "name": "In Progress", "done": False,
                "role": "progress"}, {"id": "done", "name": "Done", "done": True}]
        server.write_board(p, {"version": 1, "columns": cols, "tasks": cards})
    return p


def card(id, title, parent=None, column="todo", order=0, **kw):
    return {"id": id, "title": title, "notes": "", "priority": "", "tags": [], "column": column, "parent": parent,
            "order": order, "created": "2026-10-01", "updated": "2026-10-0" + str(order + 1), **kw}


board = make_project("fablecraft", "# Fablecraft\nA story writing game.",
                     [card("a", "Fix the board's send button"), card("a1", "Sub step", "a", order=1),
                      card("a2", "Done sub", "a", column="done", order=2, images=["0123456789abcdef.png"]),
                      card("k", "Add dragon sprites", order=3), card("s", "Sent one", order=4, agent={"id": "x"})])
other = make_project("onemoretask", "# onemoretask\nA task board for Claude Code.", [card("o", "Changelog button")])

# Only one project: no call at all.
solo = server.Project(board.path)
real_projects = server.projects
server.projects = lambda: [solo]
asks = fake_claude('{"project": 2, "sure": true, "why": "x"}')
check("one project: no check, no call", (server.which_project(solo, "t", "n"), len(asks)), ({}, 0))
server.projects = real_projects

# Claude is sure it is for project 2: that project comes back, with the profiles in the ask.
asks = fake_claude('Sure.\n{"project": 2, "sure": true, "why": "It is about the board app."}')
got = server.which_project(board, "Fix the board's send button", "The send button on onemoretask...", "a")
check("other project returned", (got.get("project", {}).get("id"), got.get("why")), (other.id, "It is about the board app."))
ask, given = asks[0]
check("profiles: README and card titles, the card's own title left out of its board",
      ("A task board for Claude Code." in given, "Changelog button" in given, "Add dragon sprites" in given,
       given.count("Fix the board's send button")), (True, True, True, 1))
check("current project is project 1", "## Project 1: fablecraft" in given, True)

for answer, why in [('{"project": 2, "sure": false}', "not sure"), ('{"project": 1, "sure": true}', "fits here"),
                    ('{"project": 9, "sure": true}', "out of range")]:
    asks = fake_claude(answer)
    check(f"no warning when {why}", (server.which_project(board, "t", "about onemoretask"), len(asks)), ({}, 1))
fake_claude("I think the second one")
try:
    server.which_project(board, "t", "about onemoretask")
    check("no JSON raises", "returned", "raised")
except RuntimeError:
    check("no JSON raises", "raised", "raised")

# No hint of another project in the card: no Claude call at all. A hint: its name, or words only its board uses.
asks = fake_claude('{"project": 2, "sure": true, "why": "x"}')
check("no hint: no call", (server.which_project(board, "More dragon sprites", "Bigger wings for the dragon."), len(asks)), ({}, 0))
check("the other project's name is a hint", server.project_hint(board, [other], "Fix it", "The OneMoreTask list"), True)
check("a longer word holding the name is not", server.project_hint(board, [other], "Fix", "onemoretasks2 thing"), False)
check("words only the other board uses are a hint", server.project_hint(board, [other], "Changelog", "for Claude"), True)
check("words of both boards: no hint", server.project_hint(board, [other], "Story writing", "the changelog"), False)

# Move: the card, its subtasks and picture go to the top of the other board's first column; a done subtask to done.
os.makedirs(server.reports.task_images(board.board), exist_ok=True)
with open(server.reports.image_path(server.reports.task_images(board.board), "0123456789abcdef.png"), "wb") as f:
    f.write(b"png")
res = server.move_task(board, "a", other)
src, dst = server.load_tasks(board), server.load_tasks(other)
check("source keeps the rest", sorted(t["id"] for t in src["tasks"]), ["k", "s"])
check("rev is the source's new rev", res["rev"], server.board_rev(board))
moved = {t["id"]: t for t in dst["tasks"]}
check("card and subtasks moved", sorted(moved), ["a", "a1", "a2", "o"])
check("card on top of To Do, checked", (moved["a"]["column"], moved["a"]["order"] < moved["o"]["order"],
                                         moved["a"]["projectOk"]), ("todo", True, True))
check("subtask columns", (moved["a1"]["column"], moved["a2"]["column"], moved["a1"]["parent"]), ("todo", "done", "a"))
check("picture copied", os.path.exists(server.reports.image_path(server.reports.task_images(other.board),
                                                                "0123456789abcdef.png")), True)

for tid, to, why in [("s", other, "a sent card"), ("k", board, "same project"), ("zz", other, "unknown card")]:
    try:
        server.move_task(board, tid, to)
        check(f"move refused: {why}", "moved", "refused")
    except (ValueError, LookupError):
        check(f"move refused: {why}", "refused", "refused")

# A project with no board yet gets the page's default board.
empty = make_project("empty", "# Empty", None)
server.move_task(board, "k", empty)
eb = server.load_tasks(empty)
check("new board made, card in To Do", ([c["name"] for c in eb["columns"]], eb["tasks"][0]["column"] == eb["columns"][0]["id"]),
      (["To Do", "In Progress", "Review", "Done"], True))

# The board: Send asks first when Claude places the card elsewhere; nothing starts.
node = shutil.which("node")
if node:
    src_html = open(os.path.join(os.path.dirname(HERE), "index.html"), encoding="utf-8").read()
    check_fn = re.search(r"const projectChecks = new Map\(\);[\s\S]*?\n}\n", src_html).group(0)
    send_fn = re.search(r"async function sendToClaude\(t\) \{[\s\S]*?\n}\n", src_html).group(0)
    harness = """
let calls = [], openId = null, boardGen = 0, rev = 1, saving = false, P = "p", pending = [], asked = null, useServer = true;
const projects = [{ id: "p", name: "fablecraft" }, { id: "q", name: "onemoretask" }];
const card = { id: "a", title: "Fix send", tags: ["bug"], notes: "The board's send button", agent: null, column: "todo", parent: null };
const task = (id) => (id === "a" ? card : null), touch = () => {}, render = () => {}, tagSuggestions = () => [];
const $ = () => ({ value: "", disabled: false }), window = { t: (s) => s }, isCodexModel = () => false, setStatus = () => {};
const addPending = (t, status, message) => { const m = { status, message, state: "sending" }; pending.push(m); return m; };
const dropPending = () => {}, refreshAgent = () => {}, renderAgent = () => {}, pollAgents = async () => {}, settled = async () => {};
const colRole = () => ({ id: "progress" }), moveTask = (id, col) => { card.column = col; }, now = () => 0;
const autoFilling = new Map(), askProject = (t, other) => { asked = other.project.name; };
let answer = { project: { id: "q", name: "onemoretask" }, why: "board app" };
let switchDuring = false;
const post = (url, body) => { calls.push(url); if (url === "/api/which-project" && switchDuring) P = "q";
  return url === "/api/which-project"
  ? (answer instanceof Error ? Promise.reject(answer) : Promise.resolve(answer)) : Promise.resolve({ id: "s1" }); };
""" + check_fn + send_fn + """
(async () => {
  const out = [];
  await sendToClaude(card);
  out.push([calls.slice(), asked, card.column, !!card.projectOk]);
  card.projectOk = true; calls = [];                         // "Keep it here": no second check, sent
  await sendToClaude(card);
  out.push([calls.slice(), card.agent && card.agent.id]);
  card.projectOk = false; card.agent = null; card.notes = "other text"; calls = []; answer = {};   // fits here
  await sendToClaude(card);
  out.push([calls.slice(), card.projectOk]);
  card.projectOk = false; card.agent = null; card.notes = "third"; calls = []; answer = new Error("no claude");
  await sendToClaude(card);
  out.push([calls.slice(), card.projectOk]);
  card.projectOk = false; card.agent = null; card.parent = "x"; calls = [];   // a subtask: no check
  await sendToClaude(card);
  out.push(calls.slice());
  card.parent = null; card.agent = null; card.column = "todo"; card.notes = "fourth"; calls = []; answer = {}; switchDuring = true;   // project switched mid-check
  await sendToClaude(card);
  out.push([calls.slice(), card.column, !!card.projectOk]);
  console.log(JSON.stringify(out));
})();
"""
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    got = json.loads(out.stdout or "null") if out.returncode == 0 else out.stderr[-400:]
    check("Send: asks and starts nothing; kept card sends; fits-here sends and is marked; failure sends unmarked; subtask unchecked;"
          " a project switch during the check sends nothing",
          got, [[["/api/which-project"], "onemoretask", "todo", False],
                [["/api/agent"], "s1"],
                [["/api/which-project", "/api/agent"], True],
                [["/api/which-project", "/api/agent"], False],
                ["/api/agent"],
                [["/api/which-project"], "todo", False]])
else:
    print("skip board Send: no node")

shutil.rmtree(TMP, ignore_errors=True)
print("FAILED" if fails else "all passed")
sys.exit(1 if fails else 0)
