"""POST /api/autofill: Claude's title and tags for a card's notes, tags folded into the board's existing ones; and the
board's one-call-per-card autoFill.

Run: python3 tests/test_autofill.py  (stdlib; Claude is faked; node for the board's autoFill)."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="autofill-test-")
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
        asks.append(args[-1])
        return SimpleNamespace(returncode=0, stdout=json.dumps({"result": answer}), stderr="")
    server.run_claude = run
    return asks


# Twins fold into the board's spelling.
known = ["Bugs", "ui", "feature-request", "observations"]
check("plural folds into board tag", server.pick_tags(["bug"], known), ["Bugs"])
check("singular board tag wins", server.pick_tags(["feature requests"], ["feature-request"]), ["feature-request"])
check("hash, case and space", server.pick_tags(["#UI", "Feature Request"], known), ["ui", "feature-request"])
check("ies plural", server.pick_tags(["categories"], ["category"]), ["category"])
check("ss is not a plural", server.tag_keys("css"), ["css"])
for board, claude in [("fix", "fixes"), ("branch", "branches"), ("cookie", "cookies"), ("class", "classes"), ("fixes", "fix")]:
    check(f"{claude} folds into {board}", server.pick_tags([claude], [board]), [board])
check("dupes dropped, max 3", server.pick_tags(["bug", "bugs", "a1", "b2", "c3"], known), ["Bugs", "a1", "b2"])
check("observations never added", server.pick_tags(["observation", "Observations"], known), [])
check("new tag lowercased, comma removed", server.pick_tags(["Perf,Speed"], []), ["perf speed"])
check("garbage tags ignored", server.pick_tags("bug", known), [])

# One call, JSON answer, even with a preamble around it.
asks = fake_claude('Here you go:\n{"title": "\\"Fix done column scroll.\\"", "tags": ["bug", "UI"]}\nDone.')
check("title and tags", server.autofill("notes", True, True, known), {"title": "Fix done column scroll", "tags": ["Bugs", "ui"]})
check("known tags are in the ask", "Bugs, ui, feature-request" in asks[0], True)
asks = fake_claude('{"title": "x", "tags": ["bug"]}')
check("only tags asked", server.autofill("notes", False, True, []), {"tags": ["bug"]})
check("no title asked for", '"title"' in asks[0], False)
check("nothing asked: no call", (server.autofill("notes", False, False), len(asks)), ({}, 1))
fake_claude("Here is a title: Fix scroll")
try:
    server.autofill("notes", True, True)
    check("no JSON raises", "returned", "raised")
except RuntimeError:
    check("no JSON raises", "raised", "raised")

# The board: one autoFill per card at a time, so Send waits for the call the title box started when it lost focus.
node = shutil.which("node")
if node:
    src = open(os.path.join(os.path.dirname(HERE), "index.html"), encoding="utf-8").read()
    fn = re.search(r"const autoFilling = new Map\(\);[\s\S]*?\n}\n[\s\S]*?\n}\n", src).group(0)
    check("no first-sentence title left", "titleFromNotes" in src, False)
    harness = """
let calls = 0, saved = 0, openId = null, resolveCall;
const card = { id: "a", title: "", tags: ["x"], notes: "Fix the scroll. It jumps.", agent: null };
const task = (id) => (id === "a" ? card : null), touch = () => saved++, render = () => {}, $ = () => ({}), tagSuggestions = () => [];
const post = (url, body) => { calls++; return new Promise((r) => (resolveCall = () => r({ title: "Fix column scroll jump" }))); };
""" + fn + """
(async () => {
  const blur = autoFill(card);                 // the title box lost focus
  const shown = card.title;
  const send = autoFill(card);                 // Send right after: must get the same call, not a new one
  const same = send === blur;
  resolveCall(); await send;
  console.log(JSON.stringify([shown, same, calls, card.title]));
})();
"""
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    got = json.loads(out.stdout or "null") if out.returncode == 0 else out.stderr[-300:]
    check("Send waits for the running call; Untitled meanwhile; Claude's title after",
          got, ["Untitled", True, 1, "Fix column scroll jump"])

    # Send shows "Sending" and moves the card at once; the launch itself still waits for the title.
    send_fn = re.search(r"async function sendToClaude\(t\) \{[\s\S]*?\n}\n", src).group(0)
    harness = """
let calls = [], openId = null, resolveCall, boardGen = 0, rev = 1, saving = false, P = "p", pending = [];
const card = { id: "a", title: "", tags: [], notes: "Fix the scroll. It jumps.", agent: null, column: "todo" };
const task = (id) => (id === "a" ? card : null), touch = () => {}, render = () => {}, tagSuggestions = () => [];
const $ = () => ({ value: "", disabled: false }), window = { t: (s) => s }, isCodexModel = () => false, setStatus = () => {};
const addPending = (t, status, message) => { const m = { status, message, state: "sending" }; pending.push(m); return m; };
const dropPending = () => {}, refreshAgent = () => {}, renderAgent = () => {}, pollAgents = async () => {}, settled = async () => {};
const colRole = () => ({ id: "progress" }), moveTask = (id, col) => { card.column = col; }, now = () => 0;
const post = (url, body) => { calls.push(url); return url === "/api/autofill"
  ? new Promise((r) => (resolveCall = () => r({ title: "Fix column scroll jump", tags: ["bug"] })))
  : Promise.resolve({ id: "s1" }); };
""" + fn + send_fn + """
(async () => {
  const send = sendToClaude(card);
  await new Promise((r) => setTimeout(r, 0));
  const before = [card.column, pending.length, calls.slice()];
  resolveCall(); await send;
  console.log(JSON.stringify([before, calls, card.title, card.tags, card.agent && card.agent.id]));
})();
"""
    out = subprocess.run([node, "-e", harness], capture_output=True, text=True)
    got = json.loads(out.stdout or "null") if out.returncode == 0 else out.stderr[-300:]
    check("Send moves the card and shows Sending before the title comes; launches after it",
          got, [["progress", 1, ["/api/autofill"]], ["/api/autofill", "/api/agent"], "Fix column scroll jump", ["bug"], "s1"])

    # The open panel swaps to the sent layout on the click, before the title or the launch come back.
    shown = (harness.replace('let calls = [], openId = null,', 'let calls = [], openId = "a", panel = [],')
                    .replace('const dropPending = () => {},', 'const renderDetail = () => panel.push([calls.slice(), pending.map((m) => m.status)]), dropPending = () => {},')
                    .replace("console.log(JSON.stringify([before, calls, card.title, card.tags, card.agent && card.agent.id]));",
                             "console.log(JSON.stringify(panel[0] || null));"))
    out = subprocess.run([node, "-e", shown], capture_output=True, text=True)
    got = json.loads(out.stdout or "null") if out.returncode == 0 else out.stderr[-300:]
    check("Send redraws the open panel with the launch pending before any server call ends", got, [[], ["launch"]])

    # Another window's board swapped in while the title was coming (the move not on disk yet): Send still launches.
    reload = (harness.replace("resolveCall(); await send;", 'boardGen++; card.column = "todo"; resolveCall(); await send;')
                     .replace("console.log(JSON.stringify([before, calls, card.title, card.tags, card.agent && card.agent.id]));",
                              "console.log(JSON.stringify([calls, card.column, card.agent && card.agent.id]));"))
    out = subprocess.run([node, "-e", reload], capture_output=True, text=True)
    got = json.loads(out.stdout or "null") if out.returncode == 0 else out.stderr[-300:]
    check("A board reload during the title wait does not stop Send; the card goes back to In Progress",
          got, [["/api/autofill", "/api/agent"], "progress", "s1"])
else:
    print("skip autoFill: no node")

shutil.rmtree(TMP, ignore_errors=True)
print("FAILED" if fails else "all passed")
sys.exit(1 if fails else 0)
