"""The server finds the Review column by its role, so renaming it (or a French name) keeps it working.

Run: python3 tests/test_column_roles.py  (stdlib only; a fresh temp HOME)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = tempfile.mkdtemp(prefix="column-roles-test-")
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.environ.pop("CLAUDE_CONFIG_DIR", None)
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

cases = [
    ("renamed, has role", [{"id": "a", "name": "To Do"}, {"id": "b", "name": "Relecture code", "role": "review"},
                           {"id": "c", "name": "Done", "done": True}], "b"),
    ("renamed to QA, has role", [{"id": "a", "name": "Review later"}, {"id": "b", "name": "QA", "role": "review"}], "b"),
    ("old board, no role", [{"id": "a", "name": "To Do"}, {"id": "b", "name": "Review"},
                            {"id": "c", "name": "Done", "done": True}], "b"),
    ("old board, renamed, no role", [{"id": "a", "name": "To Do"}, {"id": "b", "name": "Relecture code"}], None),
    ("done column never counts", [{"id": "a", "name": "Reviewed", "done": True}], None),
]
fails = 0
for name, cols, want in cases:
    got = server.review_column({c["id"]: c for c in cols}.values())
    ok = got == want
    fails += not ok
    print(("ok  " if ok else "FAIL"), name, "->", got, "" if ok else f"(want {want})")
print(f"{len(cases) - fails} passed, {fails} failed")
sys.exit(1 if fails else 0)
