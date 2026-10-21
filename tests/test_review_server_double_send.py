"""Two sends of the same card at once start one session, not two.

launch read tasks.json, saw no agent, and started the session; a second send landing during that start (a double
click, a second board window) saw no agent either and started a twin that the card never tracked.

Run: python3 tests/test_review_server_double_send.py  (stdlib only; a fresh temp HOME, the session start faked)."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="double-send-test-")
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
    with open(p.data, "w", encoding="utf-8") as f:
        json.dump({"columns": [{"id": "c", "name": "To Do"}],
                   "tasks": [{"id": "t1", "title": "Fix it", "column": "c", "worktree": False}]}, f)
    started = []

    def fake_start(p_, task_id, name, prompt, model, effort=""):
        time.sleep(0.3)  # `claude --bg` takes seconds to answer
        started.append(task_id)
        return f"a{len(started)}"

    server.start_session = fake_start
    results = []

    def send():
        try:
            results.append(server.launch(p, "t1")["id"])
        except PermissionError as e:
            results.append(f"refused: {e}")

    threads = [threading.Thread(target=send) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    if len(started) != 1:
        raise SystemExit(f"FAIL {len(started)} sessions started for one card: {results}")
    if not any(r.startswith("refused") for r in results):
        raise SystemExit(f"FAIL the second send was not refused: {results}")
    # The card is free again afterwards: a later send is refused only because the card now has its session.
    try:
        server.launch(p, "t1")
        raise SystemExit("FAIL a send after the start was accepted")
    except PermissionError as e:
        if "already sent" not in str(e):
            raise SystemExit(f"FAIL wrong refusal after the start: {e}")
    print("PASS one session for two sends at once")
finally:
    shutil.rmtree(TMP, ignore_errors=True)
