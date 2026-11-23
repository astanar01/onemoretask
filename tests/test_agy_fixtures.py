"""S2: the saved real Antigravity runs match what expect.json says about them.

Run: python3 tests/test_agy_fixtures.py  (stdlib only; reads tests/fixtures/antigravity)."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FIX = os.path.join(HERE, "fixtures", "antigravity")
NAMES = ["a", "a-text", "b", "c", "d", "e", "f", "g", "h"]
FAILS = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def test_s2_fixtures_match_expect():
    with open(os.path.join(FIX, "expect.json"), encoding="utf-8") as f:
        expect = json.load(f)
    check(sorted(expect) == sorted(NAMES), "S2 expect.json names every fixture")
    for n in NAMES:
        paths = {ext: os.path.join(FIX, "%s.%s" % (n, ext)) for ext in ("stdout", "stderr", "exit")}
        check(all(os.path.isfile(p) for p in paths.values()), "S2 %s has stdout, stderr and exit" % n)
        with open(paths["stdout"], encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        events = []
        ndjson = bool(lines)
        for ln in lines:
            try:
                events.append(json.loads(ln).get("event"))
            except ValueError:
                ndjson = False
        e = expect.get(n, {})
        check(ndjson == e.get("ndjson"), "S2 %s stdout ndjson=%s" % (n, e.get("ndjson")))
        check(("init" in events) == e.get("init"), "S2 %s init line=%s" % (n, e.get("init")))
        check(("result" in events) == e.get("result"), "S2 %s result line=%s" % (n, e.get("result")))
        with open(paths["exit"], encoding="utf-8") as f:
            check(int(f.read().strip()) == e.get("exit"), "S2 %s exit=%s" % (n, e.get("exit")))
        for ext in ("stdout", "stderr"):
            with open(paths[ext], encoding="utf-8") as f:
                body = f.read()
            check("/Users/" not in body.replace("/Users/USER", ""), "S2 %s.%s holds no real home path" % (n, ext))


if __name__ == "__main__":
    test_s2_fixtures_match_expect()
    print("\n%d failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)
