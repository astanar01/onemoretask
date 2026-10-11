"""Every piece of English the board shows has a French line in i18n.js, and no French line is stale.

Run: python3 tests/test_i18n.py            (stdlib only, reads index.html, i18n.js and server.py)
     python3 tests/test_i18n.py --keys     lists every key the page uses (to write translations from)

Keys come from: text nodes and title/placeholder/aria-label attributes in the static markup (what
translateDom() looks up), the inner markup of data-i18n elements, every t("...") / t('...') / t(`...`)
call in the page script, and the fixed {"error": "..."} messages server.py sends to the page."""
import json
import os
import re
import sys
from html.parser import HTMLParser

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

with open(os.path.join(ROOT, "index.html"), encoding="utf-8") as fh:
    PAGE = fh.read()
with open(os.path.join(ROOT, "i18n.js"), encoding="utf-8") as fh:
    I18N = fh.read()
with open(os.path.join(ROOT, "server.py"), encoding="utf-8") as fh:
    SERVER = fh.read()

ATTRS = ("title", "placeholder", "aria-label")


class Markup(HTMLParser):
    """Collects what translateDom() would look up: trimmed text nodes and the three attributes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.keys = []
        self.skip = 0        # inside <script>/<style>/data-i18n element
        self.stack = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        skip = tag in ("script", "style") or "data-i18n" in a
        self.stack.append((tag, skip))
        if skip:
            self.skip += 1
        for k in ATTRS:
            if a.get(k):
                self.keys.append(a[k])

    def handle_startendtag(self, tag, attrs):
        a = dict(attrs)
        for k in ATTRS:
            if a.get(k):
                self.keys.append(a[k])

    def handle_endtag(self, tag):
        while self.stack:
            t, skip = self.stack.pop()
            if skip:
                self.skip -= 1
            if t == tag:
                break

    def handle_data(self, data):
        if self.skip:
            return
        k = data.strip()
        if k:
            self.keys.append(k)


def markup_keys():
    p = Markup()
    p.feed(PAGE)
    keys = p.keys
    # data-i18n elements are looked up by their whole inner markup.
    for m in re.finditer(r"<(\w+)\b[^>]*\bdata-i18n\b[^>]*>(.*?)</\1>", PAGE, re.S):
        keys.append(m.group(2).strip())
    return keys


def js_keys():
    keys = []
    for q, body in re.findall(r"""\bt\(\s*(["'`])((?:\\.|(?!\1).)*)\1""", PAGE, re.S):
        keys.append(body.replace("\\" + q, q).replace("\\n", "\n").replace("\\\\", "\\"))
    return keys


# Board data the page renders through t(col.name): the default column names and the name of an added column.
DATA_KEYS = ["To Do", "In Progress", "Review", "Done", "New column"]

# Fixed sentences server.py sends and the page shows through t(ag.reason) / t(u.reason). Each must still be
# in server.py verbatim (the check below), so a reworded server line shows up here, not as silent English.
SERVER_SENTENCES = [
    "Moved to done: sessions closed, recap saved. A reply starts a new session from the recap.",
    "Session was removed",
    "Waiting on a permission prompt or a question in the session — attach to answer",
    "Stopped without reporting — reply to check on it",
    "Stopped without reporting — attach or reply to check on it",
    "Stopped after a progress note without reporting done — reply to check on it",
    "Stopped in the session — reply to check on it",
    "Claude Code's safety check kept failing, so Claude stopped. The board tries again in a minute.",
    "Claude Code's safety check kept failing, so Claude stopped. Reply to try again.",
    "Not installed from git — reinstall to get updates.",
    "Git is not installed — install git to get updates.",
    "Local changes in the app folder — update by hand with git pull.",
    "This copy has commits that are not on GitHub.",
    "Could not check GitHub — try again later.",
    "Nothing to update.",
]


def server_keys():
    keys = re.findall(r'"error":\s*"((?:[^"\\]|\\.)+)"', SERVER)
    for s in SERVER_SENTENCES:
        assert s in SERVER, "server.py no longer holds the sentence: " + s
        keys.append(s)
    return keys + DATA_KEYS


def fr_dict():
    m = re.search(r"/\* fr-start \*/\s*(\{.*\})\s*/\* fr-end \*/", I18N, re.S)
    assert m, "i18n.js: fr-start / fr-end markers missing"
    return json.loads(m.group(1))


def all_keys():
    seen, out = set(), []
    for k in markup_keys() + js_keys() + server_keys():
        if k not in seen and not IGNORE.match(k):
            seen.add(k)
            out.append(k)
    return out


# Markup text that is not English: symbols, numbers, names, pure punctuation.
IGNORE = re.compile(r"^(?:[^A-Za-z]*|Task Board|Claude|Codex|Français|English|[A-Z]{1,3}|\d+\s*%|[a-z]+-[a-z-]+)$")


def main():
    fr = fr_dict()
    keys = all_keys()
    if "--keys" in sys.argv:
        for k in keys:
            print(json.dumps(k, ensure_ascii=False) + ("" if k in fr else "   <- missing"))
        print(f"{len(keys)} keys, {sum(k in fr for k in keys)} translated", file=sys.stderr)
        return 0
    fails = []
    for k in keys:
        if k not in fr:
            fails.append("no French line for " + json.dumps(k, ensure_ascii=False))
        else:
            for ph in re.findall(r"\{\w+\}", k):
                if ph not in fr[k]:
                    fails.append(f"French line for {json.dumps(k, ensure_ascii=False)} lost the placeholder {ph}")
        if "${" in k:
            fails.append("t() key holds a ${} interpolation (use {name} placeholders): " + json.dumps(k, ensure_ascii=False))
    for k in fr:
        if k not in keys:
            fails.append("stale French line (nothing on the page uses it): " + json.dumps(k, ensure_ascii=False))
    for k, v in fr.items():
        if not v.strip():
            fails.append("empty French line for " + json.dumps(k, ensure_ascii=False))
    for f in fails:
        print("FAIL", f)
    print(f"{len(keys)} keys, {len(fails)} failures")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
