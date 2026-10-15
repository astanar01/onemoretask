"""A send that fails after its session started stops that session instead of leaving it running with no card.

Run: python3 tests/test_review_server_launch_orphan.py  (stdlib only; a fresh temp HOME, session start/stop faked)."""
import json
import os
import shutil
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="launch-orphan-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

fails = []
try:
    proj = os.path.join(TMP, "proj")
    os.makedirs(proj)
    p = server.Project(proj)
    os.makedirs(p.board)
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump({"columns": [{"id": "c", "name": "To Do"}],
                   "tasks": [{"id": "t1", "title": "Fix it", "column": "c", "worktree": False}]}, f)
    stopped, removed = [], []
    server.start_session = lambda p_, task_id, name, prompt, model: "cdx-1" if server.codex.is_model(model) else "a1"
    server.run_claude = lambda args, **kw: stopped.append(args) or types.SimpleNamespace(returncode=0, stdout="", stderr="")
    server.codex.remove = lambda board, tid: removed.append(tid)

    def broken(*a, **kw):
        raise OSError("disk full")

    server.set_agent = broken
    for model, want in (("", (["stop", "a1"], stopped)), ("gpt-5", ("cdx-1", removed))):
        try:
            server.launch(p, "t1", model)
            fails.append(f"{model or 'claude'}: launch did not raise")
        except OSError as e:
            if "disk full" not in str(e):
                fails.append(f"{model or 'claude'}: wrong error {e}")
        if want[0] not in want[1]:
            fails.append(f"{model or 'claude'}: session not stopped ({stopped}, {removed})")
        if server.LAUNCHING:
            fails.append(f"{model or 'claude'}: LAUNCHING guard left set: {server.LAUNCHING}")
    # With a worktree: the send's fresh worktree and branch are taken back too.
    import subprocess
    git = lambda *a: subprocess.run(["git", "-C", proj, *a], capture_output=True, text=True)
    git("init", "-q"); git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "init")
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump({"columns": [{"id": "c", "name": "To Do"}], "tasks": [{"id": "t1", "title": "Fix it", "column": "c"}]}, f)
    before = git("worktree", "list").stdout.count("\n")
    try:
        server.launch(p, "t1", "")
        fails.append("worktree: launch did not raise")
    except OSError:
        pass
    after = git("worktree", "list").stdout.count("\n")
    if after != before:
        fails.append(f"worktree: left on disk ({git('worktree', 'list').stdout.strip()})")
    if git("branch", "--list").stdout.count("\n") != 1:
        fails.append(f"worktree: branch left: {git('branch', '--list').stdout.strip()}")
    print(("FAIL " + "; ".join(fails)) if fails else "ok  claude and codex sessions stopped, worktree taken back, error re-raised, guard clear")
finally:
    shutil.rmtree(TMP, ignore_errors=True)

if fails:
    raise SystemExit(1)
print("PASS")
