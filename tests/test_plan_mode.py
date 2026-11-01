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
                   task("ans1", answerOnly=True), task("plain1")]}
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

# --- the prompt
state = json.load(open(os.path.join(board, "tasks.json")))
P = server.Project(project)
tasks_by ={t["id"]: t for t in state["tasks"]}
prompt = server.build_prompt(P, state, tasks_by["plan1"])
folder = reports.plan_dir(board, "plan1")
check("the prompt names the plan folder", folder in prompt)
check("the prompt asks for the plan update command", " plan --file " in prompt)
for word in ("search_graph", "brief.md", "design.md", "plan.md", "fresh reviewers", "no history", "Build this plan"):
    check(f"the prompt says {word}", word in prompt)
check("the prompt does not carry the answer-only brief", "Answer only (this task" not in prompt)
check("the prompt keeps the sandbox rule", "sandbox where the project folder is read-only" in prompt)
check("the prompt allows only plan documents", "Write only the plan documents" in prompt)
check("a plain card gets no plan brief", "Plan mode" not in server.build_prompt(P, state, tasks_by["plain1"]))
check("an answer-only card keeps its brief", "Answer only (this task" in server.build_prompt(P, state, tasks_by["ans1"]))
codex_prompt = server.build_prompt(P, state, tasks_by["plan1"], model="gpt-5.4")
check("a Codex session gets its own way to start reviewers", "spawn_agent" in codex_prompt)
check("the task's sandbox is on for a plan card", server.answer_only_task(P, "plan1"))
remind = server.plan_reminder(P, tasks_by["plan1"])
check("a reply repeats the folder and the command", folder in remind and " plan --file " in remind and "plan.md" in remind, remind)


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
