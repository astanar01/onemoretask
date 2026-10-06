"""Auto mode's safety check ends a turn: the board must spot it, say so on the card and resume the session itself.

Run: python3 tests/test_auto_mode_stall.py  (stdlib only; a fresh temp HOME, every session call faked)."""
import json
import os
import sys
import tempfile
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="stall-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

# The real transcript tail (strings trimmed): Agent tool_use, its denied result, the CLI's note, metadata.
FIXTURE = [json.loads(x) for x in open(os.path.join(HERE, "fixtures", "auto_mode_stall.jsonl"), encoding="utf-8")]
SID = FIXTURE[0]["sessionId"]
STALL_AT = server._ts(next(e["timestamp"] for e in FIXTURE if e.get("subtype") == "informational"))

server.PROJECTS_DIR = os.path.join(os.environ["HOME"], ".claude", "projects")
tdir = os.path.join(server.PROJECTS_DIR, "-fake-project")
os.makedirs(tdir)


def write_transcript(sid, entries):
    with open(os.path.join(tdir, sid + ".jsonl"), "w", encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in entries)


def add_entries(sid, entries):
    with open(os.path.join(tdir, sid + ".jsonl"), "a", encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in entries)


def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def user_entry(t):
    return {"type": "user", "isSidechain": False, "timestamp": iso(t), "message": {"role": "user", "content": "go on"}}


def stall_entry(t):
    e = dict(next(e for e in FIXTURE if e.get("subtype") == "informational"))
    return dict(e, timestamp=iso(t))


# ---- the helper, on the real lines
write_transcript(SID, FIXTURE)
assert server.auto_mode_stall(SID) == STALL_AT, server.auto_mode_stall(SID)
add_entries(SID, [user_entry(STALL_AT + 70)])
assert server.auto_mode_stall(SID) is None, "a later user entry starts a new turn"
write_transcript("plain", [e for e in FIXTURE if e.get("subtype") != "informational"])
assert server.auto_mode_stall("plain") is None, "no stall note, no stall"
print("ok  auto_mode_stall: detects the real stall, clears on a later message, None without it")

# ---- the real Watcher.poll() with every session call faked
proj = os.path.join(TMP, "proj")
os.makedirs(proj)
p = server.Project(proj)
os.makedirs(p.board)
tasks = {"columns": [{"id": "todo", "name": "To do"}, {"id": "done", "name": "Done", "done": True}],
         "tasks": [{"id": "t1", "title": "Stalled task", "column": "todo", "agent": {"id": "a0", "started": ""}}]}


def save_tasks():
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump(tasks, f)


save_tasks()
sessions = {"a0": {"id": "a0", "sessionId": SID, "state": "idle", "status": "idle"}}
resumes, alerts, clock = [], [], [STALL_AT + 10]


def fake_resume(p_, info, message):
    new = f"r{len(resumes) + 1}"
    old = sessions.pop(info["id"])
    sessions[new] = dict(old, id=new)
    resumes.append((info["sessionId"], message))
    add_entries(info["sessionId"], [user_entry(clock[0])])  # the resumed turn starts in the transcript
    return new


def no_claude(*a, **kw):
    raise RuntimeError("no real claude in this test")


server.projects = lambda: [p]
server.list_sessions = lambda cwd: [dict(s) for s in sessions.values()]
server.resume = fake_resume
server.run_claude = no_claude
server.notify = lambda title, sub, msg: alerts.append((title, msg))
server.time = types.SimpleNamespace(time=lambda: clock[0], sleep=time.sleep)
W = server.WATCH
TRY_AGAIN = "Claude Code's safety check kept failing, so Claude stopped. The board tries again in a minute."
REPLY = "Claude Code's safety check kept failing, so Claude stopped. Reply to try again."


def poll():
    W.poll()
    for _ in range(200):
        if not W.delivering:
            break
        time.sleep(0.01)
    assert not W.delivering, "a retry thread hung"


def card(task_id="t1"):
    return W.snapshot(p.id)[task_id]


write_transcript(SID, FIXTURE)
poll()  # primes the watcher, like the first pass after a server start
assert card()["phase"] == "needs_you" and card()["reason"] == TRY_AGAIN, card()["reason"]
clock[0] = STALL_AT + server.STALL_RETRY - 1
poll()
assert not resumes, "resumed before STALL_RETRY"
print("ok  card reads:", TRY_AGAIN, "| no resume before", server.STALL_RETRY, "s")

for n in range(1, server.STALL_TRIES + 1):
    clock[0] += 2 if n == 1 else 100
    poll()
    assert len(resumes) == n, (n, len(resumes))
    assert resumes[-1][1].startswith(server.STALL_MESSAGE) and "report.py" in resumes[-1][1]
    log = reports.read(p.board, "t1")
    assert log[-1]["status"] == "resume" and log[-1]["message"] == server.STALL_NOTE and log[-1]["session"] == f"r{n}"
    assert card()["phase"] == "working", card()
    poll()
    assert len(resumes) == n and card()["phase"] == "working", "one resume per stall"
    # The resumed session stalls again at once.
    add_entries(SID, [user_entry(clock[0] + 1), stall_entry(clock[0] + 5)])
print("ok  exactly one resume per stall, after STALL_RETRY; card logs:", server.STALL_NOTE)

assert not alerts, f"alerted while the board retries: {alerts}"
clock[0] += 100
poll()
assert len(resumes) == server.STALL_TRIES, "retried past STALL_TRIES"
assert card()["phase"] == "needs_you" and card()["reason"] == REPLY, card()["reason"]
assert alerts == [("Claude needs you", REPLY)], alerts
print(f"ok  stops after {server.STALL_TRIES} retries; card reads: {REPLY}; one alert")

# A report from Claude in between resets the count.
reports.append(p.board, "t1", "progress", "did a step", "claude")
poll()  # the stall is already over a minute old: it resumes at once
assert len(resumes) == server.STALL_TRIES + 1, "a report should reset the retry count"
print("ok  a report from Claude resets the count")

# ---- pending card note: the note delivery resumes it, not the stall retry
SID2 = "22222222-2222-2222-2222-222222222222"
write_transcript(SID2, [dict(e, sessionId=SID2) for e in FIXTURE[:-2]] + [stall_entry(clock[0] - 200)])
sessions["b0"] = {"id": "b0", "sessionId": SID2, "state": "idle", "status": "idle"}
tasks["tasks"].append({"id": "t2", "title": "Note task", "column": "todo", "agent": {"id": "b0", "started": ""}})
save_tasks()
reports.append(p.board, "t2", "note", "please also check the logs", "you")
before = len(resumes)
poll()
mine = [m for sid, m in resumes[before:] if sid == SID2]
assert len(mine) == 1 and "please also check the logs" in mine[0] and not mine[0].startswith(server.STALL_MESSAGE), mine
print("ok  pending note: one resume, by the note delivery, no stall retry")

# ---- a closed card is left to the recap
SID3 = "33333333-3333-3333-3333-333333333333"
write_transcript(SID3, [dict(e, sessionId=SID3) for e in FIXTURE])
sessions["c0"] = {"id": "c0", "sessionId": SID3, "state": "idle", "status": "idle"}
tasks["tasks"].append({"id": "t3", "title": "Done task", "column": "done", "agent": {"id": "c0", "started": ""}})
save_tasks()
server.archive = lambda p_, task_id: None  # the recap would run Claude
before = len(resumes)
poll()
assert not [m for sid, m in resumes[before:] if sid == SID3], "retried a closed card"
print("ok  closed card: no retry")

# ---- a resume that fails is not repeated; the card asks for a reply and alerts once
SID4 = "44444444-4444-4444-4444-444444444444"
write_transcript(SID4, [dict(e, sessionId=SID4) for e in FIXTURE[:-2]] + [stall_entry(clock[0] - 200)])
sessions["d0"] = {"id": "d0", "sessionId": SID4, "state": "idle", "status": "idle"}
tasks["tasks"].append({"id": "t4", "title": "Failing resume", "column": "todo", "agent": {"id": "d0", "started": ""}})
save_tasks()
failed = []
ok_resume = server.resume
server.resume = lambda p_, info, m: failed.append(m) or no_claude() if info["sessionId"] == SID4 else ok_resume(p_, info, m)
alerts.clear()
poll()
poll()
assert len(failed) == 1, f"failed resume repeated: {len(failed)}"
assert card("t4")["reason"] == REPLY, card("t4")["reason"]
clock[0] += server.ALERT_WAIT + 1  # the alert waits for the board page to show the change, or this long
poll()
assert len(failed) == 1 and [a for a in alerts if a[1] == REPLY] == [("Claude needs you", REPLY)], alerts
print("ok  failed resume: tried once, card reads 'Reply to try again', one alert")
print("PASS")
