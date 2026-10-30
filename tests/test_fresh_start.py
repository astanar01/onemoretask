"""A cold card's fit for a fresh start (fresh_start), and POST /api/agent/spawn (follow-up card from a recap).

Run: python3 tests/test_fresh_start.py  (stdlib + git; a fresh temp HOME, no real claude)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="fresh-start-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

server.PROJECTS_DIR = os.path.join(os.environ["HOME"], ".claude", "projects")
tdir = os.path.join(server.PROJECTS_DIR, "-fake-project")
os.makedirs(tdir)
fails = 0


def check(ok, what, detail=""):
    global fails
    fails += not ok
    print("ok  " if ok else "FAIL", what, "" if ok else detail)


def bash(cmd):
    return ("Bash", {"command": cmd})


def transcript(sid, uses):
    """A transcript whose assistant turns call these tools, in order."""
    with open(os.path.join(tdir, sid + ".jsonl"), "w", encoding="utf-8") as f:
        for i, (name, inp) in enumerate(uses):
            f.write(json.dumps({"type": "assistant", "timestamp": "2026-10-14T10:00:00.000Z", "message": {
                "id": f"m{i}", "role": "assistant", "model": "claude-opus-5-5",
                "content": [{"type": "tool_use", "id": f"tu{i}", "name": name, "input": inp}]}}) + "\n")


def git(cwd, *args):
    subprocess.run(["git", "-C", cwd, "-c", "user.name=t", "-c", "user.email=t@t", *args], check=True,
                   capture_output=True)


INFO = {"tokens": 180000, "expires": 0}
DONE = [{"from": "claude", "status": "done", "message": "Done."}]
NO_WT = {"title": "x", "agent": {"id": "a0"}}


def fit(sid, uses, log=DONE, t=NO_WT):
    transcript(sid, uses)
    return server.fresh_start(t, log, sid, INFO)


r = fit("good", [bash("python3 report.py done 'ok'"), ("Read", {"file_path": "/x"})])
check(r == {"fit": "good", "reasons": [], "resume_tokens": 180000, "new_tokens": 56500}, "reported done: good", r)
check(server.fresh_start(NO_WT, DONE, "good", INFO) is r, "second call is memoised")

r = fit("question", [bash("python3 report.py question 'which?'")],
        log=DONE + [{"from": "you", "status": "reply", "message": "hm"},
                    {"from": "claude", "status": "question", "message": "Which one?"}])
check(r["fit"] == "bad" and r["reasons"] == ["open question"], "last Claude entry a question: bad", r)

r = fit("timer", [bash("python3 report.py progress 'waiting'"), ("ScheduleWakeup", {"delaySeconds": 600})])
check(r["reasons"] == ["waiting on a timer"], "timer after the last report: bad", r)
r = fit("timer-old", [("Monitor", {"command": "x"}), bash("python3 report.py done 'ok'")])
check(r["fit"] == "good", "timer before the last report: good", r)

# A tiny real repo: a tracked file, an ignored one, then a branch with a commit main lacks.
repo = os.path.join(TMP, "repo")
os.makedirs(repo)
git(repo, "init", "-q", "-b", "main")
with open(os.path.join(repo, ".gitignore"), "w") as f:
    f.write("ignored.txt\n")
tracked, ignored = os.path.join(repo, "a.txt"), os.path.join(repo, "ignored.txt")
for x in (tracked, ignored):
    with open(x, "w") as f:
        f.write("1\n")
git(repo, "add", "-A")
git(repo, "commit", "-q", "-m", "init")

r = fit("edit-after", [bash("git commit -m x"), ("Edit", {"file_path": tracked}), bash("python3 report.py done 'ok'")])
check(r["reasons"] == ["edits after the last commit"], "tracked file edited after the last commit: bad", r)
r = fit("edit-before", [("Edit", {"file_path": tracked}), bash("git -C /r commit -m x"), bash("python3 report.py done 'ok'")])
check(r["fit"] == "good", "edit then commit: good", r)
r = fit("edit-ignored", [bash("git commit -m x"), ("Write", {"file_path": ignored}),
                         ("Write", {"file_path": os.path.join(TMP, "scratch.txt")})])
check(r["fit"] == "good", "ignored or out-of-repo files written after the commit: good", r)
r = fit("commit-words", [bash("git commit -m x"), ("Edit", {"file_path": tracked}),
                         bash("python3 report.py done 'Edited a.txt; did not git commit it yet'")])
check(r["reasons"] == ["edits after the last commit"], "'git commit' inside a report is not a commit: bad", r)
r = fit("commit-chained", [("Edit", {"file_path": tracked}), bash("cd /r && git add -A && git commit -m y")])
check(r["fit"] == "good", "edit then chained git commit: good", r)

wt_task = {"title": "x", "agent": {"id": "a0", "worktree": {"path": repo, "branch": "feat", "base": "main"}}}
r = fit("wt-clean", [bash("python3 report.py done 'ok'")], t=wt_task)
check(r["fit"] == "good", "clean worktree with nothing unmerged: good", r)
git(repo, "checkout", "-q", "-b", "feat")
git(repo, "commit", "-q", "--allow-empty", "-m", "work")
with open(tracked, "w") as f:
    f.write("2\n")
r = fit("wt-dirty", [bash("python3 report.py done 'ok'")], t=wt_task)
check(r["reasons"] == ["uncommitted work in the worktree", "unmerged commits in the worktree"],
      "dirty worktree with an unmerged commit: bad", r)

# ---- POST /api/agent/spawn
proj = os.path.join(TMP, "proj")
os.makedirs(proj)
p = server.Project(proj)
os.makedirs(p.board)
with open(p.data, "w", encoding="utf-8") as f:
    json.dump({"columns": [], "tasks": [{"id": "t1", "title": "Old task", "tags": ["ui"], "column": "review",
                                         "agent": {"id": "a0", "model": "opus"}}]}, f)
reports.append(p.board, "t1", "done", "All done.", "claude")
transcript("s0", [bash("python3 report.py done 'ok'")])
server.projects = lambda: [p]
server.list_sessions = lambda cwd: [{"id": "a0", "sessionId": "s0", "state": "done"}]
server.write_recap = lambda *a: "## Goal\nDo the thing."
ran = []


class Done:
    returncode, stdout, stderr = 0, "", ""


server.run_claude = lambda args, **k: ran.append(args) or Done()
httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=httpd.serve_forever, daemon=True).start()


def post(body):
    req = urllib.request.Request(f"http://127.0.0.1:{httpd.server_address[1]}/api/agent/spawn",
                                 json.dumps(body).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


path = server.shell_path(os.path.join(tdir, "s0.jsonl"))
code, r = post({"p": p.id, "taskId": "t1", "close": False})
want = {"ok": True, "title": "Follow-up: Old task", "tags": ["ui"], "model": "opus",
        "notes": f'Continues the card "Old task" (t1).\n\n## Goal\nDo the thing.\n\nFull conversations: {path}'}
check(code == 200 and r == want, "close=false: follow-up card data", (code, r))
check(not ran and len(reports.read(p.board, "t1")) == 1 and not os.path.exists(reports.recap_path(p.board, "t1")),
      "close=false: old card and session untouched", (ran, reports.read(p.board, "t1")))

code, r = post({"p": p.id, "taskId": "t1", "close": True})
check(code == 200 and r == want, "close=true: same follow-up card data", (code, r))
log = reports.read(p.board, "t1")
check(ran == [["rm", "a0"]] and log[-1]["status"] == "recap" and os.path.exists(reports.recap_path(p.board, "t1")),
      "close=true: archived (recap saved, session removed, recap on the old card)", (ran, log[-1]))

# A reply sent while spawn writes the recap becomes a note; spawn hands it to a new session once archived.
state = server.load_tasks(p)
state["tasks"].append({"id": "t2", "title": "Other task", "column": "review", "agent": {"id": "a2"}})
with open(p.data, "w", encoding="utf-8") as f:
    json.dump(state, f)
reports.append(p.board, "t2", "done", "All done.", "claude")
transcript("s2", [bash("python3 report.py done 'ok'")])
server.list_sessions = lambda cwd: [{"id": "a2", "sessionId": "s2", "state": "done"}]
noted, reopened = [], []


def recap_with_reply(*a):
    noted.append(server.WATCH.note_if_closing(p, "t2", "wait, also do X", []))
    return "## Goal\nOther thing."


server.write_recap = recap_with_reply
real_reopen, server.reopen = server.reopen, lambda p_, tid, text, images=(), handed=0: reopened.append((tid, text, handed))
r = server.spawn(p, "t2", True)
server.reopen = real_reopen
check(r["ok"] and noted == [True], "spawn close: a reply during the recap is kept as a note", (r, noted))
check(len(reopened) == 1 and reopened[0][0] == "t2" and "wait, also do X" in reopened[0][1] and reopened[0][2] == 1,
      "spawn close: the note reopens the card in a new session", reopened)
check(not reports.take_notes(p.board, "t2")[0] and (p.id, "t2") not in server.WATCH.closing,
      "spawn close: no note left pending, card no longer closing", (reports.read(p.board, "t2"), server.WATCH.closing))

# A done card whose sessions were already closed (recap saved, session gone): start from the saved recap.
server.list_sessions = lambda cwd: []
ran.clear()
for close in (False, True):
    code, r = post({"p": p.id, "taskId": "t1", "close": close})
    check(code == 200 and r == want, f"closed card, close={close}: follow-up from the saved recap", (code, r))
check(not ran and (p.id, "t1") not in server.WATCH.closing, "closed card: nothing removed, not left closing",
      (ran, server.WATCH.closing))

# Moved to done a moment ago: the watcher is still writing the recap. Spawn waits for it, then uses it.
state = server.load_tasks(p)
state["tasks"].append({"id": "t3", "title": "Asked", "column": "done", "agent": {"id": "a3", "model": "sonnet"}})
with open(p.data, "w", encoding="utf-8") as f:
    json.dump(state, f)
server.WATCH.closing.add((p.id, "t3"))


def watcher_finishes():
    time.sleep(1)
    with open(reports.recap_path(p.board, "t3"), "w", encoding="utf-8") as f:
        f.write("## Goal\nAnswer.\n\n" + server.TRANSCRIPTS + "\n\nFull conversations:\n\n- /x/s3.jsonl\n")
    with server.WATCH.lock:
        server.WATCH.closing.discard((p.id, "t3"))


threading.Thread(target=watcher_finishes, daemon=True).start()
code, r = post({"p": p.id, "taskId": "t3", "close": True})
check(code == 200 and r["title"] == "Follow-up: Asked" and r["model"] == "sonnet"
      and r["notes"] == 'Continues the card "Asked" (t3).\n\n## Goal\nAnswer.\n\nFull conversations: /x/s3.jsonl',
      "recap being written: waits, then uses it", (code, r))
server.WATCH.closing.add((p.id, "t3"))
server.SPAWN_WAIT = 0.6
code, r = post({"p": p.id, "taskId": "t3", "close": True})
check(code == 500 and "still being written" in r.get("error", ""), "recap never finishes: says so", (code, r))
server.WATCH.closing.discard((p.id, "t3"))
server.list_sessions = lambda cwd: [{"id": "a0", "sessionId": "s0", "state": "done"}]

code, r = post({"p": p.id, "taskId": "nope", "close": False})
check(code == 404, "unknown task: 404", (code, r))
server.write_recap = lambda *a: (_ for _ in ()).throw(RuntimeError("claude -p failed: overloaded"))
server.list_sessions = lambda cwd: [{"id": "a0", "sessionId": "s0", "state": "done"}, {"id": "b1", "sessionId": "s0"}]
reports.append(p.board, "t1", "reply", "more", "you", session="b1")
code, r = post({"p": p.id, "taskId": "t1", "close": False})
check(code == 200 and r.get("older_recap") is True and "## Goal\nDo the thing." in r["notes"] and "may miss" in r["notes"],
      "failing recap: the new card starts from the saved one and says it is older", (code, r))
os.remove(reports.recap_path(p.board, "t1"))
code, r = post({"p": p.id, "taskId": "t1", "close": False})
check(code == 500 and r == {"error": "claude -p failed: overloaded"}, "failing recap, none saved: its message", (code, r))
httpd.shutdown()

print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
