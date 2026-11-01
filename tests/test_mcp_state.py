"""MCP on the card: servers that failed (server.mcp_state), every use of a tool (server.mcp_uses, codex.mcp_uses) and
the text saying what it looked for (codex.mcp_what), read from a Claude transcript or a Codex event log and carried on
the watcher's card record.

Run: python3 tests/test_mcp_state.py  (stdlib only; a fresh temp HOME, every session call faked)."""
import json
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="mcp-state-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import codex  # noqa: E402
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


def write(path, entries, mode="w"):
    with open(path, mode, encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in entries)


def delta(at, pending, failed=()):
    # The shape Claude Code 2.1 writes (seen in real transcripts): names in pending, objects in failed.
    return {"type": "attachment", "timestamp": at, "isSidechain": False,
            "attachment": {"type": "deferred_tools_delta", "addedNames": [], "pendingMcpServers": list(pending),
                           "failedMcpServers": list(failed)}}


def use(at, uid, name, inp=None):
    return {"type": "assistant", "timestamp": at, "isSidechain": False,
            "message": {"id": "m-" + uid, "content": [{"type": "tool_use", "id": uid, "name": name, "input": inp or {}}]}}


def result(at, uid, error=False):
    block = {"type": "tool_result", "tool_use_id": uid, "content": "x"}
    if error:
        block["is_error"] = True
    return {"type": "user", "timestamp": at, "isSidechain": False, "message": {"content": [block]}}


T0, T1, T2 = "2026-10-28T10:00:00.000Z", "2026-10-28T10:01:00.000Z", "2026-10-28T10:02:00.000Z"

# ---- mcp_state: failed servers only
path = os.path.join(tdir, "s1.jsonl")
write(path, [delta(T0, ["muninn", "plugin:context-mode:context-mode"])])
check(server.mcp_state("s1") is None, "servers still connecting are not shown", server.mcp_state("s1"))

write(path, [delta("2026-10-28T10:00:05.000Z", [], [{"name": "wp-debug-log", "errorCode": "ENOENT",
                                                    "error": "Executable not found in $PATH: node"}])], "a")
st = server.mcp_state("s1")
check(st == {"failed": [{"name": "wp-debug-log", "error": "Executable not found in $PATH: node"}]},
      "a failed server is listed with its error, and nothing else is on the record", st)

write(path, [{"type": "attachment", "timestamp": "2026-10-28T10:00:06.000Z", "isSidechain": False,
              "attachment": {"type": "deferred_tools_delta", "addedNames": []}}], "a")
st = server.mcp_state("s1")
check(st and st["failed"][0]["name"] == "wp-debug-log", "a newest delta with no pendingMcpServers key does not clear a failure", st)
server._mcp_memo.clear()
server._mcp_servers.clear()
st = server.mcp_state("s1")
check(st and st["failed"][0]["name"] == "wp-debug-log", "... nor when the whole file is read after a restart", st)

write(path, [delta("2026-10-28T10:00:07.000Z", []), use(T1, "open1", "mcp__muninn__muninn_recall")], "a")
st = server.mcp_state("s1")
check(st is None, "a newer delta clears the failure, and an open call does not make a record", st)
check(server.mcp_state("no-such-session") is None, "no transcript: None")

# ---- the newest delta scrolled out of the tail the board reads: it still holds, also after a board restart
long_path = os.path.join(tdir, "s2.jsonl")
write(long_path, [delta("2026-10-28T11:00:00.000Z", [], [{"name": "wp-debug-log", "error": "boom"}])])
check(server.mcp_state("s2")["failed"] == [{"name": "wp-debug-log", "error": "boom"}], "failure shows (short chat)")
chat = {"type": "assistant", "timestamp": "2026-10-28T11:01:00.000Z", "isSidechain": False,
        "message": {"id": "m", "content": [{"type": "text", "text": "x" * 100_000}]}}
write(long_path, [chat] * (server.TAIL_BYTES // 100_000 + 5), "a")
st = server.mcp_state("s2")
check(st and st["failed"] == [{"name": "wp-debug-log", "error": "boom"}],
      "failure still shows after more chat than the tail holds", st)
server._mcp_memo.clear()
server._mcp_servers.clear()  # a board restart: nothing remembered
st = server.mcp_state("s2")
check(st and st["failed"] == [{"name": "wp-debug-log", "error": "boom"}],
      "failure still shows after a restart with the delta out of the tail", st)
write(long_path, [delta("2026-10-28T11:05:00.000Z", [])], "a")
check(server.mcp_state("s2") is None, "a newer delta with no failures clears it", server.mcp_state("s2"))

# ---- full reads: a healthy session with no delta in the tail reads the whole file at most once
healthy = os.path.join(tdir, "s3.jsonl")
write(healthy, [chat] * (server.TAIL_BYTES // 100_000 + 5))
real_last, full_reads = server._last_mcp_servers, [0]


def counted(p):
    full_reads[0] += 1
    return real_last(p)


server._last_mcp_servers = counted
for _ in range(4):
    check(server.mcp_state("s3") is None, "healthy session: nothing to show")
    write(healthy, [chat], "a")
server._last_mcp_servers = real_last
check(full_reads[0] == 1, "healthy session: the whole file is read once across four changes", full_reads[0])

# ---- codex.mcp_what
w = codex.mcp_what
check(w({"code": "c", "query": "q"}) == "q", "what: query wins over code")
check(w({"queries": ["a", "b"]}) == "a, b", "what: queries joins with a comma")
check(w({"query": "", "pattern": "p"}) == "p", "what: an empty value is skipped")
check(w({"query": "x" * 500}) == "x" * 119 + "…" and len(w({"query": "x" * 500})) == 120, "what: long text ends in …")
check(w({"query": "x" * 120}) == "x" * 120, "what: text of exactly the limit is not cut")
check(w({"query": "a\n  b\t\tc"}) == "a b c", "what: line breaks collapse")
check(w({"project": "p"}) == "", "what: project alone gives nothing")
check(w({"project": "p", "other": "o"}) == "o", "what: another text value is the fallback, project skipped")
check(w({"other": 5}) == "", "what: a fallback must be text")
check(w("nope") == "" and w(None) == "" and w(["a"]) == "", "what: not a dict gives nothing")

# ---- codex.mcp_uses
log = os.path.join(TMP, "codex.jsonl")
item = {"id": "item_3", "type": "mcp_tool_call", "server": "codebase-memory-mcp", "tool": "search_graph",
        "arguments": {"project": "p", "query": "find me"}}
write(log, [{"type": "thread.started", "thread_id": "th1"}, {"type": "turn.started"},
            {"type": "item.started", "item": item}])
hour_ago = time.time() - 3600
os.utime(log, (hour_ago, hour_ago))
rows = codex.mcp_uses(log)
check(len(rows) == 1 and rows[0]["state"] == "open" and rows[0]["what"] == "find me" and rows[0]["sub"] is False
      and rows[0]["server"] == "codebase-memory-mcp" and rows[0]["tool"] == "search_graph"
      and rows[0]["id"] == "codex.jsonl:1:item_3", "Codex: a started call gives an open row", rows)
check(abs(rows[0]["at"] - hour_ago) < 1, "Codex: the first read of a log uses its last write time", rows)
first_at = rows[0]["at"]

write(log, [{"type": "item.started", "item": {**item, "id": "item_4", "tool": "other"}}], "a")
rows = codex.mcp_uses(log)
check(len(rows) == 2 and abs(rows[1]["at"] - time.time()) < 5, "Codex: a call seen live gets a time near now", rows)
check(rows[0]["at"] == first_at, "Codex: the first row's time has not moved", rows)

write(log, [{"type": "item.completed", "item": {**item, "status": "completed"}},
            {"type": "item.completed", "item": {**item, "id": "item_4", "tool": "other", "status": "failed"}}], "a")
rows = codex.mcp_uses(log)
check([r["state"] for r in rows] == ["done", "failed"], "Codex: completed gives done, failed gives failed", rows)

write(log, [{"type": "turn.completed"}, {"type": "turn.started"}, {"type": "item.started", "item": item}], "a")
rows = codex.mcp_uses(log)
check(len(rows) == 3 and rows[2]["id"] == "codex.jsonl:2:item_3" and rows[2]["state"] == "open",
      "Codex: the same item id in a later turn is a second row", rows)

write(log, [{"type": "item.completed", "item": {**item, "id": "item_9", "status": "completed"}}], "a")
check(codex.mcp_uses(log)[-1]["state"] == "done", "Codex: a completed call with no start still gives a row")
check(codex.mcp_uses(os.path.join(TMP, "gone.jsonl")) == [], "Codex: a missing log gives nothing")

# Through the server, under a path codex.is_log accepts.
real_log = os.path.join(TMP, "agent_reports", "codex", "abc.jsonl")
os.makedirs(os.path.dirname(real_log))
write(real_log, [{"type": "turn.started"}, {"type": "item.started", "item": item}])
check(codex.is_log(real_log), "the test log counts as a Codex log")
rows = server.mcp_uses([real_log])
check(len(rows) == 1 and rows[0]["id"] == "abc.jsonl:1:item_3" and rows[0]["state"] == "open",
      "server.mcp_uses reads a Codex log", rows)

# ---- server.mcp_uses on a Claude transcript
u = os.path.join(tdir, "u1.jsonl")
write(u, [use(T1, "a1", "mcp__muninn__muninn_recall", {"context": ["task board"]}),
          use(T0, "a0", "mcp__plugin_context-mode_context-mode__ctx_search", {"queries": ["x", "y"]}),
          use(T1, "b1", "Bash", {"command": "mcp__not_really"})])
rows = server.mcp_uses([u])
check([(r["server"], r["tool"], r["what"], r["state"]) for r in rows]
      == [("context-mode", "ctx_search", "x, y", "open"), ("muninn", "muninn_recall", "task board", "open")],
      "two uses and a Bash call give two rows, oldest first, short plugin names", rows)
check(rows[0]["at"] == server._ts(T0) and rows[0]["sub"] is False, "a row has its time and sub false", rows)

# The trap: a list already handed out must not change when a result arrives.
old = server.mcp_uses([u])
write(u, [result(T2, "a1")], "a")
new = server.mcp_uses([u])
check([r["state"] for r in old] == ["open", "open"], "the list handed out earlier still says open", old)
check(old != new, "old and new lists differ once a result arrives")
check(new[1]["state"] == "done" and new[0]["state"] == "open", "a use gets done from its result", new)
write(u, [result(T2, "a0", error=True)], "a")
check(server.mcp_uses([u])[0]["state"] == "failed", "a result with is_error gives failed")

# A copy in another entry type. Its id is new and it has a time, so only the type check can drop it.
b = os.path.join(tdir, "u2.jsonl")
write(b, [use(T1, "c1", "mcp__muninn__muninn_recall"),
          {"type": "api-request-blob", "timestamp": T1, "message": {"content": [
              {"type": "tool_use", "id": "blob-only", "name": "mcp__muninn__muninn_recall", "input": {}}]}}])
rows = server.mcp_uses([b])
check([r["id"] for r in rows] == ["c1"], "an api-request-blob entry adds no row", rows)

# Subagents.
sub_dir = os.path.join(tdir, "u3", "subagents")
os.makedirs(sub_dir)
m = os.path.join(tdir, "u3.jsonl")
write(m, [use(T0, "m1", "mcp__muninn__muninn_recall")])
write(os.path.join(sub_dir, "x.jsonl"), [use(T1, "s1", "mcp__muninn__muninn_read")])
write(os.path.join(sub_dir, "note.txt"), [{}])
rows = server.mcp_uses([m])
check([(r["id"], r["sub"]) for r in rows] == [("m1", False), ("s1", True)], "a use in a subagent's file is marked", rows)

# The same id in two paths: a resume copies history.
f1, f2 = os.path.join(tdir, "f1.jsonl"), os.path.join(tdir, "f2.jsonl")
write(f1, [use(T0, "dup", "mcp__muninn__muninn_recall")])
write(f2, [use(T0, "dup", "mcp__muninn__muninn_recall")])
rows = server.mcp_uses([f1, f2])
check(len(rows) == 1 and rows[0]["state"] == "open", "the same id in two paths gives one row", rows)
write(f2, [result(T1, "dup")], "a")
rows = server.mcp_uses([f1, f2])
check(len(rows) == 1 and rows[0]["state"] == "done", "an open first copy and a done second copy read done", rows)
rows = server.mcp_uses([f2, f1])
check(len(rows) == 1 and rows[0]["state"] == "done", "... whichever path comes first", rows)

# A half-written last line, and no reading again from the start.
h = os.path.join(tdir, "h.jsonl")
line = json.dumps(use(T0, "h1", "mcp__muninn__muninn_recall"))
with open(h, "w", encoding="utf-8") as f:
    f.write(line[:20])
check(server.mcp_uses([h]) == [], "half a line is not read yet")
bytes_read = server._mcp_use_memo[h][0]
check(bytes_read == 0, "half a line does not count as read", bytes_read)
with open(h, "a", encoding="utf-8") as f:
    f.write(line[20:] + "\n")
check([r["id"] for r in server.mcp_uses([h])] == ["h1"], "the rest of the line is read next time")
bytes_read = server._mcp_use_memo[h][0]
check(bytes_read == os.path.getsize(h), "the memo moved to the end of the file", bytes_read)
real_loads, parsed = server.json.loads, []
server.json.loads = lambda s, *a, **k: (parsed.append(s), real_loads(s, *a, **k))[1]
write(h, [use(T1, "h2", "mcp__muninn__muninn_read")], "a")
rows = server.mcp_uses([h])
server.json.loads = real_loads
check(len(parsed) == 1 and [r["id"] for r in rows] == ["h1", "h2"], "only the new line is parsed", len(parsed))
with open(h, "w", encoding="utf-8") as f:
    f.write(json.dumps(use(T2, "h3", "mcp__muninn__muninn_read")) + "\n")
check([r["id"] for r in server.mcp_uses([h])] == ["h3"], "a file that got smaller starts over")

# Bad times skip the use, and the next line is still read.
t = os.path.join(tdir, "t.jsonl")
bad = [use(T0, f"bad{i}", "mcp__muninn__muninn_recall") for i in range(4)]
del bad[0]["timestamp"]
bad[1]["timestamp"], bad[2]["timestamp"], bad[3]["timestamp"] = None, 12345, "not a time"
write(t, bad + [use(T1, "good", "mcp__muninn__muninn_read"), {"type": "assistant", "message": "mcp__ text"}])
rows = server.mcp_uses([t])
check([r["id"] for r in rows] == ["good"], "a missing, null, number or bad time skips the use, and the next is read", rows)

# More than the cap keeps the newest.
c = os.path.join(tdir, "c.jsonl")
n = server.MCP_USES_SHOWN + 5
write(c, [use(f"2026-10-28T10:{i // 60:02d}:{i % 60:02d}.000Z", f"c{i}", "mcp__muninn__muninn_recall") for i in range(n)])
rows = server.mcp_uses([c])
check(len(rows) == server.MCP_USES_SHOWN and rows[0]["id"] == "c5" and rows[-1]["id"] == f"c{n - 1}",
      "more than the cap keeps the newest", len(rows))

# ---- the watcher puts it on the card record
write(path, [use("2026-10-28T10:02:00.000Z", "u9", "mcp__muninn__muninn_remember", {"concept": "a note"})], "a")
proj = os.path.join(TMP, "proj")
os.makedirs(proj)
p = server.Project(proj)
os.makedirs(p.board)
removed = os.path.join(tdir, "removed.jsonl")
write(removed, [use(T0, "r1", "mcp__muninn__muninn_read")])
with open(p.data, "w", encoding="utf-8") as f:
    json.dump({"columns": [{"id": "todo", "name": "To do"}, {"id": "done", "name": "Done", "done": True}],
               "tasks": [{"id": "t1", "title": "Task", "column": "todo", "agent": {"id": "a1", "started": ""}},
                         {"id": "t2", "title": "Removed", "column": "todo", "agent": {"id": "gone", "started": ""}}]}, f)
reports.append(p.board, "t1", "progress", "working on it", "claude")
reports.append(p.board, "t2", "progress", "worked on it", "claude")
with open(reports.recap_path(p.board, "t2"), "w", encoding="utf-8") as f:
    f.write(f"The recap.\n{server.TRANSCRIPTS}\n- {removed}\n")
server.projects = lambda: [p]
server.list_sessions = lambda cwd: [{"id": "a1", "sessionId": "s1", "state": "working", "status": "busy"}]
server.run_claude = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))
server.WATCH.poll()
rec = server.WATCH.snapshot(p.id).get("t1") or {}
rows = {r["id"]: r for r in rec.get("mcp_uses") or []}
check(rec.get("phase") == "working" and "u9" in rows and rows["u9"]["state"] == "open" and rows["u9"]["what"] == "a note",
      "the card record carries the row", {k: rec.get(k) for k in ("phase", "mcp_uses")})
check((server.WATCH.snapshot(p.id).get("t2") or {}).get("mcp_uses", [{}])[0].get("id") == "r1",
      "a card with a removed session: a recap naming the transcript still gives rows", server.WATCH.snapshot(p.id).get("t2"))

rev = server.WATCH.revs.get((p.id, "t1"))
write(path, [result(T2, "u9")], "a")
server.WATCH.poll()
rec = server.WATCH.snapshot(p.id).get("t1") or {}
rows = {r["id"]: r for r in rec.get("mcp_uses") or []}
check(rows.get("u9", {}).get("state") == "done", "the state moves to done after a result", rows.get("u9"))
check(rev is not None and server.WATCH.revs.get((p.id, "t1")) > rev, "the change count moves, so the page redraws",
      (rev, server.WATCH.revs.get((p.id, "t1"))))

# ---- a finished card: never read after a restart, and it keeps the rows it had when it was moved to done
def set_columns(**columns):
    with open(p.data, encoding="utf-8") as f:
        board = json.load(f)
    for t_ in board["tasks"]:
        t_["column"] = columns.get(t_["id"], t_["column"])
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump(board, f)


set_columns(t2="done")
reads, real_calls = [], server._mcp_calls
server._mcp_calls = lambda f: (reads.append(f), real_calls(f))[1]
server.WATCH.poll()
rec = server.WATCH.snapshot(p.id).get("t2") or {}
check([r["id"] for r in rec.get("mcp_uses") or []] == ["r1"], "moved to done: the card keeps its rows", rec.get("mcp_uses"))
check(removed not in reads, "moved to done: its chat is not read again", reads)
server.WATCH.cache.clear()  # a board restart: nothing remembered
server._mcp_use_memo.clear()
reads.clear()
server.WATCH.poll()
rec = server.WATCH.snapshot(p.id).get("t2") or {}
check(rec.get("mcp_uses") == [], "after a restart a finished card has no rows", rec.get("mcp_uses"))
check(removed not in reads, "after a restart a finished card's chat is not read", reads)
check(path in reads, "... while an open card's chat still is", reads)
set_columns(t2="todo")
server.WATCH.poll()
rec = server.WATCH.snapshot(p.id).get("t2") or {}
check([r["id"] for r in rec.get("mcp_uses") or []] == ["r1"], "reopened: the rows come back", rec.get("mcp_uses"))
set_columns(t1="done")
reads.clear()
server.WATCH.poll()
check(path in reads, "a finished card whose session is still there is still read", reads)
server._mcp_calls = real_calls

print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
