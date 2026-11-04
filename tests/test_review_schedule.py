"""The board installs the scheduled skill review with the login shell's PATH, and the board itself takes that PATH.

Why: launchd/cron start jobs with /usr/bin:/bin:/usr/sbin:/sbin, so Claude Code could not start MCP servers run by
node/npx (nvm) in the scheduled review, and then skipped them for 15 minutes in every other session too.

Run: python3 tests/test_review_schedule.py  (stdlib only; a fresh temp HOME, fake launchctl / crontab / shell /
claude on PATH: no real scheduler entry is made and no real claude runs)."""
import json
import os
import plistlib
import stat
import subprocess
import sys
import tempfile
import threading
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="review-schedule-test-")
os.environ["HOME"] = os.path.join(TMP, "home")   # before importing: the modules read ~ when called
os.makedirs(os.environ["HOME"])
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.environ.pop("ONEMORETASK_KEEP_PATH", None)
sys.path.insert(0, os.path.dirname(HERE))
import review_schedule as rs  # noqa: E402
import shell_env  # noqa: E402

FAILS = []


def check(ok, what):
    print(("ok   " if ok else "FAIL ") + what)
    if not ok:
        FAILS.append(what)


def script(path, body):
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)
    return path


BIN = os.path.join(TMP, "bin")          # fake launchctl / crontab / claude
NODE = os.path.join(TMP, "nvm", "bin")  # where only the login shell knows node lives
os.makedirs(BIN)
os.makedirs(NODE)
script(os.path.join(NODE, "node"), "exit 0\n")
CALLS = os.path.join(TMP, "launchctl.log")
LOADED = os.path.join(TMP, "launchctl.loaded")
script(os.path.join(BIN, "launchctl"), f"""echo "$@" >> '{CALLS}'
case "$1" in
  bootstrap) touch '{LOADED}' ;;
  bootout) rm -f '{LOADED}' ;;
  print) [ -f '{LOADED}' ] || exit 113 ;;
esac
exit 0
""")
CRONTAB = os.path.join(TMP, "crontab.txt")
script(os.path.join(BIN, "crontab"), f"""if [ "$1" = "-l" ]; then [ -f '{CRONTAB}' ] || exit 1; cat '{CRONTAB}'; else cat > '{CRONTAB}'; fi
""")
CLAUDE_ARGS = os.path.join(TMP, "claude.args")
script(os.path.join(BIN, "claude"), f"""printf '%s\\n' "$@" > '{CLAUDE_ARGS}'
echo "PATH=$PATH" >> '{CLAUDE_ARGS}'
echo review ran
exit 0
""")
LOGIN = f"{NODE}:/usr/bin:/bin"
SHELL = script(os.path.join(TMP, "fakeshell"), f'echo "motd noise"\nprintf "\\n{shell_env.MARK}{LOGIN}{shell_env.MARK}\\n"\n')
BROKEN_SHELL = script(os.path.join(TMP, "brokenshell"), "exit 1\n")
os.environ["SHELL"] = SHELL
os.environ["PATH"] = f"{BIN}:/usr/bin:/bin:/usr/sbin:/sbin"   # what launchd gives: no node

proj = os.path.join(TMP, "proj")
os.makedirs(os.path.join(proj, "skill-observations"))

# --- shell_env
check(shell_env.merged("/a:/b", "/b:/c::/a") == "/a:/b:/c", "merged keeps login order, adds the rest once")
check(shell_env.login_path(SHELL) == LOGIN, "login_path reads the PATH between the marks, past shell noise")
check(shell_env.login_path(BROKEN_SHELL) == "", "a failing shell gives no PATH")
env = {"PATH": "/usr/bin:/bin"}
added = shell_env.fix_path(env, SHELL)
check(env["PATH"].split(":")[0] == NODE and NODE in added, "fix_path puts the login PATH first and reports it")
env = {"PATH": "/usr/bin", "ONEMORETASK_KEEP_PATH": "1"}
check(shell_env.fix_path(env, SHELL) == [] and env["PATH"] == "/usr/bin", "ONEMORETASK_KEEP_PATH leaves PATH alone")

# --- input checks
for days, at in (([], "09:00"), ([7], "09:00"), ([0], "25:00"), ([0], "9h")):
    try:
        rs.clean(days, at)
        check(False, f"clean refuses {days} {at}")
    except ValueError:
        check(True, f"clean refuses {days} {at}")
check(rs.clean(["4", 0, 0], "7:05") == ([0, 4], "07:05"), "clean sorts, dedupes and pads")

# --- macOS: a LaunchAgent with the login PATH, loaded with launchctl
rs.WINDOWS, rs.MAC = False, True
st = rs.install(proj, [0, 2, 6], "09:07")
p = rs.plist_path(proj)
with open(p, "rb") as f:
    pl = plistlib.load(f)
check(st["on"] and st["scheduler"] == "launchd", "launchd: status on after install")
check(sorted(e["Weekday"] for e in pl["StartCalendarInterval"]) == [0, 1, 3], "launchd: Mon/Wed/Sun -> 1/3/0")
check(all(e["Hour"] == 9 and e["Minute"] == 7 for e in pl["StartCalendarInterval"]), "launchd: 09:07")
check(pl["EnvironmentVariables"]["PATH"].split(":")[0] == NODE, "launchd: the job carries the login PATH")
check(pl["ProgramArguments"][-3:] == ["run", "--project", os.path.realpath(proj)], "launchd: runs review_schedule run")
calls = open(CALLS).read().split("\n")
check(any(c.startswith("bootout") for c in calls) and any(c.startswith("bootstrap") for c in calls),
      "launchd: an old copy is booted out, the new one bootstrapped")
rs.install(proj, [1], "10:00")
check(len([c for c in open(CALLS).read().split("\n") if c.startswith("bootstrap")]) == 2 and rs.status(proj)["days"] == [1],
      "launchd: a second install replaces the job")
st = rs.remove(proj)
check(not st["on"] and not os.path.exists(p), "launchd: remove unloads it and deletes the plist")
rs.install(proj, [0], "09:07")
os.remove(LOADED)   # the job vanished behind the board's back
check(rs.status(proj)["missing"], "launchd: a setting with no loaded job shows as missing")
rs.remove(proj)

# --- cron: one tagged line, other lines kept
rs.MAC = False
with open(CRONTAB, "w") as f:
    f.write("0 1 * * * /usr/bin/backup\n")
rs.install(proj, [0, 2, 4], "09:07")
rs.install(proj, [0, 2, 4], "09:07")
lines = open(CRONTAB).read().splitlines()
ours = [x for x in lines if x.rstrip().endswith(f"{rs.CRON_TAG} {rs.job_id(proj)}")]
check(len(ours) == 1 and "/usr/bin/backup" in lines[0], "cron: one line of ours, the user's line kept")
check(ours and ours[0].startswith("7 9 * * 1,3,5 PATH='" + NODE), "cron: 09:07 Mon/Wed/Fri with the login PATH")
check(rs.status(proj)["on"], "cron: status on")
rs.remove(proj)
check(open(CRONTAB).read() == "0 1 * * * /usr/bin/backup\n", "cron: remove leaves only the user's line")

# --- the run: login PATH taken, claude started in the project with the review prompt, all logged
code = rs.run(proj)
args = open(CLAUDE_ARGS).read()
log = open(rs.log_file(proj)).read()
check(code == 0, "run: exit 0")
check("comprehensive review" in args and os.path.join(proj, "skill-observations") in args, "run: the review prompt names the log")
check("--permission-mode\nauto" in args, "run: auto permission mode, like cards")
check(f"PATH={NODE}" in args, "run: claude gets the login PATH (node first)")
check(f"node={NODE}/node" in log and "exit 0" in log, "run: the log names node and the exit code")

# --- the board's routes
import server  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

pr = server.open_project(proj)
srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
port = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{port}"


def call(method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", "Origin": base})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


code, st = call("GET", f"/api/review-schedule?p={pr.id}")
check(code == 200 and st["on"] is False and st["days"] == [0, 2, 4], "GET: off with the default days")
code, st = call("POST", f"/api/review-schedule?p={pr.id}", {"p": pr.id, "on": True, "days": [1, 3], "time": "08:30"})
check(code == 200 and st["on"] and st["time"] == "08:30", "POST on: installed")
code, st = call("POST", f"/api/review-schedule?p={pr.id}", {"p": pr.id, "on": True, "days": [], "time": "08:30"})
check(code == 400 and "day" in st.get("error", ""), "POST with no day: 400")
code, st = call("POST", f"/api/review-schedule?p={pr.id}", {"p": pr.id, "on": False})
check(code == 200 and not st["on"] and rs.CRON_TAG not in open(CRONTAB).read(), "POST off: removed")
srv.shutdown()

print(f"test_review_schedule: {'PASS' if not FAILS else 'FAIL (%d)' % len(FAILS)}")
sys.exit(1 if FAILS else 0)
