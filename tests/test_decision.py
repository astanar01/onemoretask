"""A decision report is stored as a progress entry with a checked `decision` object, and bad input is refused.

Run: python3 tests/test_decision.py  (stdlib only; a temp board, the report script run as a subprocess)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
import reports  # noqa: E402

BOARD = tempfile.mkdtemp(prefix="decision-test-")
GOOD = {"query": "Tree or list?", "options": [{"label": "List", "why": "simple"}, {"label": "Tree", "why": "shows the path"}],
        "chosen": 1, "by": "you", "why": "asked for it"}


def cli(payload):
    return subprocess.run([sys.executable, os.path.join(ROOT, "report.py"), "--board", BOARD, "t1", "decision", "-"],
                          input=payload, capture_output=True, text=True)


fails = []


def check(name, cond):
    print(("ok   " if cond else "FAIL ") + name)
    if not cond:
        fails.append(name)


r = cli(json.dumps(GOOD))
check("cli accepts a decision", r.returncode == 0 and "reported decision" in r.stdout)
log = reports.read(BOARD, "t1")
e = log[-1] if log else {}
check("stored as progress with a decision", e.get("status") == "progress" and e.get("from") == "claude")
check("message names the chosen option", e.get("message") == "Decision: Tree or list? -> Tree")
check("decision kept whole", e.get("decision", {}).get("chosen") == 1 and e["decision"]["by"] == "you")

open_d = dict(GOOD, chosen=None, by="agent")
cli(json.dumps(open_d))
e = reports.read(BOARD, "t1")[-1]
check("open decision has chosen None", e["decision"]["chosen"] is None and e["message"] == "Decision open: Tree or list?")

for name, bad in [("one option", dict(GOOD, options=[{"label": "A"}])),
                  ("index out of range", dict(GOOD, chosen=5)),
                  ("bool index", dict(GOOD, chosen=True)),
                  ("unknown chooser", dict(GOOD, by="bot")),
                  ("no query", dict(GOOD, query=" "))]:
    r = cli(json.dumps(bad))
    check(f"refused: {name}", r.returncode != 0 and "decision:" in r.stderr)

check("progress still works", subprocess.run([sys.executable, os.path.join(ROOT, "report.py"), "--board", BOARD, "t1",
                                              "progress", "hello"], capture_output=True).returncode == 0)
print("FAILED: " + ", ".join(fails) if fails else "all decision checks passed")
sys.exit(1 if fails else 0)
