"""A progress note sent right after the done report must not turn a finished card into "Stopped after a progress note".

Run: python3 tests/test_phase_of.py  (stdlib only; a fresh temp HOME, no session calls)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
os.environ["HOME"] = tempfile.mkdtemp(prefix="phase-test-")  # before importing server: it reads ~ at import
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

# The CLI marks the turn "blocked" when its own summary sounds like it waits on you (the real case).
BLOCKED = {"id": "s1", "state": "blocked", "status": "idle", "detail": "hat feature complete; awaiting push confirmation"}


def entry(frm, status, msg="m"):
    return {"from": frm, "status": status, "message": msg}


fails = 0


def check(name, log, want_phase, want_reason=None):
    global fails
    phase, reason = server.phase_of(BLOCKED, log, 0)
    ok = phase == want_phase and (want_reason is None or reason == want_reason)
    fails += not ok
    print(("PASS" if ok else "FAIL"), name, "->", phase, repr(reason[:70]))


# The real hat card: done, then a correction sent as progress 8 s later.
check("done then progress", [entry("you", "reply"), entry("claude", "done", "Hat report"), entry("claude", "progress")],
      "finished", "Hat report")
check("answer then two progress", [entry("claude", "answer", "A"), entry("claude", "progress"), entry("claude", "progress")],
      "finished", "A")
# Negative controls: a progress with no done before it, or a done from an older turn, still needs you.
check("progress only", [entry("you", "reply"), entry("claude", "progress")], "needs_you")
check("old done, reply, progress", [entry("claude", "done"), entry("you", "reply"), entry("claude", "progress")], "needs_you")
check("question then progress", [entry("claude", "question"), entry("claude", "progress")], "needs_you")

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
