"""A card reply that starts with "-" (a bullet list) still reaches the session.

resume passes the message as the last argument of `claude --bg --resume <id> <message>`. The CLI reads an argument
starting with "-" as an option and fails with "error: unknown option '- fix the thing'" (measured on the real CLI),
after the session was already stopped for the resume.

Run: python3 tests/test_review_server_dash_reply.py  (stdlib only; a fresh temp HOME, every CLI call faked)."""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="dash-reply-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

try:
    proj = os.path.join(TMP, "proj")
    os.makedirs(proj)
    p = server.Project(proj)
    os.makedirs(p.board)
    calls = []

    def fake_claude(args, **kw):
        calls.append(args)
        if args[:2] == ["--bg", "--resume"]:
            # What the CLI's option parser does with the positional message.
            if args[3].startswith("-"):
                return subprocess.CompletedProcess(args, 1, "", f"error: unknown option '{args[3]}'")
            return subprocess.CompletedProcess(args, 0, "backgrounded · abc123", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    server.run_claude = fake_claude
    server.all_sessions = lambda p_: []
    server.time.sleep = lambda s: None
    for text in ("- fix the thing\n- and this", "--help", "plain reply"):
        try:
            aid = server.resume(p, {"id": "a0", "sessionId": "s0"}, text)
        except RuntimeError as e:
            raise SystemExit(f"FAIL reply {text!r} did not reach the session: {e}")
        if aid != "abc123" or text.strip() not in calls[-1][3]:
            raise SystemExit(f"FAIL reply {text!r} was changed or lost: {calls[-1]}")
    print("PASS replies starting with '-' reach the session")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
