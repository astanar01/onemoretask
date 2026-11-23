"""S66: the Codex and Antigravity notes tell the session to read and follow the project's own rules files, by absolute
path, each named only when it exists.

Run: python3 tests/test_cli_notes.py  (stdlib only; a temp HOME and a temp project, never the real one)."""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TMP = os.path.realpath(tempfile.mkdtemp(prefix="cli-notes-test-"))
os.environ["HOME"] = os.path.join(TMP, "home")
os.environ["ONEMORETASK_NO_CLI_SETUP"] = "1"
os.makedirs(os.environ["HOME"])
sys.path.insert(0, os.path.dirname(HERE))
import server  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("ok   " if cond else "FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


proj = os.path.join(TMP, "proj")
os.makedirs(proj)
p = server.Project(proj)
main_rules = os.path.join(proj, "CLAUDE.md")
local_rules = os.path.join(proj, "CLAUDE.local.md")
NOTES = (("codex", lambda ao: server.codex_note(p, ao)), ("antigravity", lambda ao: server.agy_note(p, ao)))


def test_s66_neither_file():
    for cli, note in NOTES:
        for ao in (False, True):
            text = note(ao)
            check(main_rules not in text and local_rules not in text, f"S66 {cli} answer_only={ao}: no file, none named")


def test_s66_main_only():
    open(main_rules, "w").write("rules\n")
    for cli, note in NOTES:
        for ao in (False, True):
            text = note(ao)
            check(server.shell_path(main_rules) in text and "read and follow" in text and local_rules not in text,
                  f"S66 {cli} answer_only={ao}: only the main rules file named, absolute")


def test_s66_both_files():
    open(local_rules, "w").write("local rules\n")
    for cli, note in NOTES:
        for ao in (False, True):
            text = note(ao)
            check(server.shell_path(main_rules) in text and server.shell_path(local_rules) in text,
                  f"S66 {cli} answer_only={ao}: both rules files named")
    check("its AGENTS.md and CLAUDE.md apply" not in server.codex_note(p, True),
          "S66 the old answer-only phrase is gone")


if __name__ == "__main__":
    test_s66_neither_file()
    test_s66_main_only()
    test_s66_both_files()
    print("\n%d failed" % len(FAILS))
    sys.exit(1 if FAILS else 0)
