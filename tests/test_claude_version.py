"""The Claude Code button: compares `claude --version` with npm's dist-tag for the user's channel, and
`claude update` installs the newer one.

Run: python3 tests/test_claude_version.py  (stdlib; a fake `claude` script and a fake npm answer, temp HOME)."""
import io
import json
import os
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="claude-version-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.path.join(os.environ["HOME"], ".claude"))
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

# A fake claude: --version prints the version in ver.txt; `update` writes NEW into it (or fails when FAIL is set).
VER = os.path.join(TMP, "ver.txt")
FAKE = os.path.join(TMP, "claude")
with open(FAKE, "w") as f:
    f.write(f"""#!/bin/sh
if [ "$1" = "--version" ]; then echo "$(cat '{VER}') (Claude Code)"; exit 0; fi
if [ "$1" = "update" ]; then
  [ -n "$FAIL" ] && {{ echo "Error: no permission to write" >&2; exit 1; }}
  echo "$NEW" > '{VER}'; echo "Updated"; exit 0
fi
exit 2
""")
os.chmod(FAKE, os.stat(FAKE).st_mode | stat.S_IEXEC)
server.claude_cmd = lambda found=None: [FAKE]

TAGS = {"stable": "2.1.292", "latest": "2.1.300"}
asked = []


def fake_urlopen(url, timeout=None):
    asked.append(url)
    if TAGS is None:
        raise OSError("network down")
    return io.BytesIO(json.dumps(TAGS).encode())


server.urlopen = fake_urlopen


def set_ver(v):
    with open(VER, "w") as f:
        f.write(v + "\n")


def set_channel(ch):
    with open(os.path.join(os.environ["HOME"], ".claude", "settings.json"), "w") as f:
        json.dump({"autoUpdatesChannel": ch} if ch else {}, f)


fails = 0


def check(name, ok, detail=""):
    global fails
    fails += not ok
    print(("ok  " if ok else "FAIL"), name, "" if ok else detail)


set_ver("2.1.290")
set_channel(None)
st = server.claude_version_status()
check("latest channel by default", st["channel"] == "latest" and st["latest"] == "2.1.300", st)
check("older install is outdated", st["installed"] == "2.1.290" and st["outdated"] and not st["error"], st)
check("asked npm", asked and asked[-1] == server.NPM_TAGS, asked)

set_channel("stable")
st = server.claude_version_status()
check("stable channel read from settings", st["channel"] == "stable" and st["latest"] == "2.1.292" and st["outdated"], st)
set_ver("2.1.300")
check("newer than the channel is not outdated", not server.claude_version_status()["outdated"])
set_ver("2.1.10")
set_channel(None)
check("versions compare as numbers (2.1.10 < 2.1.300)", server.claude_version_status()["outdated"])
set_ver("2.1.300")
check("same version is not outdated", not server.claude_version_status()["outdated"])

TAGS = None
real_which = server.shutil.which
server.shutil.which = lambda name: None   # no curl to fall back on
st = server.claude_version_status()
check("network down: error, not outdated", st["error"] and not st["outdated"] and st["installed"] == "2.1.300", st)

# urllib fails (python.org Python on macOS has no CA certificates): curl is asked instead.
CURL = os.path.join(TMP, "curl")
with open(CURL, "w") as f:
    f.write("#!/bin/sh\necho '{\"latest\": \"2.1.400\"}'\n")
os.chmod(CURL, os.stat(CURL).st_mode | stat.S_IEXEC)
server.shutil.which = lambda name: CURL if name == "curl" else None
st = server.claude_version_status()
check("urllib fails: curl fallback used", st["latest"] == "2.1.400" and st["outdated"] and not st["error"], st)
server.shutil.which = real_which
TAGS = {"stable": "2.1.292", "latest": "2.1.300"}

# claude update installs the newer one and the cached status follows it.
set_ver("2.1.290")
u = server.ClaudeUpdater()
check("updater sees the update", u.status()["outdated"])
os.environ["NEW"] = "2.1.300"
old, new = u.update()
check("update ran: from/to", (old, new) == ("2.1.290", "2.1.300"), (old, new))
check("status after update is current", not u.status()["outdated"], u.status())

set_ver("2.1.290")
os.environ["FAIL"] = "1"
try:
    u.update()
    check("failed update raises", False)
except RuntimeError as e:
    check("failed update raises with claude's message", "no permission" in str(e), str(e))
del os.environ["FAIL"]

server.claude_cmd = lambda found=None: [os.path.join(TMP, "missing-claude")]
st = server.claude_version_status()
check("no claude: says so", st["error"] == "Could not find Claude Code." and not st["outdated"], st)

print("PASS" if not fails else f"{fails} failed")
sys.exit(1 if fails else 0)
