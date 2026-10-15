"""A question report can carry 2 to 6 answer choices (--option); the card turns them into buttons.

Run: python3 tests/test_question_options.py  (stdlib only; a fresh temp board, the report script run as a subprocess)."""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = tempfile.mkdtemp(prefix="question-options-test-")
BOARD = os.path.join(TMP, "board")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)
import reports  # noqa: E402
import server  # noqa: E402


def cli(*args):
    return subprocess.run([sys.executable, os.path.join(ROOT, "report.py"), "--board", BOARD, "t1", *args],
                          capture_output=True, text=True)


fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


r = cli("question", "Where should the list go?", "--option", " A. Task list (left) ", "--option", "B. Right side")
check("cli accepts a question with options", r.returncode == 0 and "reported question" in r.stdout)
e = (reports.read(BOARD, "t1") or [{}])[-1]
check("options stored, trimmed", e.get("options") == ["A. Task list (left)", "B. Right side"])
check("entry shape", e.get("status") == "question" and e.get("from") == "claude"
      and e.get("message") == "Where should the list go?" and set(e) == {"at", "status", "message", "from", "options"})

cli("question", "No choices here?")
e = reports.read(BOARD, "t1")[-1]
check("no options key without --option", "options" not in e)

n = len(reports.read(BOARD, "t1"))
for name, opts in [("one option", ["A"]),
                   ("seven options", list("ABCDEFG")),
                   ("empty label", ["A", "  "]),
                   ("duplicate label", ["A", " A"])]:
    r = cli("question", "Pick one?", *[x for o in opts for x in ("--option", o)])
    check(f"refused: {name}", r.returncode != 0 and "question:" in r.stderr)
r = cli("progress", "working", "--option", "A", "--option", "B")
check("refused: --option with progress", r.returncode != 0 and "only goes with question" in r.stderr)
r = cli("question", "Pick?", "--option")
check("refused: --option with no label", r.returncode != 0)
check("nothing posted by refused calls", len(reports.read(BOARD, "t1")) == n)

check("six options allowed", cli("question", "Pick?", *[x for o in "ABCDEF" for x in ("--option", o)]).returncode == 0)

text = "\n".join(server.card_lines("REPORT"))
check("card_lines tells agents to pass --option", '--option "A. <short label>"' in text)
check("card_lines no longer asks for a decision on the user's choice",
      "choice the user made)" not in text and "Never post a decision for a choice the user just made" in text)

# Many-part questions: --part starts a part, the --option after it belong to it.
r = cli("question", "Pick both:", "--part", " 1. Starting tokens? ", "--option", "a) 20 at signup", "--option", "b) none",
        "--part", "2. Do tokens expire?", "--option", "a) no", "--option", "b) yes")
check("cli accepts a many-part question", r.returncode == 0)
e = reports.read(BOARD, "t1")[-1]
check("parts stored, trimmed", e.get("parts") == [{"q": "1. Starting tokens?", "options": ["a) 20 at signup", "b) none"]},
                                                  {"q": "2. Do tokens expire?", "options": ["a) no", "b) yes"]}])
check("no options key with parts", "options" not in e)
n = len(reports.read(BOARD, "t1"))
for name, args in [("part with one option", ["--part", "1?", "--option", "a", "--part", "2?", "--option", "a", "--option", "b"]),
                   ("same option twice in a part", ["--part", "1?", "--option", "a", "--option", "a"]),
                   ("empty part question", ["--part", " ", "--option", "a", "--option", "b"]),
                   ("same part twice", ["--part", "1?", "--option", "a", "--option", "b", "--part", "1?", "--option", "a", "--option", "b"]),
                   ("option before the first part", ["--option", "x", "--part", "1?", "--option", "a", "--option", "b"]),
                   ("nine parts", [x for k in range(9) for x in ("--part", f"{k}?", "--option", "a", "--option", "b")])]:
    r = cli("question", "Pick?", *args)
    check(f"refused: {name}", r.returncode != 0 and "question:" in r.stderr)
r = cli("done", "ok", "--part", "1?", "--option", "a", "--option", "b")
check("refused: --part with done", r.returncode != 0 and "only goes with question" in r.stderr)
check("nothing posted by refused part calls", len(reports.read(BOARD, "t1")) == n)
check("same option label allowed in different parts",
      cli("question", "Q", "--part", "1?", "--option", "a) no", "--option", "b) yes",
          "--part", "2?", "--option", "a) no", "--option", "b) yes").returncode == 0)
check("card_lines tells agents to use --part", '--part "1. <first question>"' in text and "1a 2b" in text)

print("FAILED: " + ", ".join(fails) if fails else "all question option checks passed")
sys.exit(1 if fails else 0)
