"""GET /api/mcp: the MCP servers a session in a project gets, each with its state and its number of tools.

The board asks Claude Code itself: a short-lived `claude -p` in stream-json mode answers the control request
`mcp_status`. `claude mcp list` was not enough: it says "Connected" for context-mode on a Node too old for it, when
only its health check loads (measured 2026-11-20: 1 tool on Node 24.12.0, 9 on 24.22.3).

Run: python3 tests/test_mcp_menu.py  (stdlib only; a fake claude that speaks that protocol, a fresh temp HOME)."""
import json
import os
import stat
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.realpath(tempfile.mkdtemp(prefix="mcp-menu-test-"))
HOME = os.path.join(TMP, "home")
os.environ["HOME"] = HOME   # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(HOME)
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

FAILS = []


def check(ok, what, detail=""):
    print(("ok   " if ok else "FAIL ") + what + ("" if ok or not detail else f": {detail}"))
    if not ok:
        FAILS.append(what)


# A fake claude: it logs how it was started, then answers control requests from the scenario file until its stdin
# closes. Modes: ok (the n-th mcp_status gets the n-th answer, the last one from then on), die, refuse, silent.
SCENARIO, LOG = os.path.join(TMP, "scenario.json"), os.path.join(TMP, "claude.log")
FAKE = os.path.join(TMP, "claude")
with open(FAKE, "w") as f:
    f.write(f"""#!{sys.executable}
import json, os, sys
with open({LOG!r}, "a") as f:
    f.write(json.dumps({{"argv": sys.argv[1:], "cwd": os.getcwd()}}) + "\\n")
scn = json.load(open({SCENARIO!r}))
if scn["mode"] == "die":
    sys.exit(3)
n = 0
for line in sys.stdin:
    d = json.loads(line)
    rid, kind = d["request_id"], d["request"]["subtype"]
    if kind == "initialize":
        reply = {{"subtype": "success", "request_id": rid, "response": {{}}}}
    elif scn["mode"] == "silent":
        continue
    elif scn["mode"] == "refuse":
        reply = {{"subtype": "error", "request_id": rid, "error": "Unsupported control request subtype: mcp_status"}}
    else:
        answers = scn["answers"]
        reply = {{"subtype": "success", "request_id": rid, "response": {{"mcpServers": answers[min(n, len(answers) - 1)]}}}}
        n += 1
        print(json.dumps({{"type": "system", "subtype": "commands_changed"}}), flush=True)   # stream noise to skip
        print("not json at all", flush=True)
    print(json.dumps({{"type": "control_response", "response": reply}}), flush=True)
with open({LOG!r}, "a") as f:
    f.write(json.dumps({{"closed": True}}) + "\\n")
""")
os.chmod(FAKE, os.stat(FAKE).st_mode | stat.S_IXUSR)
server.CLAUDE_CMD = [FAKE]


def scenario(mode="ok", answers=None):
    with open(SCENARIO, "w") as f:
        json.dump({"mode": mode, "answers": answers or []}, f)
    open(LOG, "w").close()


def log():
    return [json.loads(x) for x in open(LOG).read().splitlines()]


def launches():
    return [x for x in log() if "argv" in x]


def srv(name, status, tools=(), **more):
    return {"name": name, "status": status, "tools": [{"name": t} for t in tools], "config": {"command": "secret-cmd",
            "env": {"KEY": "sk-secret"}}, **more}


def watch(cwd=TMP, **kw):
    seen = []
    t0 = time.time()
    err = server.mcp_watch(cwd, seen.append, **kw)
    return err, seen, time.time() - t0


# --- one row
row = server.mcp_row(srv("plugin:context-mode:context-mode", "connected", ["ctx_doctor"], source="plugin", scope="dynamic"))
check(row == {"name": "plugin:context-mode:context-mode", "label": "context-mode", "status": "limited", "tools": 1,
              "error": "", "from": "plugin"}, "context-mode with only its health check is 'limited', not connected", row)
row = server.mcp_row(srv("plugin:context-mode:context-mode", "connected", ["ctx_doctor", "ctx_execute", "ctx_search"]))
check(row["status"] == "connected" and row["tools"] == 3, "context-mode with its real tools is connected")
row = server.mcp_row(srv("other", "connected", ["ctx_doctor"]))
check(row["status"] == "connected", "another server with one tool named like that stays connected")
row = server.mcp_row({"name": "claude.ai Gmail", "status": "needs-auth", "scope": "claudeai"})
check(row == {"name": "claude.ai Gmail", "label": "claude.ai Gmail", "status": "needs-auth", "tools": 0, "error": "",
              "from": "claudeai"}, "a server with no tools and no source still makes a row", row)
row = server.mcp_row(srv("x", "failed", error="E" * 900))
check(len(row["error"]) == 300, "a long error is cut")
check("secret" not in json.dumps(server.mcp_row(srv("x", "connected", ["a"]))), "command and env never reach the page")
check(server.mcp_row({})["status"] == "unknown", "an entry with no status is 'unknown'")
order = [r["label"] for r in server.mcp_rows([srv("Zed", "connected"), srv("off", "disabled"), srv("new", "weird"),
                                              srv("auth", "needs-auth"), srv("slow", "pending"), srv("bad", "failed"),
                                              srv("alpha", "connected"), "junk",
                                              srv("plugin:context-mode:context-mode", "connected", ["ctx_doctor"])])]
check(order == ["bad", "context-mode", "auth", "slow", "alpha", "Zed", "off", "new"],
      "rows: what does not work first, then sign-in, starting, working (by name), off, unknown", order)

# --- one check against the fake claude
scenario(answers=[[srv("a", "pending"), srv("b", "connected", ["t1"])],
                  [srv("a", "connected", ["t1", "t2"]), srv("b", "connected", ["t1"])]])
err, seen, took = watch(settle=0.1, every=0.05, wait=10)
check(err == "" and len(seen) == 2, "the list is handed over each time it changes", (err, seen))
check([(r["label"], r["status"], r["tools"]) for r in seen[-1]] == [("a", "connected", 2), ("b", "connected", 1)]
      and seen[0][0]["status"] == "pending", "first the starting server, then both connected with their tools")
check(took < 5, "it stops as soon as nothing is starting any more", took)
first = launches()[0]
settings = json.loads(first["argv"][first["argv"].index("--settings") + 1])
check(first["argv"][0] == "-p" and "--no-session-persistence" in first["argv"] and settings == {"disableAllHooks": True}
      and first["argv"].count("stream-json") == 2 and first["cwd"] == TMP,
      "claude runs in the folder, in stream mode, saves no session and runs no hooks", first)
check(log()[-1] == {"closed": True}, "claude is ended by closing its input, so it stops its servers itself")

scenario(answers=[[srv("b", "connected")]])
err, seen, took = watch(settle=0.6, every=0.05, wait=10)
check(err == "" and len(seen) == 1 and took >= 0.6, "a list with nothing starting still waits out the settle time", took)

scenario(answers=[[srv("a", "connected")], [srv("a", "connected")], [srv("a", "connected"), srv("late", "connected")]])
err, seen, took = watch(settle=0.5, every=0.05, wait=10)
check(err == "" and [r["label"] for r in seen[-1]] == ["a", "late"], "a server that joins late is in the final list", seen)

scenario(answers=[[srv("a", "pending")]])
err, seen, took = watch(settle=0.1, every=0.05, wait=0.6)
check(err == "" and seen[-1][0]["status"] == "pending" and took < 5, "a server that never comes up ends the check at the time limit", took)
check(log()[-1] == {"closed": True}, "and claude is still ended")

scenario("die")
err, seen, took = watch(settle=0.1, every=0.05, wait=5)
check(err == "Claude Code did not answer." and not seen and took < 4, "claude ending at once: an error, no hang", (err, took))
scenario("refuse")
err, seen, took = watch(settle=0.1, every=0.05, wait=5)
check(err == "This Claude Code cannot list its MCP servers. Update it." and not seen, "an older claude that refuses the request", err)
scenario("silent")
err, seen, took = watch(settle=0.1, every=0.05, wait=0.5)
check(err == "Claude Code did not answer." and took < 5, "a claude that never answers ends at the time limit", (err, took))
server.CLAUDE_CMD = [os.path.join(TMP, "no-such-claude")]
err, seen, took = watch(settle=0.1, every=0.05, wait=5)
check(err == "Could not find Claude Code." and not seen, "no claude at all", err)
server.CLAUDE_CMD = [FAKE]

# --- folder trust: read from .claude.json, a folder above counts
proj = os.path.join(TMP, "work", "proj")
os.makedirs(os.path.join(proj, "inner"))
check(server.folder_trusted(proj) is False, "no .claude.json: not trusted")
with open(os.path.join(HOME, ".claude.json"), "w") as f:
    json.dump({"projects": {proj: {"hasTrustDialogAccepted": True}, "/elsewhere": {"hasTrustDialogAccepted": False}, "/odd": 3}}, f)
check(server.folder_trusted(proj) and server.folder_trusted(os.path.join(proj, "inner")), "the folder and a folder inside it are trusted")
check(not server.folder_trusted(os.path.join(TMP, "work")) and not server.folder_trusted("/elsewhere"), "the folder above and a refused one are not")

# --- the cache behind GET /api/mcp
server.MCP_SETTLE, server.MCP_WAIT = 0.1, 10


def finished(path, force=False):
    st = server.MCP_CHECK.status(path, force)
    first = dict(st)
    for _ in range(200):
        if not st["checking"]:
            break
        time.sleep(0.05)
        st = server.MCP_CHECK.status(path)
    return first, st


scenario(answers=[[srv("ok", "connected", ["t"]), srv("bad", "failed", error="ENOENT: no such file")]])
first, st = finished(proj)
check(first["checking"] is True and first["servers"] == [] and first["at"] == 0, "the first answer comes at once: checking, nothing known yet", first)
check(st["checking"] is False and st["error"] == "" and st["folder"] is True and st["at"] > 0
      and [(r["label"], r["status"]) for r in st["servers"]] == [("bad", "failed"), ("ok", "connected")], "then the list, the broken one first", st)
check(len(launches()) == 1 and launches()[0]["cwd"] == os.path.realpath(proj), "a trusted folder is checked from inside it", launches())
server.MCP_CHECK.status(proj)
time.sleep(0.2)
check(len(launches()) == 1, "asking again within a minute starts no new check")
scenario(answers=[[srv("ok", "connected", ["t"])]])
first, st = finished(proj, force=True)
check(first["checking"] is True and [r["label"] for r in first["servers"]] == ["bad", "ok"], "Check again: the old list stays up while the new check runs", first)
check([r["label"] for r in st["servers"]] == ["ok"] and len(launches()) == 1, "and the new list replaces it")
scenario("die")
first, st = finished(proj, force=True)
check(st["error"] == "Claude Code did not answer." and [r["label"] for r in st["servers"]] == ["ok"] and not st["checking"],
      "a failed check says so and keeps the last list", st)

other = os.path.join(TMP, "untrusted")
os.makedirs(other)
scenario(answers=[[srv("mine", "connected")]])
first, st = finished(other)
check(st["folder"] is False and os.path.realpath(launches()[0]["cwd"]) == os.path.realpath(tempfile.gettempdir()),
      "a folder Claude does not trust yet is checked from the temp folder, and the answer says so", (st, launches()))

# --- the route
pr = server.open_project(proj)
web = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
threading.Thread(target=web.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{web.server_address[1]}"
scenario(answers=[[srv("plugin:context-mode:context-mode", "connected", ["ctx_doctor"], source="plugin")]])
with urllib.request.urlopen(f"{base}/api/mcp?p={pr.id}&force=1") as r:
    body = json.loads(r.read())
check(r.status == 200 and sorted(body) == ["at", "checking", "error", "folder", "servers"] and body["checking"] is True,
      "GET /api/mcp?force=1 starts a check and answers at once", body)
for _ in range(200):
    with urllib.request.urlopen(f"{base}/api/mcp?p={pr.id}") as r:
        body = json.loads(r.read())
    if not body["checking"]:
        break
    time.sleep(0.05)
check(body["servers"] == [{"name": "plugin:context-mode:context-mode", "label": "context-mode", "status": "limited",
                           "tools": 1, "error": "", "from": "plugin"}], "GET /api/mcp then gives the rows", body)
web.shutdown()

print("\n%d failed" % len(FAILS) if FAILS else "\nok")
sys.exit(1 if FAILS else 0)
