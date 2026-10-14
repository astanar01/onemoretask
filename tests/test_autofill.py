"""POST /api/autofill: Claude's title and tags for a card's notes, tags folded into the board's existing ones; and the
board's no-AI first-sentence title.

Run: python3 tests/test_autofill.py  (stdlib; Claude is faked; node for the board's titleFromNotes)."""
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
check("ss is not a plural", server.tag_key("css"), "css")
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

# The board's first-sentence title.
node = shutil.which("node")
if node:
    src = open(os.path.join(os.path.dirname(HERE), "index.html"), encoding="utf-8").read()
    fn = re.search(r"function titleFromNotes[\s\S]*?\n}\n", src).group(0)
    cases = {"Mr. Smith asked for x": "Mr. Smith asked for x", "- [ ] buy milk": "Buy milk", "* [x] done thing": "Done thing",
             "Fix the scroll. It jumps to the top.": "Fix the scroll", "see a.b for it": "See a.b for it",
             "## \n\nreal line": "Real line", "   ": ""}
    out = subprocess.run([node, "-e", fn + f"console.log(JSON.stringify({json.dumps(list(cases))}.map(titleFromNotes)))"],
                         capture_output=True, text=True)
    for (n, want), got in zip(cases.items(), json.loads(out.stdout or "[]") or [None] * len(cases)):
        check(f"titleFromNotes {n!r}", got, want)
else:
    print("skip titleFromNotes: no node")

shutil.rmtree(TMP, ignore_errors=True)
print("FAILED" if fails else "all passed")
sys.exit(1 if fails else 0)
