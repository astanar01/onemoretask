"""A progress note sent right after the done report must not turn a finished card into "Stopped after a progress note",
and a card note Claude picks up after its done report must show it working again.

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


def entry(frm, status, msg="m", at="2026-10-14T15:00:00"):
    return {"from": frm, "status": status, "message": msg, "at": at}


fails = 0


def check(name, log, want_phase, want_reason=None, session=BLOCKED, delivered="9999"):
    global fails
    phase, reason = server.phase_of(session, log, 0, delivered)
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

# A note sent as Claude finished, that it then works on (the real no-title card): the old done must not end the turn.
BUSY = {"id": "s1", "state": "working", "status": "busy"}
NOTE_RUN = [entry("you", "reply"), entry("claude", "done", "Old"), entry("you", "note"), entry("claude", "progress")]
check("done, note, progress, busy", NOTE_RUN, "working", session=BUSY)
check("done, note, progress, stopped", NOTE_RUN, "needs_you")
check("done, note, progress, new done", NOTE_RUN + [entry("claude", "done", "New")], "finished", "New", session=BUSY)
# Negative controls: a note Claude has not picked up yet leaves the done as it was (the watcher handles delivery).
check("done, note, nothing yet", [entry("claude", "done", "Old"), entry("you", "note")], "finished", "Old", session=BUSY)
check("note before done", [entry("you", "note"), entry("claude", "done", "Old"), entry("claude", "progress")],
      "finished", "Old", session=BUSY)
# A fix-up line Claude posts after its done, before the note is handed to it, is not Claude picking the note up.
check("done, note not handed over, fix-up progress", NOTE_RUN, "finished", "Old", delivered="")
check("done, older note handed over, newer note not, fix-up progress",
      [entry("claude", "done", "Old", at="t1"), entry("you", "note", at="t2"), entry("claude", "progress", at="t3")],
      "finished", "Old", delivered="t0")

print("ALL PASS" if not fails else f"{fails} FAILED")
sys.exit(1 if fails else 0)
