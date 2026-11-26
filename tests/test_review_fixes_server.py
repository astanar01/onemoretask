"""Server bugs found by the 2026-11-15 whole-app review, one check each: the repo-size route, Create repo under an
empty parent repo, a failed session listing, two card messages at once, a resume that lands mid-pass, a second
review after fixes, fixes that keep the feature and the board's check of each fix, trust with CLAUDE_CONFIG_DIR,
changed-file names, an odd transcript line, a refused delete that must not stop the app, and forgetting a project
with a scheduled skill review.

Run: python3 tests/test_review_fixes_server.py  (stdlib only, no Claude session started)."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="review-fixes-"))
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.makedirs(os.environ["HOME"])
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.environ["GIT_CONFIG_GLOBAL"] = os.path.join(TMP, "gitconfig")
with open(os.environ["GIT_CONFIG_GLOBAL"], "w") as f:
    f.write("[user]\n\tname = t\n\temail = t@t\n[init]\n\tdefaultBranch = main\n")
sys.path.insert(0, ROOT)
import server  # noqa: E402

REAL_LIST_SESSIONS = server.list_sessions
server.list_sessions = lambda cwd: []
server.run_claude = lambda args, **k: (_ for _ in ()).throw(AssertionError(f"claude called: {args}"))
FAILS = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


def sh(cwd, *a):
    return subprocess.run(a, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


def repo(path):
    os.makedirs(path, exist_ok=True)
    sh(path, "git", "init", "-q")
    return path


def project(name, tasks=()):
    p = server.open_project(repo(os.path.join(TMP, name)))
    server.reports.ensure_board(p.board)
    server.write_board(p, {"version": 1, "columns": [{"id": "c", "name": "Review", "role": "review"}],
                           "tasks": list(tasks)})
    return p


class Done:
    def __init__(self, out="", code=0, err=""):
        self.stdout, self.stderr, self.returncode = out, err, code


# --- POST /api/git/scan is routed (it answered 404, so Create repo never listed big folders)
p0 = project("scan")
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"
req = urllib.request.Request(base + "/api/git/scan", method="POST", data=json.dumps({"p": p0.id}).encode(),
                             headers={"Content-Type": "application/json", "Origin": base})
try:
    with urllib.request.urlopen(req) as r:
        code, body = r.status, json.loads(r.read())
except urllib.error.HTTPError as e:
    code, body = e.code, {}
check(code == 200 and "entries" in body, f"POST /api/git/scan answers the folder sizes (got {code})")
srv.shutdown()

# --- Create repo under a folder with an empty `git init`: the project gets its own repo
home = repo(os.path.join(TMP, "fakehome"))
os.makedirs(os.path.join(home, ".ssh"))
open(os.path.join(home, ".ssh", "id_ed25519"), "w").write("SECRET")
shop = os.path.join(home, "sites", "shop")
os.makedirs(os.path.join(shop, "big"))
open(os.path.join(shop, "index.php"), "w").write("<?php")
open(os.path.join(shop, "big", "video.mp4"), "w").write("x")
ps = server.open_project(shop)
info = server.init_repo(ps, leave_out=["big"])
files = sh(shop, "git", "show", "--name-only", "--format=", "HEAD").split("\n")
check(os.path.realpath(sh(shop, "git", "rev-parse", "--show-toplevel")) == os.path.realpath(shop),
      "init_repo: the commit lands in the project's own repo")
check(info["commits"] and files == ["index.php"], f"init_repo: only the project's files, big left out ({files})")
check(subprocess.run(["git", "-C", home, "rev-parse", "HEAD"], capture_output=True).returncode != 0,
      "init_repo: the parent's empty repo got no commit")

# --- a failed `claude agents` is an error, not "no sessions"
for out, rc, what in (("", 1, "a non-zero exit"), ("not json", 0, "output that is not JSON")):
    server.run_claude = lambda args, **k: Done(out, rc, "Error: not logged in")
    try:
        REAL_LIST_SESSIONS(TMP)
        check(False, f"list_sessions raises on {what}")
    except RuntimeError:
        check(True, f"list_sessions raises on {what}")
server.run_claude = lambda args, **k: Done('[{"id": "a1"}, {"name": "terminal"}]')
check([s["id"] for s in REAL_LIST_SESSIONS(TMP)] == ["a1"], "list_sessions: a good listing still reads")

# --- two card messages at once: one resumes, the other waits for the lock and becomes a note
card = {"id": "t1", "title": "x", "column": "c", "agent": {"id": "aaaa1111"}}
p1 = project("race", [card])
server.WATCH._put((p1.id, "t1"), {"id": "aaaa1111", "sessionId": "11111111-2222-3333-4444-555555555555",
                                  "state": "idle", "phase": "finished", "reason": "", "log": []})
server.all_sessions = lambda p: [{"id": "aaaa1111"}]
calls = []


def slow_claude(args, **k):
    calls.append(args[:2])
    if "--resume" in args:
        time.sleep(1)
        return Done("backgrounded · bbbb0001")
    return Done()


server.run_claude = slow_claude
res = []
th = [threading.Thread(target=lambda m=m: res.append(server.reply(p1, "t1", m))) for m in ("first", "second")]
[x.start() for x in th]
[x.join() for x in th]
resumes = sum(1 for c in calls if "--resume" in c)
check(resumes == 1 and sorted(res) == [False, True], f"two replies at once: one resume, one note ({resumes}, {res})")
notes = [e for e in server.reports.read(p1.board, "t1") if e.get("status") == "note"]
check(len(notes) == 1, "two replies at once: the second is kept as a note")

# --- a resume that lands after its card was read this pass is not overwritten by the end of the pass
p2 = project("midpass", [{"id": "a", "title": "a", "column": "c", "agent": {"id": "s1"}},
                         {"id": "b", "title": "b", "column": "c", "agent": {"id": "s2"}}])
server.list_sessions = lambda cwd: [{"id": "s1", "sessionId": "x1", "status": "idle", "state": "idle"},
                                    {"id": "s2", "sessionId": "x2", "status": "idle", "state": "idle"}]
server.projects = lambda: [p2]
real_read = server.reports.read
hit = []


def read_and_resume(board, task_id):
    if board == p2.board and task_id == "b" and not hit:  # card "a" was read already: resume it now
        hit.append(1)
        server.WATCH.expect(p2, "a", "s9")
    return real_read(board, task_id)


server.reports.read = read_and_resume
server.WATCH.poll()
server.reports.read = real_read
check(server.WATCH.snapshot(p2.id)["a"]["phase"] == "working", "a resume during the pass keeps its card working")

# --- a second review after a hand-back quotes the fix report, so the fix commit is kept
p3 = project("review2")
started = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
server.write_board(p3, {"version": 1, "columns": [{"id": "c", "name": "Review", "role": "review"}],
                        "tasks": [{"id": "t1", "title": "T", "column": "c", "agent": {"id": "w1", "started": started}}]})


def commit(name):
    open(os.path.join(p3.path, name), "w").write(name)
    sh(p3.path, "git", "add", "-A")
    sh(p3.path, "git", "commit", "-qm", name)
    return sh(p3.path, "git", "rev-parse", "--short", "HEAD")


A = server.reports.append
A(p3.board, "t1", "launch", "Sent", "you", session="w1")
s1 = commit("feature")
A(p3.board, "t1", "done", f"Built the feature in {s1}", "claude", session="w1")
A(p3.board, "t1", "review", "Code review started", "you", session="r1")
A(p3.board, "t1", "done", f"Finding 1: off by one in {s1}", "claude", session="r1")
A(p3.board, "t1", "reply", "Fix finding 1", "you", session="w1")
s2 = commit("fix")
A(p3.board, "t1", "done", f"Fixed finding 1 in {s2}", "claude", session="w1")
prompts = []
server.start_session = lambda p, tid, name, prompt, *a, **k: prompts.append(prompt) or "r2"
server.WATCH.cache.pop((p3.id, "t1"), None)
server.review(p3, "t1")
quoted = prompts[0][prompts[0].index("Its last report"):prompts[0].index("Commits on")]
check(s1 in quoted and s2 in quoted and "Finding 1" not in quoted,
      "second review: quotes the work and fix reports, not the earlier findings")

# --- a fix keeps the feature the task built: both fix prompts carry the rule
KEEP = ("must work the same way after the fix, minus the bug", "Fix the cause, never hide the symptom",
        "Never remove or weaken a feature, a check or a test", "do not commit: ask on the card with options",
        "the main path the task notes describe", "what behavior was kept")
later = prompts[0][prompts[0].index("If a later reply asks for fixes"):]
check(all(k in later for k in KEEP), "review prompt: a later fix keeps the feature the task built")

p6 = project("fixcheck")
server.write_board(p6, {"version": 1, "columns": [{"id": "c", "name": "Review", "role": "review"}],
                        "tasks": [{"id": "t1", "title": "Keep drafts", "notes": "Save keeps the draft open.",
                                   "column": "c", "agent": {"id": "w1", "started": started, "model": "opus"}}]})


def commit6(name):
    open(os.path.join(p6.path, name), "w").write(name)
    sh(p6.path, "git", "add", "-A")
    sh(p6.path, "git", "commit", "-qm", name)
    return sh(p6.path, "git", "rev-parse", "--short", "HEAD")


A(p6.board, "t1", "launch", "Sent", "you", session="w1")
f1 = commit6("feature")
A(p6.board, "t1", "done", f"Built it in {f1}", "claude", session="w1")
A(p6.board, "t1", "review", "Code review started (sonnet, session r1)", "you", session="r1", cli="codex")
FINDINGS = "Finding 1 (major): Save loses the draft\n\n- Where: `app.py:3`\n- Changes how it works: no"
A(p6.board, "t1", "done", FINDINGS, "claude", session="r1")
key6 = (p6.id, "t1")
started_now = []
server.start_session = lambda p, tid, name, prompt, model, *a, **k: started_now.append((prompt, model)) or "fc1"
server.projects = lambda: [p6]
server.WATCH.primed = True
server.WATCH._put(key6, {"id": "r1", "sessionId": "sr1", "state": "idle", "phase": "working", "reason": "", "log": [],
                         "cli": "codex"})
server.list_sessions = lambda cwd: [{"id": "r1", "sessionId": "sr1", "status": "idle", "state": "idle"}]
server.WATCH.poll()
check(server.WATCH.snapshot(p6.id)["t1"]["phase"] == "finished" and not started_now,
      "fix check: the review's own report starts none")

# hand the findings back to the worker: the message carries the rule too
handed = []
server.all_sessions = lambda p: [{"id": "w1", "sessionId": "sw1"}]
server.resume = lambda p, info, message: handed.append(message) or "w2"
server.handback(p6, "t1", "Fix finding 1")
check(handed and all(k in handed[0] for k in KEEP), "hand-back message: the fix keeps the feature the task built")

# the worker fixes and reports done: one fix check, once
time.sleep(1.1)  # git log --since counts whole seconds: the fix lands after the reply, as it does for real
f2 = commit6("fix")
A(p6.board, "t1", "done", f"Fixed finding 1 in {f2}: Save keeps the draft, the Save button works as before", "claude",
  session="w2")
server.list_sessions = lambda cwd: [{"id": "w2", "sessionId": "sw2", "status": "idle", "state": "idle"}]


def settle():
    for _ in range(200):
        if not server.WATCH.fixchecking:
            return
        time.sleep(0.05)


def fix_checks():
    return [e for e in server.reports.read(p6.board, "t1") if e.get("status") == "review"
            and e["message"].startswith("Fix check started by the board")]


server.WATCH.poll()
settle()
for _ in range(2):  # later passes, and the check's own start, start no second one
    server.WATCH.poll()
    settle()
check(len(started_now) == 1 and len(fix_checks()) == 1,
      f"fix check: a fix's done report starts it exactly once ({len(started_now)} started, {len(fix_checks())} logged)")
prompt6, model6 = started_now[0] if started_now else ("", "")
check(model6 == "sonnet" and f'"low {f2}"' in prompt6 and f1 not in prompt6.split("The fix commits")[-1],
      "fix check: the review's model, the low budget, only the fix commit")
check(all(x in prompt6 for x in ("Save keeps the draft open.", "Save loses the draft", "Fix finding 1",
                                  "Save keeps the draft, the Save button works", '"Fix check" section',
                                  "no file changes, no commits")),
      "fix check: the prompt has the task notes, the findings, the ask and the fix report, and changes nothing")
check(fix_checks() and fix_checks()[0].get("cli") == "claude" and fix_checks()[0].get("session") == "fc1"
      and server.WATCH.snapshot(p6.id)["t1"]["phase"] == "working", "fix check: shows on the card while it runs")

# its finding gets fixed in turn: a board restart starts no check for it; a fix seen live does
A(p6.board, "t1", "done", "Finding 1 (major): Save now closes the draft\n\n- Where: `app.py:4`", "claude", session="fc1")
A(p6.board, "t1", "reply", "Fix finding 1", "you", session="fc1")
time.sleep(1.1)
f3 = commit6("fix2")
A(p6.board, "t1", "done", f"Fixed in {f3}", "claude", session="fc1")
server.list_sessions = lambda cwd: [{"id": "fc1", "sessionId": "sfc1", "status": "idle", "state": "idle"}]
server.WATCH.cache.pop(key6, None)
server.WATCH.poll()
settle()
check(len(started_now) == 1, "fix check: none after a board restart (the fix was not seen finishing)")
server.WATCH._put(key6, {**server.WATCH.cache[key6], "phase": "working"})
server.WATCH.poll()
settle()
check(len(started_now) == 2 and f'"low {f3}"' in started_now[-1][0], "fix check: a fix of a fix check is checked too")

# a fix whose report names no commit has nothing to check
A(p6.board, "t1", "done", "Finding 1 (minor): odd text\n\n- Where: `app.py:5`", "claude", session="fc1")
A(p6.board, "t1", "reply", "Fix finding 1", "you", session="fc1")
A(p6.board, "t1", "done", "Nothing to change: the text is right", "claude", session="fc1")
check(server.fix_check(p6, "t1") is None and len(started_now) == 2, "fix check: none when the fix names no commit")

D = lambda st, fr, m="": {"status": st, "from": fr, "message": m}  # noqa: E731
base = [D("launch", "you"), D("done", "claude"), D("review", "you"), D("done", "claude")]
for tail, want, what in (([], None, "the review's report alone"),
                         ([D("reply", "you"), D("progress", "claude")], None, "a fix still at work"),
                         ([D("reply", "you"), D("done", "claude")], 4, "a fix reported done"),
                         ([D("reply", "you"), D("done", "claude"), D("progress", "claude")], 4, "a progress after it"),
                         ([D("reply", "you"), D("done", "claude"), D("reply", "you")], None, "a reply after it"),
                         ([D("reply", "you"), D("question", "claude"), D("reply", "you"), D("done", "claude")], 4,
                          "a fix that asked first"),
                         ([D("note", "you"), D("resume", "you"), D("done", "claude")], 4,
                          "a fix asked while the reviewer was still closing (a note, handed over later)")):
    check(server.fix_check_due(base + tail) == want, f"fix_check_due: {what}")

# --- trust goes to the .claude.json the CLI reads under CLAUDE_CONFIG_DIR
cfg = os.path.join(TMP, "cfg")
os.makedirs(cfg)
os.environ["CLAUDE_CONFIG_DIR"] = cfg
server.trust_folder(p3)
saved = json.load(open(os.path.join(cfg, ".claude.json")))["projects"]
check(saved.get(p3.path, {}).get("hasTrustDialogAccepted") is True,
      "trust_folder writes $CLAUDE_CONFIG_DIR/.claude.json (made when missing)")
del os.environ["CLAUDE_CONFIG_DIR"]

# --- changed files: each new file of a new folder, and counts for names git would quote
p4 = project("names")
for n in ("café.txt", 'say "hi".txt'):
    open(os.path.join(p4.path, n), "w").write("a\n")
sh(p4.path, "git", "add", "-A")
sh(p4.path, "git", "commit", "-qm", "base")
for n in ("café.txt", 'say "hi".txt'):
    open(os.path.join(p4.path, n), "a").write("b\nc\n")
os.makedirs(os.path.join(p4.path, "newpkg"))
for n in ("one.py", "two.py"):
    open(os.path.join(p4.path, "newpkg", n), "w").write("x\n")
rows, _ = server._changed_files(p4.path)
by = {r["path"]: r for r in rows}
check(by.get("café.txt", {}).get("added") == 2 and by.get('say "hi".txt', {}).get("added") == 2,
      "changed files: line counts for accented and quoted names")
check("newpkg/one.py" in by and "newpkg/two.py" in by and "newpkg/" not in by,
      "changed files: a new folder lists each file")

# --- an odd transcript line does not stop the watcher's skill reading
odd = os.path.join(TMP, "odd.jsonl")
with open(odd, "w") as f:
    f.write(json.dumps({"type": "user", "message": "Skill as a plain string"}) + "\n")
    f.write(json.dumps({"message": {"content": [{"type": "tool_use", "name": "Skill", "input": "Skill text"}]}}) + "\n")
    f.write(json.dumps({"message": {"content": [{"type": "tool_use", "id": "u1", "name": "Skill",
                                                 "input": {"skill": "run"}}]}, "timestamp": "t"}) + "\n")
try:
    used, _ = server._skill_calls(odd)
    check(used == {"u1": ("run", "t")}, "odd transcript lines are skipped, good ones still read")
except Exception as e:  # noqa: BLE001
    check(False, f"odd transcript lines are skipped ({e!r})")

# --- a refused Delete worktree leaves the running app alone
stopped = []
server.stop_run = lambda p, task_id: stopped.append(task_id)
p5 = project("delete")
open(os.path.join(p5.path, "f"), "w").write("f")
sh(p5.path, "git", "add", "-A")
sh(p5.path, "git", "commit", "-qm", "base")
wt = os.path.join(TMP, "wt")
sh(p5.path, "git", "worktree", "add", "-q", "-b", "task-1", wt)
open(os.path.join(wt, "dirty.txt"), "w").write("unsaved")
server.write_board(p5, {"version": 1, "columns": [{"id": "c", "name": "Doing"}],
                        "tasks": [{"id": "t1", "title": "T", "column": "c",
                                   "agent": {"id": "a", "worktree": {"path": wt, "branch": "task-1"}}}]})
server.live_worktree = lambda t: {"path": wt, "branch": "task-1", "dir": wt}
try:
    server.delete_worktree(p5, "t1")
    check(False, "delete with unsaved work is refused")
except server.UnsavedWorkError:
    check(stopped == [], "a refused delete does not stop the running app")

# --- forgetting a project removes its scheduled skill review
removed = []
real_remove = server.review_schedule.remove
server.review_schedule.remove = lambda path: removed.append(os.path.realpath(path))
server.review_schedule._save({os.path.realpath(p5.path): {"days": [0], "time": "09:00"}})
server.forget_project(p5.id)
server.review_schedule.remove = real_remove
check(removed == [os.path.realpath(p5.path)], "forget_project removes the project's scheduled skill review")

# --- stopping a run or a Codex turn: the leader quits on SIGTERM, a command it started ignores it
def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    return bool(stat) and not stat.startswith("Z")


if os.name == "posix":
    for name, kill in (("Run app stop", server._kill), ("Codex turn stop", server.codex._kill)):
        pidfile = os.path.join(TMP, f"child-{len(name)}.pid")
        proc = subprocess.Popen(["sh", "-c", f"(trap '' TERM; exec sleep 60) & echo $! > '{pidfile}'; wait"],
                                start_new_session=True)
        for _ in range(100):
            if os.path.exists(pidfile) and open(pidfile).read().strip():
                break
            time.sleep(0.05)
        child = int(open(pidfile).read())
        started = time.time()
        kill(proc)
        left = alive(child)
        if left:
            try:
                os.kill(child, 9)
            except ProcessLookupError:
                pass
        check(proc.poll() is not None and not left and time.time() - started < 15,
              f"{name}: a child that ignores SIGTERM is stopped too (child left={left})")

print(f"test_review_fixes_server: {'PASS' if not FAILS else 'FAIL (%d)' % len(FAILS)}")
sys.exit(1 if FAILS else 0)
