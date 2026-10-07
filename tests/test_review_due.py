"""The board says when the weekly skill review is due, and card briefs tell sessions not to mention it.

Run: python3 tests/test_review_due.py  (stdlib only; a fresh temp HOME and project)."""
import datetime
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="review-due-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import observations  # noqa: E402
import server  # noqa: E402

today = datetime.date.today()
proj = os.path.join(TMP, "proj")
folder = os.path.join(proj, "skill-observations")
os.makedirs(folder)
stamp = os.path.join(folder, "last-review-date.txt")


def write(text):
    with open(stamp, "w", encoding="utf-8") as f:
        f.write(text)


def source():
    return observations.list_observations(proj)["sources"][0]


cases = [("2 days ago", (today - datetime.timedelta(days=2)).isoformat() + "\n", False),
         ("6 days ago", (today - datetime.timedelta(days=6)).isoformat(), False),
         ("7 days ago", (today - datetime.timedelta(days=7)).isoformat(), True),
         ("never", "never\n", True),
         ("garbage", "last tuesday", True),
         ("empty", "", True)]
for name, text, due in cases:
    write(text)
    s = source()
    assert s["reviewDue"] is due, (name, s)
    print(f"ok  {name}: reviewDue={due}")
s = source()
assert s["lastReview"] is None and s["reviewDays"] is None, s
write((today - datetime.timedelta(days=9)).isoformat())
s = source()
assert s["reviewDays"] == 9 and s["lastReview"] == (today - datetime.timedelta(days=9)).isoformat(), s
print("ok  lastReview and reviewDays are reported")
os.remove(stamp)
s = source()
assert s["reviewDue"] is True and s["lastReview"] is None, s
print("ok  missing file: reviewDue=True")

# ---- the card brief tells the session to leave the review out (only when task-observer is installed)
skill = os.path.join(os.environ["HOME"], ".claude", "skills", "task-observer")
os.makedirs(skill)
open(os.path.join(skill, "SKILL.md"), "w").close()
p = server.Project(proj)
state = {"columns": [{"id": "todo", "name": "To do"}], "tasks": [{"id": "t1", "title": "A task", "column": "todo", "order": 0}]}
brief = server.build_prompt(p, state, state["tasks"][0])
assert "Unless this card's task is the review itself, do not mention the weekly skill review on the card" in brief, brief
assert "the board shows when it is due" in brief, brief
print("ok  card brief has the do-not-mention-the-review sentence")
print("PASS")
