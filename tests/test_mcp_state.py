"""MCP state for the Conversation tab: servers still connecting, servers that failed and calls in flight, read
from a Claude transcript (server.mcp_state) and a Codex event log (codex.mcp_state), and carried on the
watcher's card record.

Run: python3 tests/test_mcp_state.py  (stdlib only; a fresh temp HOME, every session call faked)."""
import json
import os
import sys
import tempfile

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


def use(at, uid, name):
    return {"type": "assistant", "timestamp": at, "isSidechain": False,
            "message": {"id": "m-" + uid, "content": [{"type": "tool_use", "id": uid, "name": name, "input": {}}]}}


def result(at, uid):
    return {"type": "user", "timestamp": at, "isSidechain": False,
            "message": {"content": [{"type": "tool_result", "tool_use_id": uid, "content": "x"}]}}


# ---- Claude: the newest delta is the state; plugin names read as the plugin's own name
path = os.path.join(tdir, "s1.jsonl")
write(path, [delta("2026-10-28T10:00:00.000Z", ["muninn", "plugin:context-mode:context-mode"])])
st = server.mcp_state("s1")
check(st and st["connecting"] == ["muninn", "context-mode"] and not st["calls"] and not st["failed"],
      "servers still connecting are listed", st)

write(path, [delta("2026-10-28T10:00:05.000Z", [], [{"name": "wp-debug-log", "errorCode": "ENOENT",
                                                    "error": "Executable not found in $PATH: node"}])], "a")
st = server.mcp_state("s1")
check(st and st["connecting"] == [] and st["failed"] == [{"name": "wp-debug-log",
                                                           "error": "Executable not found in $PATH: node"}],
      "a later delta clears connecting and lists the failed server with its error", st)

write(path, [delta("2026-10-28T10:00:06.000Z", []),
             use("2026-10-28T10:01:00.000Z", "u1", "mcp__muninn__muninn_recall"),
             use("2026-10-28T10:01:00.000Z", "u2", "mcp__plugin_context-mode_context-mode__ctx_search"),
             use("2026-10-28T10:01:00.000Z", "u3", "Bash")], "a")
st = server.mcp_state("s1")
calls = st and sorted((c["server"], c["tool"]) for c in st["calls"])
check(calls == [("context-mode", "ctx_search"), ("muninn", "muninn_recall")] and not st["failed"],
      "open MCP calls are listed (not Bash), plugin server name shortened", st)
check(st and st["calls"][0]["since"] == server._ts("2026-10-28T10:01:00.000Z"), "a call keeps its start time", st)

write(path, [result("2026-10-28T10:01:02.000Z", "u1"), result("2026-10-28T10:01:03.000Z", "u2"),
             result("2026-10-28T10:01:03.000Z", "u3")], "a")
check(server.mcp_state("s1") is None, "nothing to show once every call has its result", server.mcp_state("s1"))
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

# ---- Codex: only calls (exec --json writes no connection events)
log = os.path.join(TMP, "codex.jsonl")
item = {"id": "item_3", "type": "mcp_tool_call", "server": "codebase-memory-mcp", "tool": "search_graph"}
write(log, [{"type": "thread.started", "thread_id": "th1"}, {"type": "turn.started"},
            {"type": "item.started", "item": item}])
st = codex.mcp_state(log)
check(st and [(c["server"], c["tool"]) for c in st["calls"]] == [("codebase-memory-mcp", "search_graph")],
      "Codex: a started MCP call shows", st)
since = st and st["calls"][0]["since"]
check(codex.mcp_state(log)["calls"][0]["since"] == since, "Codex: the call keeps the time the board first saw it")
write(log, [{"type": "item.completed", "item": {**item, "status": "completed"}}], "a")
check(codex.mcp_state(log) is None, "Codex: a completed call is gone")
write(log, [{"type": "turn.completed"}, {"type": "turn.started"}], "a")
check(codex.mcp_state(log) is None, "Codex: an older turn's calls never show")

# ---- the watcher puts it on the card record
write(path, [use("2026-10-28T10:02:00.000Z", "u9", "mcp__muninn__muninn_remember")], "a")
proj = os.path.join(TMP, "proj")
os.makedirs(proj)
p = server.Project(proj)
os.makedirs(p.board)
with open(p.data, "w", encoding="utf-8") as f:
    json.dump({"columns": [{"id": "todo", "name": "To do"}, {"id": "done", "name": "Done", "done": True}],
               "tasks": [{"id": "t1", "title": "Task", "column": "todo", "agent": {"id": "a1", "started": ""}}]}, f)
reports.append(p.board, "t1", "progress", "working on it", "claude")
server.projects = lambda: [p]
server.list_sessions = lambda cwd: [{"id": "a1", "sessionId": "s1", "state": "working", "status": "busy"}]
server.run_claude = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))
server.WATCH.poll()
rec = server.WATCH.snapshot(p.id).get("t1") or {}
check(rec.get("phase") == "working" and [c["tool"] for c in (rec.get("mcp") or {}).get("calls", [])]
      == ["muninn_remember"], "the card record carries the open MCP call", {k: rec.get(k) for k in ("phase", "mcp")})

print("ALL OK" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
