"""The "Simple words" project setting (tasks.json "simpleWords") reaches the task prompt, the review prompt and every
card reply; switched off (false) a reply says so, and a board that never set it gets nothing.

Run: python3 tests/test_simple_words.py  (stdlib only; a fresh temp HOME and project, no CLI runs)."""
import json
import os
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="simple-words-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.makedirs(os.environ["HOME"])
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import server  # noqa: E402

fails = 0


def check(name, ok):
    global fails
    print(("ok   " if ok else "FAIL ") + name)
    fails += not ok


proj = os.path.join(TMP, "proj")
os.makedirs(proj)
p = server.Project(proj)
os.makedirs(p.board)
TASK = {"id": "t1", "title": "Fix the save button", "notes": "It does nothing.", "column": "c1", "order": 0}


def board(simple):
    state = {"version": 1, "columns": [{"id": "c1", "name": "To Do"}], "tasks": [dict(TASK)]}
    if simple is not None:
        state["simpleWords"] = simple
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump(state, f)
    return state


RULE = server.SIMPLE_RULE
OFF = "Simple words is off for this board now"
for simple, name in ((None, "never set"), (False, "off"), (True, "on")):
    state = board(simple)
    prompt = server.build_prompt(p, state, state["tasks"][0])
    tail = server.board_tail(p, "t1")
    check(f"{name}: task prompt {'has' if simple else 'lacks'} the rule", (RULE in prompt) == bool(simple))
    check(f"{name}: reply {'has' if simple else 'lacks'} the rule", (RULE in tail) == bool(simple))
    check(f"{name}: reply {'has' if simple is False else 'lacks'} the off note", (OFF in tail) == (simple is False))
    check(f"{name}: language rule still last in the reply", tail.endswith(server.reports.LANGUAGE_RULE + ")"))

# Only a real true turns it on: a stray string from a hand-edited tasks.json does not.
check("'true' string is not on", not server.simple_words({"simpleWords": "true"}))
check("card lines carry the rule when asked", any(RULE in x for x in server.card_lines("r", simple=True)))
check("card lines skip it by default", not any(RULE in x for x in server.card_lines("r")))

# The review prompt passes the board setting to card_lines.
with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as f:
    src = f.read()
check("review prompt reads the setting", "*card_lines(rep, simple=simple_words(state))" in src)

# The page: the switch sits in Project settings and saves false (not a delete) when turned off.
with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as f:
    page = f.read()
menu = page[page.index('<div class="pop" id="proj-menu"'):]
menu = menu[:menu.index("</div>\n<div")]
check("switch is in the Project settings menu", 'id="simple-words"' in menu)
check("turning it off saves false", "state.simpleWords = e.target.checked;" in page)

print(f"{fails} failures")
sys.exit(1 if fails else 0)
