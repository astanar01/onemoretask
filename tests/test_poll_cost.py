"""The watcher's cost when little happens: an idle board polls slowly, /api/agents sends only changed cards, and
token totals of unchanged transcripts survive a restart.

Run: python3 tests/test_poll_cost.py  (stdlib only; a fresh temp HOME, every session call faked)."""
import http.server
import json
import os
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="poll-cost-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

server.PROJECTS_DIR = os.path.join(os.environ["HOME"], ".claude", "projects")
tdir = os.path.join(server.PROJECTS_DIR, "-fake-project")
os.makedirs(tdir)


def reply(mid, n):
    return {"type": "assistant", "timestamp": "2026-10-20T10:00:00.000Z",
            "message": {"id": mid, "model": "claude-opus-5-5", "usage": {"input_tokens": n, "output_tokens": 1}}}


def write(path, entries, mode="w"):
    with open(path, mode, encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in entries)


# ---- token totals: counted once, then read back from the saved file while the transcript is unchanged
main = os.path.join(tdir, "s1.jsonl")
write(main, [reply("m1", 100), reply("m2", 20)])
os.makedirs(os.path.join(tdir, "s1", "subagents"))
write(os.path.join(tdir, "s1", "subagents", "agent-x.jsonl"), [reply("m3", 5)])
first = server.tokens([main])
assert first["main"][0] == 120 and first["subagents"][0] == 5 and first["count"] == 1, first
server.save_tokens()
assert os.path.exists(server.TOKENS_FILE), "token totals were not saved"

real_usage = server._usage


def no_read(path):
    raise AssertionError("re-read a transcript whose totals were saved: " + path)


server._tokens_saved.clear()  # a restart: nothing in memory, the saved file only
server._usage_memo.clear()
server._usage = no_read
assert server.tokens([main]) == first, "saved totals differ"
server._usage = real_usage
write(main, [reply("m4", 7)], "a")
assert server.tokens([main])["main"][0] == 127, "a grown transcript must be counted again"
write(os.path.join(tdir, "s1", "subagents", "agent-y.jsonl"), [reply("m5", 3)])
again = server.tokens([main])
assert again["subagents"][0] == 8 and again["count"] == 2, "a new subagent must be counted"
print("ok  token totals: saved, read back after a restart without re-reading, recounted when a transcript grows")

# ---- the real Watcher.poll() with every session call faked
proj = os.path.join(TMP, "proj")
os.makedirs(proj)
p = server.Project(proj)
os.makedirs(p.board)
tasks = {"columns": [{"id": "todo", "name": "To do"}, {"id": "done", "name": "Done", "done": True}],
         "tasks": [{"id": f"t{i}", "title": f"Task {i}", "column": "todo", "agent": {"id": f"a{i}", "started": ""}}
                   for i in range(3)]}
with open(p.data, "w", encoding="utf-8") as f:
    json.dump(tasks, f)
for i in range(3):
    reports.append(p.board, f"t{i}", "done", f"finished {i}", "claude")
sessions = {}
server.projects = lambda: [p]
server.list_sessions = lambda cwd: [dict(s) for s in sessions.values()]
server.run_claude = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no real claude in this test"))
W = server.WATCH
W.poll()
W.poll()
assert "working" not in {v["phase"] for v in W.snapshot(p.id).values()}, W.snapshot(p.id)
assert W.idle, "no card works: the watcher should slow down"
W.expect(p, "t1", "a1")
assert W.wake.is_set(), "a launch must wake the watcher"
W.wake.clear()
sessions["a1"] = {"id": "a1", "sessionId": None, "state": "working", "status": "busy"}
W.poll()
assert not W.idle, "a working card must keep the fast pace"
del sessions["a1"]
W.launched.clear()
W.poll()
assert W.idle, "back to idle once the card stops"
print("ok  idle: slow while nothing works, woken by a launch, fast while a card works")

# ---- /api/agents?since=: only the cards that changed
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}/api/agents?p={p.id}"


def get(url):
    with urllib.request.urlopen(url) as r:
        return json.loads(r.read()), r.headers.get("X-Agents-Rev")


flat, none = get(base)
assert none is None and set(flat) == {"t0", "t1", "t2"}, "no since: the old full answer"
body, tok = get(base + "&since=")
assert tok and set(body["cards"]) == {"t0", "t1", "t2"} and sorted(body["ids"]) == ["t0", "t1", "t2"], body
W.poll()
body, tok2 = get(base + "&since=" + tok)
assert body["cards"] == {} and sorted(body["ids"]) == ["t0", "t1", "t2"] and tok2 == tok, ("nothing changed", body)
reports.append(p.board, "t2", "progress", "one more line", "claude")
W.poll()
body, tok3 = get(base + "&since=" + tok2)
assert set(body["cards"]) == {"t2"} and body["cards"]["t2"]["log"][-1]["message"] == "one more line", body
assert tok3 != tok2
body, _ = get(base + "&since=oldboot." + tok3.split(".")[1])
assert set(body["cards"]) == {"t0", "t1", "t2"}, "a token from before a restart must get every card"
srv.shutdown()
print("ok  /api/agents?since=: every card first, nothing when nothing changed, one card when one changed")
print("PASS")
