"""A delegating card pins its subagents to the card's subagent model, on the first send and when a closed card is
reopened; inbox_settings carries the pin as env only when there is one.

Run: python3 tests/test_subagent_model_pin.py  (stdlib only, no Claude session started)."""
import json
import os
import sys
import tempfile
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="subagent-pin-"))
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)
import server  # noqa: E402

p = types.SimpleNamespace(board=TMP, path=TMP, id="p")

# pinned_worker: only a delegating Claude card with a Claude subagent model
assert server.pinned_worker({"delegate": True, "subagentModel": "opus"}, "fable") == "opus"
assert server.pinned_worker({"delegate": False, "subagentModel": "opus"}, "fable") == ""
assert server.pinned_worker({"delegate": True}, "fable") == ""
assert server.pinned_worker({"delegate": True, "subagentModel": "opus"}, "gpt-5.5") == ""  # a Codex session

# inbox_settings: env only with a worker
s = json.loads(server.inbox_settings(p, "x", "opus"))
assert s["env"] == {"CLAUDE_CODE_SUBAGENT_MODEL": "opus", "CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"}, s
assert "env" not in json.loads(server.inbox_settings(p, "x")), "no pin without a worker"

# reopen: a closed delegating card starts its new session with the pin
calls = []
server.start_session = lambda *a, **k: calls.append(a) or "aid1"
card = {"id": "x", "title": "card", "delegate": True, "subagentModel": "opus", "agent": {"model": "fable", "effort": ""}}
server.load_tasks = lambda p: {"tasks": [card]}
server.reports.read_recap = lambda board, task_id: "recap"
server.reports.append = lambda *a, **k: None
server.renew_worktree = lambda p, t: None
server.live_worktree = lambda t: None
server.build_prompt = lambda *a: "prompt"
server.WATCH = types.SimpleNamespace(expect=lambda *a: None)
server.reopen(p, "x", "go on")
assert calls and calls[0][6] == "opus", calls

print("ok")
