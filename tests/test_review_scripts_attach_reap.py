"""attach._terminal does not hang when the attached program ignores SIGTERM: it is killed after a short wait.

    python3 tests/test_review_scripts_attach_reap.py
"""
import os
import subprocess
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "reap-test-" + uuid.uuid4().hex
CHILD = f"""
import sys, time
sys.path.insert(0, {ROOT!r})
import attach
cmd = (sys.executable, "-c", "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)", {MARK!r})
start = time.time()
with attach._terminal("abc", cmd) as fd:
    time.sleep(0.5)
print("left after %.1fs" % (time.time() - start))
"""

start = time.time()
try:
    r = subprocess.run([sys.executable, "-c", CHILD], capture_output=True, text=True, timeout=15)
    ok = r.returncode == 0 and "left after" in r.stdout
    got = (r.stdout + r.stderr).strip()
except subprocess.TimeoutExpired:
    ok, got = False, "hung for 15 s"
finally:
    subprocess.run(["pkill", "-9", "-f", MARK])
still = subprocess.run(["pgrep", "-f", MARK], capture_output=True).returncode == 0
print(("ok  " if ok else "FAIL") + f" _terminal returns when the child ignores SIGTERM ({got})")
sys.exit(0 if ok and not still else 1)
