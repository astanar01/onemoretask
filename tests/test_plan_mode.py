"""Plan cards: `report.py plan` merges updates into the live state, the prompt asks for the plan, the guard lets a plan
card write only its own Markdown documents, /api/plan answers the pane and says when nothing changed, and the lookups
come from the session's transcript.

Run: python3 tests/test_plan_mode.py  (stdlib only; a temp board)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="plan-mode-"))
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)
import reports  # noqa: E402
import server  # noqa: E402

fails = []


def check(name, ok, extra=""):
    print(("ok  " if ok else "FAIL") + " " + name + (f" -> {extra}" if not ok and extra else ""))
    if not ok:
        fails.append(name)


project = os.path.join(TMP, "proj")
board = os.path.join(project, ".task_board")
os.makedirs(board)
subprocess.run(["git", "-C", project, "init", "-q", "-b", "main"], check=True)


def task(tid, **more):
    return {"id": tid, "title": tid, "notes": "Idea", "priority": "", "tags": [], "column": "c1", "parent": None,
            "order": 0, "created": "2026-11-01T00:00:00+00:00", "updated": "2026-11-01T00:00:00+00:00", **more}


TASKS = {"version": 1, "columns": [{"id": "c1", "name": "To Do", "done": False}],
         "tasks": [task("plan1", planMode=True, answerOnly=True), task("other", planMode=True, answerOnly=True),
                   task("ans1", answerOnly=True), task("plain1"),
                   task("build1", buildOf="plan1"), task("buildans", buildOf="plan1", answerOnly=True),
                   task("badid", buildOf="../x"), task("badtype", buildOf=123),
                   task("buildgone", buildOf="gone")]}
with open(os.path.join(board, "tasks.json"), "w") as f:
    json.dump(TASKS, f)


def report(*args, stdin=None):
    return subprocess.run([sys.executable, os.path.join(ROOT, "report.py"), "--board", board, *args], input=stdin,
                          capture_output=True, text=True)


# --- report.py plan
r = report("plan1", "plan", json.dumps({"goal": "Ship it", "phase": "explore",
                                        "nodes": [{"id": "g", "kind": "goal", "label": "Main"},
                                                  {"id": "q", "parent": "g", "kind": "question", "label": "Which?", "detail": "why"},
                                                  {"id": "q-a", "parent": "q", "kind": "option", "label": "A"}],
                                        "files": [{"path": "server.py", "do": "look", "why": "the routes"}]}))
check("a plan update posts", r.returncode == 0 and "3 nodes, 1 files" in r.stdout, r.stdout + r.stderr)
check("it stays out of the card's log", reports.read(board, "plan1") == [])

plan = reports.read_plan(board, "plan1")
at_q = next(n for n in plan["nodes"] if n["id"] == "q")["at"]
r = report("plan1", "plan", "-", stdin=json.dumps({"nodes": [{"id": "q", "status": "decided"}],
                                                   "files": [{"path": "server.py", "do": "change"}]}))
plan = reports.read_plan(board, "plan1")
q = next(n for n in plan["nodes"] if n["id"] == "q")
check("an update keeps what it leaves out", (q["label"], q["detail"], q["parent"], q["kind"]) == ("Which?", "why", "g", "question"), q)
check("an update changes what it names", q["status"] == "decided" and q["at"] >= at_q)
check("a file moves from look to change and keeps its why", plan["files"] == [
    {"path": "server.py", "do": "change", "why": "the routes", "at": plan["files"][0]["at"]}], plan["files"])
check("goal and phase survive an update that omits them", (plan["goal"], plan["phase"]) == ("Ship it", "explore"))

msg = os.path.join(TMP, "u.json")
with open(msg, "w") as f:
    json.dump({"phase": "draft", "nodes": [{"id": "t", "label": "Has a 'quote' and a `tick`"}]}, f)
r = report("plan1", "plan", "--file", msg)
check("--file works (the sandbox's safe form)", r.returncode == 0 and reports.read_plan(board, "plan1")["phase"] == "draft", r.stderr)

for name, bad, says in (("bad json", "{", "plan:"),
                        ("bad kind", json.dumps({"nodes": [{"id": "x", "label": "y", "kind": "nope"}]}), "kind"),
                        ("bad status", json.dumps({"nodes": [{"id": "q", "status": "nope"}]}), "status"),
                        ("bad phase", json.dumps({"phase": "nope"}), "phase"),
                        ("new node with no label", json.dumps({"nodes": [{"id": "x"}]}), "label"),
                        ("node with no id", json.dumps({"nodes": [{"label": "y"}]}), "id"),
                        ("file with no path", json.dumps({"files": [{"do": "look"}]}), "path"),
                        ("unknown field", json.dumps({"node": []}), "unknown"),
                        ("a list", "[]", "object"),
                        ("a parent circle", json.dumps({"nodes": [{"id": "g", "parent": "q"}]}), "circle")):
    before = reports.read_plan(board, "plan1")
    r = report("plan1", "plan", bad)
    check(f"refused: {name}", r.returncode != 0 and says in r.stderr, r.stdout + r.stderr)
    check(f"{name} left the plan alone", reports.read_plan(board, "plan1") == before)

report("plan1", "plan", json.dumps({"drop": ["q"], "drop_files": ["server.py"]}))
plan = reports.read_plan(board, "plan1")
check("dropping a node takes its branch", sorted(n["id"] for n in plan["nodes"]) == ["g", "t"], plan["nodes"])
check("dropping a file works", plan["files"] == [])
check("a bad task id is refused", report("../x", "plan", "{}").returncode != 0)
with open(reports.plan_path(board, "other"), "w") as f:
    f.write("not json")
check("a damaged state reads as empty", reports.read_plan(board, "other")["nodes"] == [])
check("the limit holds", report("plan1", "plan", json.dumps(
    {"nodes": [{"id": str(i), "label": "n"} for i in range(reports.PLAN_MAX + 1)]})).returncode != 0)
check("a message is still required for the other statuses", report("plan1", "progress", " ").returncode != 0)

# --- phases of a Build card
said = []
for phase in ("tests", "code", "done"):
    r = report("build1", "plan", json.dumps({"phase": phase}))
    said.append(r.returncode == 0 and reports.read_plan(board, "build1")["phase"] == phase)
check("S21 report.py plan takes phases tests, code and done", all(said), said)
r = report("build1", "plan", json.dumps({"phase": "shipping"}))
check("S22 report.py plan still refuses an unknown phase", r.returncode != 0 and "phase" in r.stderr, r.stdout + r.stderr)
r = subprocess.run([sys.executable, os.path.join(ROOT, "report.py")], capture_output=True, text=True)
check("S23 report.py help names the new phases", "tests" in r.stdout + r.stderr and "explore|draft|review|ready|tests|code|done" in r.stdout + r.stderr, r.stdout + r.stderr)

# --- the prompt
state = json.load(open(os.path.join(board, "tasks.json")))
P = server.Project(project)
tasks_by ={t["id"]: t for t in state["tasks"]}
prompt = server.build_prompt(P, state, tasks_by["plan1"])
folder = reports.plan_dir(board, "plan1")
check("the prompt names the plan folder", folder in prompt)
check("the prompt asks for the plan update command", " plan --file " in prompt)
for word in ("search_graph", "brief.md", "plan.md", "## Context", "memory tools", "fresh reviewers", "no history", "Build this plan"):
    check(f"the prompt says {word}", word in prompt)
check("S1 the plan prompt names spec.md", "spec.md" in prompt)
check("S2 the plan prompt no longer names design.md", "design.md" not in prompt)
check("S3 the plan prompt asks for numbered points with a check per point",
      "Number every point" in prompt and "Give each point its check" in prompt)
check("S4 the plan prompt asks plan.md for ## Tasks after ## Context",
      "## Context" in prompt and "## Tasks" in prompt and prompt.index("## Context") < prompt.index("## Tasks"))
check("S5 the plan prompt tells reviewers to check each point can be proven by its check", "proven by its check" in prompt)
check("the prompt does not carry the answer-only brief", "Answer only (this task" not in prompt)
check("the prompt keeps the sandbox rule", "sandbox where the project folder is read-only" in prompt)
check("the prompt allows only plan documents", "Write only the plan documents" in prompt)
check("a plain card gets no plan brief", "Plan mode" not in server.build_prompt(P, state, tasks_by["plain1"]))
check("an answer-only card keeps its brief", "Answer only (this task" in server.build_prompt(P, state, tasks_by["ans1"]))
codex_prompt = server.build_prompt(P, state, tasks_by["plan1"], model="gpt-5.4")
check("a Codex session gets its own way to start reviewers", "spawn_agent" in codex_prompt)
check("the task's sandbox is on for a plan card", server.answer_only_task(P, "plan1"))
remind = server.plan_reminder(P, tasks_by["plan1"])
check("a reply repeats the folder and the command", folder in remind and " plan --file " in remind and "plan.md" in remind and "## Context" in remind, remind)
check("S6 the plan reminder names ## Tasks", "## Tasks" in remind, remind)

# --- a better Plan first (card mw0hu788f5w4d): PFn = its spec point Sn


def element(lines, start):
    found = [x for x in lines if x.startswith(start)]
    return found[0] if found else ""


def has_all(text, words):
    missing = [w for w in words if w not in text]
    return not missing, missing


def before(text, a, b):
    return a in text and b in text and text.index(a) < text.index(b)


PL = server.plan_lines("R", "/b/plans/plan1")
PL_CODEX = server.plan_lines("R", "/b/plans/plan1", on_codex=True)
explore, review, ready = (element(PL, s) for s in ("1. **explore**", "3. **review**", "4. **ready**"))
review_cx, ready_cx = element(PL_CODEX, "3. **review**"), element(PL_CODEX, "4. **ready**")
check("PF1 explore asks for ## The ask with its five parts", *has_all(explore, [
    "open `/b/plans/plan1/brief.md` with `## The ask`", "the card's notes quoted as written",
    "the goal in your own words", "each with the assumption it rests on",
    "which reading the code and facts support", "the strongest case against the direction you lean to"]))
check("PF2 the notes' wording is one reading, not favoured",
      "the notes' own wording is one reading; do not favour it" in explore)
check("PF3 a winning case against changes the lean", "If that case beats your lean, change the lean" in explore)
check("PF4 readings that build different things go to the user",
      "Readings that would build different things go to the user as a `question`" in explore)
check("PF5 ## The ask comes before the first question", "Before your first question" in explore
      and before(explore, "## The ask", "Then ask the user what only they can decide"))
check("PF6 ## The ask is a few lines on a small card", "a few lines for a small card" in explore)
check("PF7 the agreed direction goes below ## The ask",
      "Save the agreed direction in `/b/plans/plan1/brief.md`, below `## The ask`" in explore)
check("PF8 a challenger gets only brief.md and spec.md", *has_all(review, [
    "Also start a challenger with the Agent tool, given only",
    "`/b/plans/plan1/brief.md` and `/b/plans/plan1/spec.md`", "the strongest case against the chosen approach",
    "whether that case beats the plan"]))
check("PF9 a challenger win goes to the user", "If it does, or it would undo a choice the user made, ask the user "
      "with a `question` report; never swap the approach on your own" in review)
check("PF10 otherwise why the plan stands goes under ## Challenger",
      *has_all(review, ["under `## Challenger` in", "why the plan stands"]))
check("PF11 every reviewer hands its report back as a file", *has_all(review, [
    "cat > \"$TMPDIR/plan1-review-<name>.md\" <<'EOF'", "not a script",
    "replies with only that file's absolute path", "how to hand back its report"]))
check("PF11b the write rule allows reviewer report files",
      "apart from $TMPDIR for scratch files and reviewer reports" in element(PL, "- Write your Markdown documents"))
check("PF12 the old text-reply wording is gone", "report as text" not in "\n".join(PL))
check("PF13 a failed write falls back to text, noted as partial",
      *has_all(review, ["If the write fails, it replies in text and says so", "may be partial"]))
check("PF14 a cut reply is recovered", "ask for the rest from the last line seen" in review)
check("PF15 the existing review asks stay (guard)", *has_all(review, [
    "start fresh reviewers with", "no history", "proven by its check", "is each point one thing"]))
check("PF16 a builder gets only plan.md and spec.md", *has_all(ready, [
    "start one builder with the Agent tool, given only", "`/b/plans/plan1/plan.md` and `/b/plans/plan1/spec.md`",
    "guess or ask", "in a file as above"]))
check("PF16b the builder check comes before ready", "Keep the phase at `review` until this check is done" in ready
      and before(ready, "start one builder", "Then set the phase to ready")
      and "(`ready` only when step 4 says)" in element(PL, "Work in these phases"))
check("PF16c builder findings go under ## Builder check", "under `## Builder check` in" in ready)
check("PF17 done lists the choices made without asking", "every choice you made without asking" in ready)
check("PF18 Codex starts the challenger and builder with spawn_agent",
      "Also start a challenger with spawn_agent with `fork_turns: \"none\"`" in review_cx
      and "start one builder with spawn_agent with `fork_turns: \"none\"`" in ready_cx
      and "the Agent tool" not in review_cx and "the Agent tool" not in ready_cx)
check("PF19 the reminder names the new steps", *has_all(remind, [
    "## The ask", "spec.md", "S1", "challenger", "plan1-review-", "absolute path", "builder", "plan.md and spec.md"]))
# Lengths measured on the unchanged code at commit 8a48392 (main, before this card): 6012 and 420.
PLAN_LINES_BEFORE, REMINDER_BEFORE = 6012, 420
plan_len = len("\n".join(PL))
remind_len = len(remind.replace(reports.plan_dir(P.board, "plan1"), "").replace(server.report_cmd(P, "plan1"), ""))
check("PF20 the added text stays short (guard)",
      plan_len <= PLAN_LINES_BEFORE + 2800 and remind_len <= REMINDER_BEFORE + 550, (plan_len, remind_len))
news = [x for x in open(os.path.join(ROOT, "whats-new.md"), encoding="utf-8").read().splitlines()
        if x.startswith("- ") and "a fresh helper tries hard to prove the plan wrong" in x]
check("PF22 whats-new.md has the line for this change", len(news) == 1, news)
readme = open(os.path.join(ROOT, "README.md"), encoding="utf-8").read()
start = readme.find("### Plan one idea before building it")
end = readme.find("\n### ", start + 4) if start >= 0 else -1
section = readme[start:end if end >= 0 else len(readme)] if start >= 0 else ""
check("PF23 the README says the idea is questioned, challenged and checked",
      *has_all(section, ["questions the idea", "challenger", "fresh builder"]))

# --- the Build card's prompt
built = server.build_prompt(P, state, tasks_by["build1"])
check("S7 a card with a good buildOf gets build lines naming the plan folder", "Build mode:" in built and folder in built, built)
check("S8 a card without buildOf gets no build lines", "Build mode:" not in server.build_prompt(P, state, tasks_by["plain1"]))
check("S9 a card with buildOf and Answer only gets no build lines", "Build mode:" not in server.build_prompt(P, state, tasks_by["buildans"]))
try:
    bad = [server.build_prompt(P, state, tasks_by[x]) for x in ("badid", "badtype")]
    check("S10 a card with a bad buildOf launches with no build lines and no error", not any("Build mode:" in b for b in bad))
except Exception as e:
    check("S10 a card with a bad buildOf launches with no build lines and no error", False, repr(e))
check("S11 the build lines say to write no production code before the tests", "Write no production code yet" in built)
check("S12 the build lines say tests must fail because the behaviour is missing", "must fail because the behaviour is missing" in built)
check("S13 the build lines say commit tests alone in a worktree, and not on a shared branch",
      "In a worktree, commit the tests alone" in built and "do not commit failing tests to the shared branch" in built)
check("S14 the build lines say never to weaken a spec test", "Never weaken, skip or delete a spec test" in built)
check("S15 the build lines say to ask after 3 failed tries", "survives 3 tries, ask with a `question` report" in built)
check("S16 the build lines say one test per point outranks a fewest tests rule", 'One test per point here outranks any "fewest tests" rule' in built)
check("S17 the build lines name the design.md fallback, the recap case and $TMPDIR",
      "use design.md" in built and "If your notes are a recap" in built and "$TMPDIR" in built)

# --- a reply on a Build card
tail = server.board_tail(P, "build1")
check("S18 a reply on a Build card repeats the order, spec path, state path and report command",
      "Build card for plan card plan1" in tail and "tests from the spec with no production code" in tail
      and os.path.join(folder, "spec.md") in tail and reports.plan_path(board, "build1") in tail and " plan --file " in tail, tail)
check("S19 a reply on a plain card has no build reminder", "Build card" not in server.board_tail(P, "plain1"))
try:
    tails = [server.board_tail(P, x) for x in ("badid", "badtype")]
    check("S20 a reply on a card with a bad buildOf does not raise", not any("Build card" in x for x in tails))
except Exception as e:
    check("S20 a reply on a card with a bad buildOf does not raise", False, repr(e))


# --- the guard
def guard(tid, path, tool="Write", cwd=project):
    r = subprocess.run([sys.executable, os.path.join(ROOT, "branch_guard.py"), "--board", board, tid],
                       input=json.dumps({"tool_name": tool, "tool_input": {"file_path": path}, "cwd": cwd}),
                       capture_output=True, text=True)
    return r.stdout


check("a plan card may write its brief", guard("plan1", os.path.join(folder, "brief.md")) == "")
check("and edit it", guard("plan1", os.path.join(folder, "brief.md"), tool="Edit") == "")
for name, path in (("project code", os.path.join(project, "server.py")),
                   ("another card's plan", os.path.join(reports.plan_dir(board, "other"), "brief.md")),
                   ("a script in its folder", os.path.join(folder, "run.sh")),
                   ("the board's tasks", os.path.join(board, "tasks.json")),
                   ("a way out of its folder", os.path.join(folder, "..", "..", "tasks.md")),
                   ("Markdown in the project", os.path.join(project, "docs", "x.md")),
                   ("code in a temp folder", os.path.join(tempfile.gettempdir(), "try.py")),
                   ("code outside any repo", os.path.join(TMP, "home", "x.js")),
                   ("Markdown outside any repo", "/opt/nowhere/x.md"),
                   ("code in the plan folder", os.path.join(folder, "x.py")),
                   ("a skill file", "/opt/nowhere/.claude/skills/s/SKILL.md"),
                   ("code in the observer log", os.path.join(project, "skill-observations", "x.py"))):
    out = guard("plan1", path)
    check(f"a plan card may not write {name}", "writes no code" in out, out)
check("a plan card may write a message for the report script", guard("plan1", os.path.join(tempfile.gettempdir(), "update.json")) == "")
check("a plan card may write to the observer log", guard("plan1", os.path.join(project, "skill-observations", "observation-log", "1-x.md")) == "")
out = guard("ans1", os.path.join(reports.plan_dir(board, "ans1"), "brief.md"))
check("an answer-only card has no plan folder", "answer-only card" in out, out)
check("a plain card is left alone", guard("plain1", os.path.join(project, "server.py")) == "")


# --- /api/plan
os.makedirs(folder, exist_ok=True)
with open(os.path.join(folder, "brief.md"), "w") as f:
    f.write("# Brief\n")
with open(os.path.join(folder, "note.txt"), "w") as f:
    f.write("not a document")
with open(os.path.join(folder, "bad.md"), "wb") as f:
    f.write(b"caf\xe9\n")

server.project = lambda pid: P  # the real route and Handler; one project, whatever the id
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
pid = P.id


def api(tid, sig=""):
    url = f"http://127.0.0.1:{srv.server_port}/api/plan?p={pid}&t={tid}&sig={sig}"
    req = urllib.request.Request(url, headers={"Host": f"127.0.0.1:{srv.server_port}"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None


code, view = api("plan1")
check("the route answers", code == 200 and view, code)
check("it holds the live state", view["goal"] == "Ship it" and {n["id"] for n in view["nodes"]} == {"g", "t"})
check("it lists only Markdown documents", sorted(d["name"] for d in view["docs"]) == ["bad.md", "brief.md"], view["docs"])
check("a bad byte does not hide a document", any(d["name"] == "bad.md" and "caf" in d["text"] for d in view["docs"]))
check("it gives the folder for the new card's notes", view["dir"] == folder)
code, again = api("plan1", view["sig"])
check("nothing changed: says so", again == {"same": True}, again)
report("plan1", "plan", json.dumps({"phase": "review"}))
_, newer = api("plan1", view["sig"])
check("a new update is sent", newer.get("phase") == "review", newer)
with open(os.path.join(folder, "brief.md"), "a") as f:
    f.write("More\n")
_, newer2 = api("plan1", newer["sig"])
check("a saved document is sent", any("More" in d["text"] for d in newer2.get("docs", [])), newer2)
check("a card that is not a plan card is refused", api("plain1")[0] == 404, api("plain1")[0])
check("an unknown card is refused", api("nosuch")[0] == 404)

report("build1", "plan", json.dumps({"phase": "tests", "nodes": [{"id": "T1", "kind": "task", "label": "First", "status": "open"}]}))
code, bview = api("build1")
check("S24 plan_view on a Build card returns its own nodes", code == 200 and [n["id"] for n in bview["nodes"]] == ["T1"], (code, bview))
check("S25 plan_view on a Build card returns the plan card's documents",
      code == 200 and "brief.md" in [d["name"] for d in bview["docs"]] and bview["dir"] == folder, (code, bview))
refused = {x: api(x)[0] for x in ("plain1", "badid", "badtype", "buildans")}
check("S26 plan_view on a plain card, or one with a bad buildOf, is refused", set(refused.values()) == {404}, refused)
code, gone = api("buildgone")
check("S27 plan_view on a Build card whose plan folder is gone returns no documents and no error", code == 200 and gone["docs"] == [], (code, gone))
srv.shutdown()


# --- the lookups, from a real-shaped transcript
os.makedirs(server.PROJECTS_DIR + "/x", exist_ok=True)
lines = [{"type": "assistant", "timestamp": "2026-11-01T10:00:00.000Z", "message": {"content": [
             {"type": "tool_use", "id": "1", "name": "mcp__codebase-memory-mcp__search_graph", "input": {"query": "plan pane", "project": "p"}},
             {"type": "tool_use", "id": "2", "name": "mcp__codebase-memory-mcp__index_repository", "input": {"repo_path": "/x"}},
             {"type": "tool_use", "id": "3", "name": "Read", "input": {"file_path": "/x/server.py"}},
             {"type": "tool_use", "id": "4", "name": "Grep", "input": {"pattern": "plan_view"}},
             {"type": "tool_use", "id": "5", "name": "Bash", "input": {"command": "ls"}},
             {"type": "tool_use", "id": "6", "name": "mcp__other__thing", "input": {"query": "no"}}]}},
         {"type": "assistant", "isSidechain": True, "timestamp": "2026-11-01T10:00:01.000Z", "message": {"content": [
             {"type": "tool_use", "id": "7", "name": "Read", "input": {"file_path": "/x/subagent.py"}}]}},
         {"type": "user", "timestamp": "2026-11-01T10:00:02.000Z", "message": {"content": "text, not a list"}}]
with open(os.path.join(server.PROJECTS_DIR, "x", "sess1.jsonl"), "w") as f:
    f.write("\n".join(json.dumps(x) for x in lines) + "\n")
rows = server.plan_lookups("sess1")
check("newest first", [r["what"] for r in rows] == ["plan_view", "/x/server.py", "search_graph: plan pane"], rows)
check("kinds are named", [r["tool"] for r in rows] == ["search", "read", "map"], rows)
check("no session, no lookups", server.plan_lookups(None) == [] and server.plan_lookups("missing") == [])

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
