"""codex.usage / subagents / subagent_usage skip token fields that are not numbers instead of raising.

    python3 tests/test_review_scripts_codex_usage.py
"""
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp()
os.environ["HOME"] = os.path.join(TMP, "home")
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import codex  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "codex")
fails = []


def check(name, fn):
    try:
        ok, got = fn()
    except Exception as e:
        ok, got = False, repr(e)
    print(("ok  " if ok else "FAIL") + f" {name}: {got}")
    if not ok:
        fails.append(name)


try:
    log = os.path.join(TMP, "s.jsonl")
    with open(log, "w", encoding="utf-8") as f:
        for u in ({"input_tokens": "lots", "output_tokens": [1]}, {"input_tokens": 10, "cached_input_tokens": {},
                  "output_tokens": 5, "cache_write_input_tokens": "2"}):
            f.write(json.dumps({"type": "turn.completed", "usage": u}) + "\n")
    check("usage skips non-number fields", lambda: (lambda u: (u == [10, 2, 0, 5], u))(codex.usage(log)))

    run = os.path.join(TMP, "w1-1.jsonl")
    shutil.copy(os.path.join(FIX, "run1.jsonl"), run)
    day = os.path.join(TMP, "codex-home", "sessions", "2026", "10", "07")
    os.makedirs(day)
    bad = iter([{"input_tokens": "lots", "total_tokens": "many"}, [1, 2]])
    for n in sorted(os.listdir(FIX)):
        if not n.startswith("rollout-"):
            continue
        swap = next(bad)
        with open(os.path.join(FIX, n), encoding="utf-8") as f, open(os.path.join(day, n), "w", encoding="utf-8") as g:
            for line in f:
                e = json.loads(line)
                p = e.get("payload") if isinstance(e.get("payload"), dict) else {}
                if p.get("type") == "token_count" and isinstance(p.get("info"), dict):
                    p["info"]["total_token_usage"] = swap
                g.write(json.dumps(e) + "\n")
    os.environ["CODEX_HOME"] = os.path.join(TMP, "codex-home")
    check("subagents with bad token fields", lambda: (lambda r: (len(r) == 2 and all(x["tokens"] == 0 for x in r),
                                                                 [x["tokens"] for x in r]))(codex.subagents(run)))
    check("subagent_usage with bad token fields",
          lambda: (lambda u: (u == ([0, 0, 0, 0], 2), u))(codex.subagent_usage(run)))
finally:
    shutil.rmtree(TMP, ignore_errors=True)

sys.exit(1 if fails else 0)
