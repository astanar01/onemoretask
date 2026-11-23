"""S52 / S53: people can read about Antigravity (changelog, README, guide, conventions) and every new page string has a
translation entry.

Run: python3 tests/test_agy_docs.py  (stdlib only; reads repo files)."""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANNED = ("git", "commit", "server", "api", "file", "code", "session", "prompt", "model", "bug")
FAILS = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def changelog_lines():
    out, day = [], None
    for line in read("whats-new.md").splitlines():
        if line.startswith("## "):
            day = line[3:].strip()
        elif line.startswith("- ") and day and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            out.append(line[2:])
    return out


def plain(line):
    words = set(re.findall(r"[a-z]+", line.lower()))
    return not any(b in words for b in BANNED)


def test_s52_changelog_send_line():
    hits = [x for x in changelog_lines() if "Antigravity" in x and "Codex" in x and plain(x)
            and re.search(r"\bsend\b|\bsent\b", x, re.I)]
    check(bool(hits), "S52 a plain changelog line says cards can go to Antigravity")


def test_s52_changelog_rules_line():
    hits = [x for x in changelog_lines() if "Antigravity" in x and "Codex" in x and plain(x)
            and re.search(r"rules", x, re.I) and re.search(r"skill", x, re.I)]
    check(bool(hits), "S52 a plain changelog line says Codex and Antigravity follow the same rules and skills")


def test_s52_docs_name_antigravity():
    readme = read("README.md")
    guide = read(os.path.join("docs", "guide.md"))
    conv = read(os.path.join("docs", "board-conventions.md"))
    check(readme.count("Antigravity") >= 3, "S52 README names Antigravity (intro, pick a model, optional CLIs)")
    check(guide.count("Antigravity") >= 3, "S52 guide names Antigravity (requirements, model picker, reviewer picker)")
    check("Antigravity" in conv and re.search(r"fake `?agy`?", conv) is not None and "temp HOME" in conv,
          "S52 conventions: Antigravity tests use a fake agy and a temp HOME")


def test_s53_i18n_entries():
    i18n = read("i18n.js")
    for s in ("Send to Antigravity", "Start a background Antigravity session on this task",
              "Antigravity: log in once in a terminal", "Antigravity: no models yet",
              "Codex's or Antigravity's workers"):
        check(s in i18n, "S53 i18n.js has an entry holding: " + s)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("\n%d failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)
