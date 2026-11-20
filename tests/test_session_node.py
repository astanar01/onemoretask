"""The board gives its sessions a Node new enough for context-mode.

Why: a terminal keeps the Node it opened with, the login shell the board asks keeps it too, and a background session
takes its environment from Claude Code's background runner, not from the board. A board started from a terminal
opened before a Node upgrade gave every card Node 24.12.0 for five days: context-mode offered only ctx_doctor.

Run: python3 tests/test_session_node.py  (stdlib only; fake node scripts in a temp folder, no Claude session)."""
import importlib.util
import json
import os
import stat
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="session-node-test-"))
os.environ["HOME"] = os.path.join(TMP, "home")   # before importing server: it reads ~ at import
os.makedirs(os.environ["HOME"])
for k in ("ONEMORETASK_KEEP_PATH", "NVM_DIR", "CLAUDE_CONFIG_DIR"):
    os.environ.pop(k, None)
sys.path.insert(0, ROOT)
import server  # noqa: E402
import shell_env  # noqa: E402

FAILS = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


def fake_node(folder, body):
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, "node")
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body + "\n")
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return folder


OLD = fake_node(os.path.join(TMP, "old"), "echo v24.12.0")
NEW = fake_node(os.path.join(TMP, "new"), "echo v25.0.1")
EDGE = fake_node(os.path.join(TMP, "edge"), "echo v24.15.0")
DEAD = fake_node(os.path.join(TMP, "dead"), "exit 3")
ODD = fake_node(os.path.join(TMP, "odd"), "echo not a version")
EMPTY = os.path.join(TMP, "empty")
os.makedirs(EMPTY)

NVM = os.path.join(TMP, "nvm")
ROOT_NODE = os.path.join(NVM, "versions", "node")
for name, body in (("v24.12.0", "echo v24.12.0"), ("v24.9.9", "echo v24.9.9"), ("v24.22.3", "echo v24.22.3")):
    fake_node(os.path.join(ROOT_NODE, name, "bin"), body)
os.makedirs(os.path.join(ROOT_NODE, "v25.1.0", "bin"))   # no node binary in it: does not count
os.makedirs(os.path.join(ROOT_NODE, "system"))           # not a version
BEST = os.path.join(ROOT_NODE, "v24.22.3", "bin")

# --- the two files agree on the Node context-mode needs
spec = importlib.util.spec_from_file_location("install_tools", os.path.join(ROOT, "setup", "install_tools.py"))
it = importlib.util.module_from_spec(spec)
spec.loader.exec_module(it)
check(shell_env.NODE_MIN == it.NODE_MIN, "shell_env and the installer name the same oldest Node")

# --- node_version / node_ok
check(shell_env.node_version(os.path.join(OLD, "node")) == (24, 12, 0), "node_version reads what node prints")
check(shell_env.node_version(os.path.join(DEAD, "node")) is None, "a node that fails has no version")
check(shell_env.node_version(os.path.join(ODD, "node")) is None, "a node that prints no version has none")
check(shell_env.node_version(os.path.join(EMPTY, "node")) is None, "a missing node has no version")
check(shell_env.node_ok(f"{NEW}:{OLD}") is True, "node_ok: a new Node first is fine")
check(shell_env.node_ok(f"{EDGE}:{OLD}") is True, "node_ok: exactly the oldest allowed Node is fine")
check(shell_env.node_ok(f"{OLD}:{NEW}") is False, "node_ok: the FIRST node counts, a newer one later does not")
check(shell_env.node_ok(f"{EMPTY}:/nowhere") is False and shell_env.node_ok("") is False, "node_ok: no node, not ok")

# --- nvm_node_dir
check(shell_env.nvm_node_dir({"NVM_DIR": NVM}) == BEST,
      "nvm_node_dir takes the highest version by number (24.22.3 over 24.9.9), and only folders with a node")
check(shell_env.nvm_node_dir({"NVM_DIR": os.path.join(TMP, "no-nvm")}) == "", "no nvm folder, no Node from it")
check(shell_env.nvm_node_dir({}) == "", "nvm_node_dir falls back to ~/.nvm (none in this HOME)")

# --- fix_node
env = {"PATH": f"{OLD}:/usr/bin:/bin", "NVM_DIR": NVM}
check(shell_env.fix_node(env) == BEST and env["PATH"] == f"{BEST}:{OLD}:/usr/bin:/bin",
      "fix_node puts the newer nvm Node first and keeps the rest of PATH")
check(shell_env.fix_node(env) == "" and env["PATH"].split(":")[0] == BEST, "fix_node a second time changes nothing")
env = {"PATH": "/usr/bin:/bin", "NVM_DIR": NVM}
check(shell_env.fix_node(env) == BEST and env["PATH"].split(":")[0] == BEST, "fix_node also helps when PATH has no node")
env = {"PATH": f"{OLD}:{BEST}:/bin", "NVM_DIR": NVM}
check(shell_env.fix_node(env) == BEST and env["PATH"] == f"{BEST}:{OLD}:/bin", "a newer Node later on PATH moves to the front, once")
env = {"PATH": f"{NEW}:{OLD}", "NVM_DIR": NVM}
check(shell_env.fix_node(env) == "" and env["PATH"] == f"{NEW}:{OLD}", "a PATH whose Node is new enough stays as it is")
env = {"PATH": f"{OLD}:/bin", "NVM_DIR": os.path.join(TMP, "no-nvm")}
check(shell_env.fix_node(env) == "" and env["PATH"] == f"{OLD}:/bin", "no newer Node installed: PATH stays as it is")
env = {"PATH": f"{OLD}:/bin", "NVM_DIR": NVM, "ONEMORETASK_KEEP_PATH": "1"}
check(shell_env.fix_node(env) == "" and env["PATH"] == f"{OLD}:/bin", "ONEMORETASK_KEEP_PATH keeps a Node pinned on purpose")
BROKEN = os.path.join(TMP, "nvm-broken")
fake_node(os.path.join(BROKEN, "versions", "node", "v26.0.0", "bin"), "exit 1")
env = {"PATH": f"{OLD}:/bin", "NVM_DIR": BROKEN}
check(shell_env.fix_node(env) == "" and env["PATH"] == f"{OLD}:/bin", "a newer Node that does not run is not used")

# --- the case that broke: the login shell starts from the stale terminal and keeps its Node (as nvm does)
STALE = os.path.join(TMP, "staleshell")
with open(STALE, "w") as f:
    f.write(f'#!/bin/sh\nprintf "\\n{shell_env.MARK}%s{shell_env.MARK}\\n" "$PATH"\n')
os.chmod(STALE, os.stat(STALE).st_mode | stat.S_IXUSR)
saved = os.environ["PATH"]
os.environ["PATH"] = f"{OLD}:/usr/bin:/bin"       # login_path's shell inherits this
try:
    env = {"PATH": f"{OLD}:/usr/bin:/bin", "NVM_DIR": NVM}
    shell_env.fix_path(env, STALE)
    check(env["PATH"].split(":")[0] == OLD, "the login shell alone leaves the stale Node first")
    shell_env.fix_node(env)
    check(env["PATH"].split(":")[0] == BEST and shell_env.node_ok(env["PATH"]), "fix_node after it gives a Node that works")
finally:
    os.environ["PATH"] = saved

# --- server.session_path: what the board hands to its sessions
good = f"{NEW}:/usr/bin:/bin"
check(server.session_path({"PATH": good}) == good, "session_path: the board's PATH when its Node is new enough")
check(server.session_path({"PATH": f"{OLD}:/usr/bin"}) == "", "session_path: nothing to hand over with an old Node")
check(server.session_path({"PATH": good, "ONEMORETASK_KEEP_PATH": "1"}) == "", "session_path: ONEMORETASK_KEEP_PATH hands nothing over")
server.WINDOWS = True
check(server.session_path({"PATH": good}) == "", "session_path: nothing on Windows")
server.WINDOWS = False

# --- inbox_settings carries it in env, next to the subagent pin
p = types.SimpleNamespace(board=TMP, path=TMP, id="p")
card = {"id": "x", "title": "card", "agent": {"model": "fable", "effort": ""}}
server.load_tasks = lambda p: {"tasks": [card]}
check(server.SESSION_PATH == "", "no PATH is handed over before main() has looked")
check("env" not in json.loads(server.inbox_settings(p, "x")), "inbox_settings: no env without a PATH or a pin")
server.SESSION_PATH = good
check(json.loads(server.inbox_settings(p, "x")).get("env") == {"PATH": good}, "inbox_settings: the session gets the board's PATH")
both = json.loads(server.inbox_settings(p, "x", "opus")).get("env")
check(both == {"PATH": good, "CLAUDE_CODE_SUBAGENT_MODEL": "opus", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"},
      "inbox_settings: PATH and the subagent pin together")
server.SESSION_PATH = ""

print("\n%d failed" % len(FAILS) if FAILS else "\nok")
sys.exit(1 if FAILS else 0)
