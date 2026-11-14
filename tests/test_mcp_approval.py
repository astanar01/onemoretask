"""The MCP server approval a background session shows before it starts (new servers in the project's .mcp.json): the
board reads it from the session record, and the card's Use / Always / Don't use buttons answer it through attach.

Screens in tests/fixtures/attach/mcp_* are real `claude attach` captures (CLI 2.1.325) of probe sessions; the same
probes, answered live with attach.answer_mcp, saved what Claude's own menu saves (enabledMcpjsonServers,
enableAllProjectMcpServers, disabledMcpjsonServers in .claude/settings.local.json).

Run: python3 tests/test_mcp_approval.py  (stdlib only; no terminal, no Claude session)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import attach  # noqa: E402
import server  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "attach")
fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print(("ok  " if ok else "FAIL") + " " + name + ("" if ok else f": {detail}"))


def raw(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return f.read()


def run(reads, choice, names, count=0):
    """attach.answer_mcp with the terminal faked: each read returns the next captured screen (a fixture name, or bytes
    made here); keys are recorded."""
    reads, keys = [r if isinstance(r, bytes) else raw(r) for r in reads], []

    @attach.contextlib.contextmanager
    def fake_terminal(session_id, cmd):
        yield 99

    saved = attach._terminal, attach._read, attach.os.write
    attach._terminal, attach._read = fake_terminal, lambda fd, seconds: reads.pop(0) if reads else b""
    attach.os.write = lambda fd, data: keys.append(data)
    try:
        return attach.answer_mcp("x", choice, names, count=count), keys
    finally:
        attach._terminal, attach._read, attach.os.write = saved


ONE, MANY = ["probe-a"], ["probe-a", "probe-b"]
one, many = attach.screen_text(raw("mcp_one_before.raw")), attach.screen_text(raw("mcp_many_before.raw"))
check("mcp_dialog tells the two menus apart", (attach.mcp_dialog(one), attach.mcp_dialog(many)) == ("one", "many"))
check("mcp_dialog is empty once the session runs",
      not attach.mcp_dialog(attach.screen_text(raw("mcp_one_after.raw"))) and not attach.mcp_dialog(attach.screen_text(raw("mcp_many_after.raw"))))
check("focus starts on Continue without (one) and the first box (many)",
      (attach.focused(one), attach.focused(many)) == (attach.SKIP, "probe-a"), (attach.focused(one), attach.focused(many)))
redraws = [attach.focused(attach.screen_text(raw(n))) for n in
           ("mcp_one_up1.raw", "mcp_one_up2.raw", "mcp_many_down1.raw", "mcp_many_down2.raw")]
check("focus is read from a partial redraw", redraws == [attach.USE_ALL, attach.USE, "probe-b", attach.ENABLE], redraws)
# A card note is never typed into either menu (Enter would answer it).
check("type_into sees both menus as a prompt", bool(attach.DIALOG.search(one)) and bool(attach.DIALOG.search(many)))

got, keys = run(["mcp_one_before.raw", "mcp_one_up1.raw", "mcp_one_up2.raw", "mcp_one_after.raw"], "allow", ONE)
check("one server, Use it: up, up, Enter", got == (True, "") and keys == [attach.UP, attach.UP, b"\r"], (got, keys))
got, keys = run(["mcp_one_before.raw", "mcp_one_up1.raw", "mcp_one_after.raw"], "always", ONE)
check("one server, Always: up, Enter", got == (True, "") and keys == [attach.UP, b"\r"], (got, keys))
got, keys = run(["mcp_one_before.raw", "mcp_one_after.raw"], "deny", ONE)
check("one server, Don't use: Esc", got == (True, "") and keys == [attach.ESC], (got, keys))
got, keys = run(["mcp_many_before.raw", "mcp_many_down1.raw", "mcp_many_down2.raw", "mcp_many_after.raw"], "allow", MANY)
check("two servers, Use them: down, down, Enter", got == (True, "") and keys == [attach.DOWN, attach.DOWN, b"\r"], (got, keys))
got, keys = run(["mcp_many_before.raw", "mcp_many_down1.raw", "mcp_many_down2.raw", "mcp_many_after.raw"], "always", MANY)
check("two servers, Always: same keys, and the caller saves 'all later'", got == (True, "all-later")
      and keys == [attach.DOWN, attach.DOWN, b"\r"], (got, keys))
got, keys = run(["mcp_many_before.raw", "mcp_many_after.raw"], "deny", MANY)
check("two servers, Don't use: Esc (reject all)", got == (True, "") and keys == [attach.ESC], (got, keys))
got, keys = run(["mcp_one_before.raw"], "allow", ["other-server"])
check("another server on screen: no key", got[0] is False and keys == [], (got, keys))
got, keys = run(["mcp_one_after.raw"], "allow", ONE)
check("no MCP menu on screen: no key", got[0] is False and keys == [], (got, keys))
got, keys = run(["mcp_one_before.raw", "mcp_one_before.raw"], "deny", ONE)
check("menu still there after the key: not answered", got == (False, "the question is still on screen after the key"), got)

# Review finding 2: a name that only starts like the card's is another server.
admin_one = raw("mcp_one_before.raw").replace(b"probe-a", b"probe-admin")
got, keys = run([admin_one], "allow", ONE)
check("one server: probe-admin on screen, card says probe-a: no key", got[0] is False and keys == [], (got, keys))
admin_many = raw("mcp_many_before.raw").replace(b"probe-a", b"probe-admin")
got, keys = run([admin_many], "allow", MANY)
check("two servers: probe-admin on screen, card says probe-a: no key", got[0] is False and keys == [], (got, keys))
check("mcp_listed reads whole names and the count", attach.mcp_listed(many) == (["probe-a", "probe-b"], 2)
      and attach.mcp_listed(one) == (["probe-a"], 1), (attach.mcp_listed(many), attach.mcp_listed(one)))
got, keys = run(["mcp_many_before.raw"], "allow", MANY, count=3)
check("the card counted 3 servers, the screen says 2: no key", got[0] is False and keys == [], (got, keys))

# Review finding 1: 30 servers, the card's list cut to the first 13 names; every box must be walked past.
N = 30
servers = [f"long-server-name-{i:02d}" for i in range(N)]
big = ("─" * 60 + f"\r\n   {N} new MCP servers found in this project  \r\n  Select any you wish to enable. \r\n\r\n"
       + "".join(("  ❯ " if i == 0 else "    ") + f" [✔]  {s} \r\n" for i, s in enumerate(servers))
       + "     Enable selected \r\n   Space to select · Esc to reject all  \r\n").encode()
steps = [f"      {servers[i]}   ❯ {servers[i + 1]} ".encode() for i in range(N - 1)] + [f"      {servers[-1]}   ❯  Enable selected".encode()]
got, keys = run([big, *steps, raw("mcp_many_after.raw")], "always", servers[:13], count=N)
check("30 servers, 13 names on the card: walks all 30 boxes, then Enter", got == (True, "all-later")
      and keys == [attach.DOWN] * N + [b"\r"], (got, len(keys)))

# ---- server: reading the ask from the session record
needs = {}
server.job_detail = lambda job_id, field="detail": needs.get(job_id, "") if field == "needs" else ""
needs.update({"s1": "approve 1 new project MCP server (laravel-boost) — attach to respond",
              "s2": "approve 2 new project MCP servers (probe-a, probe-b) — attach to respond",
              "s3": "approve 3 new project MCP servers (a, b, c-cut-he…",
              "s4": "approve Bash: rm -rf build"})
blocked = lambda sid: {"id": sid, "state": "blocked", "status": None, "waitingFor": None}
check("one server", server.mcp_names(blocked("s1")) == ["laravel-boost"])
check("two servers", server.mcp_names(blocked("s2")) == ["probe-a", "probe-b"])
check("cut needs keeps the whole names and the full count", server.mcp_request(blocked("s3")) == (["a", "b"], 3),
      server.mcp_request(blocked("s3")))
check("cut needs: the card says how many more", server.permission_ask(blocked("s3")) == server.MCP_ASK + "a, b and 1 more",
      server.permission_ask(blocked("s3")))
check("a tool permission is not an MCP ask", server.mcp_names(blocked("s4")) == [])
check("not blocked: no ask", server.mcp_names({"id": "s1", "state": "working"}) == [])
check("permission_ask names the servers", server.permission_ask(blocked("s2")) == server.MCP_ASK + "probe-a, probe-b")
check("phase_of shows it as needs_you with that ask",
      server.phase_of(blocked("s1"), [{"from": "you", "status": "launch", "message": "go", "at": "x"}], 0)
      == ("needs_you", server.MCP_ASK + "laravel-boost"))
check("the page keeps the same prefix", server.MCP_ASK in open(os.path.join(os.path.dirname(HERE), "index.html"), encoding="utf-8").read())

# ---- server: Always on the several-servers menu saves enableAllProjectMcpServers at the git top level
tmp = tempfile.mkdtemp(prefix="mcp-approval-test-")
subprocess.run(["git", "init", "-q", tmp], check=True)
os.makedirs(os.path.join(tmp, ".claude"))
os.makedirs(os.path.join(tmp, "sub"))
with open(os.path.join(tmp, ".claude", "settings.local.json"), "w") as f:
    json.dump({"permissions": {"allow": ["Bash(ls)"]}, "enabledMcpjsonServers": ["x"]}, f)
server.allow_all_mcp(os.path.join(tmp, "sub"))
saved = json.load(open(os.path.join(tmp, ".claude", "settings.local.json")))
check("allow_all_mcp adds the flag and keeps the rest", saved == {"permissions": {"allow": ["Bash(ls)"]},
                                                              "enabledMcpjsonServers": ["x"], "enableAllProjectMcpServers": True}, saved)
check("allow_all_mcp writes no file in the subfolder", not os.path.exists(os.path.join(tmp, "sub", ".claude")))
fresh = tempfile.mkdtemp(prefix="mcp-approval-nogit-")
server.allow_all_mcp(fresh)
check("allow_all_mcp makes .claude/settings.local.json when missing",
      json.load(open(os.path.join(fresh, ".claude", "settings.local.json"))) == {"enableAllProjectMcpServers": True})

# ---- server: answer_permission sends an MCP ask to answer_mcp, never to the tool-permission menu
class P:
    id, path = "demo", tmp


calls = []
server.WATCH.snapshot = lambda pid: {"t1": {"id": "s2"}}
server.all_sessions = lambda p: [blocked("s2")]
attach.answer_mcp = lambda sid, choice, names, cmd=None, count=0: calls.append(("mcp", sid, choice, names, count)) or (True, "all-later")
attach.answer_prompt = lambda *a, **k: calls.append(("prompt",)) or (True, "")
os.remove(os.path.join(tmp, ".claude", "settings.local.json"))
res = server.answer_permission(P, "t1", "always", server.MCP_ASK + "probe-a, probe-b")
check("answer_permission routes to answer_mcp with the names", calls == [("mcp", "s2", "always", ["probe-a", "probe-b"], 2)], calls)
check("answer_permission reports an MCP answer", res == {"next": "", "again": False, "once": False, "mcp": True}, res)
check("'all-later' is saved for the project",
      json.load(open(os.path.join(tmp, ".claude", "settings.local.json"))).get("enableAllProjectMcpServers") is True)
try:
    server.answer_permission(P, "t1", "allow", server.MCP_ASK + "something-else")
    check("a stale card ask is refused", False, "no error")
except LookupError:
    check("a stale card ask is refused", True)

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
