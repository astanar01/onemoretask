"""Token totals on a card: main sessions + subagents, a split reply counted once, a forked resume not double counted,
a removed session still counted from the recap's transcript list.

Run: python3 tests/test_tokens.py  (stdlib only; a fresh temp HOME)."""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="tokens-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
sys.path.insert(0, os.path.dirname(HERE))
import reports  # noqa: E402
import server  # noqa: E402

server.PROJECTS_DIR = os.path.join(os.environ["HOME"], ".claude", "projects")
tdir = os.path.join(server.PROJECTS_DIR, "-fake-project")
os.makedirs(tdir)


def reply(mid, inp, cw, cr, out):
    # Shape of a real transcript line (keys trimmed).
    return {"type": "assistant", "timestamp": "2026-10-07T10:00:00.000Z", "message": {
        "id": mid, "role": "assistant", "content": [{"type": "text", "text": "hi"}],
        "usage": {"input_tokens": inp, "cache_creation_input_tokens": cw, "cache_read_input_tokens": cr,
                  "output_tokens": out, "cache_creation": {"ephemeral_1h_input_tokens": cw}}}}


def write(path, entries, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding="utf-8") as f:
        f.writelines(json.dumps(e) + "\n" for e in entries)


user = {"type": "user", "timestamp": "2026-10-07T10:00:00.000Z", "message": {"role": "user", "content": "go"}}
A = os.path.join(tdir, "aaaa.jsonl")
# msg_1 is split over two entries (text, then tool_use) whose output count grows while it streams.
write(A, [user, reply("msg_1", 3, 1000, 0, 5), reply("msg_1", 3, 1000, 0, 40), reply("msg_2", 1, 10, 1000, 7)])
write(os.path.join(tdir, "aaaa", "subagents", "agent-x1.jsonl"), [user, reply("msg_s1", 2, 500, 0, 20)])

t = server.tokens([A])
assert t["main"] == [4, 1010, 1000, 47], t
assert t["subagents"] == [2, 500, 0, 20] and t["count"] == 1, t
print("ok  main + subagent, split reply counted once with its final output count")

# A forked resume copies the history (same message ids) and adds its own replies.
B = os.path.join(tdir, "bbbb.jsonl")
write(B, [user, reply("msg_1", 3, 1000, 0, 40), reply("msg_2", 1, 10, 1000, 7), reply("msg_3", 1, 0, 2000, 9)])
t = server.tokens([A, B])
assert t["main"] == [5, 1010, 3000, 56], t
print("ok  forked resume: copied replies count once")

# Appending reads only the new bytes and still counts right; a half-written line waits for the next pass.
with open(B, "a", encoding="utf-8") as f:
    f.write(json.dumps(reply("msg_4", 1, 0, 100, 1)) + "\n" + json.dumps(reply("msg_5", 1, 0, 100, 1))[:30])
assert server.tokens([A, B])["main"] == [6, 1010, 3100, 57]
with open(B, "a", encoding="utf-8") as f:
    f.write(json.dumps(reply("msg_5", 1, 0, 100, 1))[30:] + "\n")
assert server.tokens([A, B])["main"] == [7, 1010, 3200, 58]
print("ok  incremental reads, half-written last line")

# Subagent rows carry their own total.
row = server.subagents("aaaa")
assert [r["tokens"] for r in row] == [522], row
print("ok  subagent row tokens")

# A card whose sessions were removed: the recap's transcript list still finds them.
board = os.path.join(TMP, "board")
os.makedirs(os.path.dirname(reports.recap_path(board, "t1")))
p = type("P", (), {"board": board})()
with open(reports.recap_path(board, "t1"), "w", encoding="utf-8") as f:
    f.write("Did things.\n\n" + server.TRANSCRIPTS + "\n\nFull conversations:\n\n- " + A + "\n- " + B + "\n")
paths = server.task_transcripts(p, "t1", ["aaaa", None, "missing"])
assert paths == [A, B], paths
print("ok  task_transcripts: live sessions + recap list, deduped, missing dropped")

# Context: size at the last real API call, the largest so far, the model's window. A compaction shrinks it; Claude
# Code's own <synthetic> error note after it (zero usage) is not an API call.
def on(model, e):
    e["message"]["model"] = model
    return e


C = os.path.join(tdir, "cccc.jsonl")
write(C, [user, on("claude-opus-5-5", reply("m1", 5, 1000, 0, 9)), on("claude-opus-5-5", reply("m2", 5, 300, 400000, 9)),
          on("claude-opus-5-5", reply("m3", 5, 20000, 0, 9)), on("<synthetic>", reply("m4", 0, 0, 0, 0))])
c = server.context("cccc")
assert c == {"tokens": 20005, "peak": 400305, "model": "claude-opus-5-5", "max": 1_000_000}, c
assert server.context("missing") is None
windows = {m: server.context_window(m) for m in ("claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5",
                                                  "claude-haiku-5-5", "claude-haiku-4-5-20251001", "gpt-5.5", "")}
assert windows == {"claude-fable-5-1": 1_000_000, "claude-opus-5-5": 1_000_000, "claude-sonnet-5-5": 1_000_000,
                   "claude-haiku-5-5": 1_000_000, "claude-haiku-4-5-20251001": 200_000, "gpt-5.5": None, "": None}, windows
print("ok  context: last call, peak, model window; synthetic note skipped")
print("PASS")
