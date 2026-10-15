"""attach.py's screen reading: the permission menu, the key for each choice, and whether the prompt asks about the
request the card showed. Pure functions over captured `claude attach` screens (tests/fixtures/attach); no terminal.

Run: python3 tests/test_attach.py  (stdlib only)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import attach  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "attach")
TMP = "/Users/demo_user/.claude/jobs/9ec2395a/tmp"  # the directory the captured commands wrote to


def screen(name):
    with open(os.path.join(FIX, name), "rb") as f:
        return attach.screen_text(f.read())


def needs(name):
    """What the server passes as `expect`: state.json `needs` without "approve " and the tool prefix."""
    with open(os.path.join(FIX, name), encoding="utf-8") as f:
        return f.read().removeprefix("approve ").removeprefix("Bash: ")


SHORT = "echo PROBE_ALLOWDENY_1 > " + TMP + "/probe1.txt"
LONG = ("echo PROBE_ALLOWDENY_LONG_COMMAND_WITH_MANY_WORDS_TO_MAKE_IT_WRAP_ACROSS_THE_TERMINAL_WIDTH_1234567890_"
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ > " + TMP + "/probe_long.txt && echo second_part_of_the_command_also_quite_long_"
        "to_push_it_past_two_hundred_characters >> " + TMP + "/probe_long.txt && cat " + TMP + "/probe_long.txt")

# menu_options
menus = {n: attach.menu_options(screen(n)) for n in
         ("bash_short_before.raw", "bash_long_before.raw", "bash_multiline_before.raw", "bash_parallel_a_before.raw")}
assert menus["bash_short_before.raw"] == [
    ("1", "Yes"), ("2", "Yes, and always allow access to " + TMP + " from this project"), ("3", "No")], menus
assert menus["bash_long_before.raw"] == [("1", "Yes"), ("2", "Yes, and don’t ask again for: echo *"), ("3", "No")], menus
assert menus["bash_multiline_before.raw"] == [
    ("1", "Yes"), ("2", "Yes, and allow access to " + TMP + " and cat commands"), ("3", "No")], menus
print("ok  menu_options reads the three menu variants")
for n in ("idle_question_text.raw", "bash_short_after_allow.raw", "bash_long_after_always.raw"):
    assert attach.menu_options(screen(n)) == [], n
print("ok  menu_options is empty with no prompt on screen (idle session, after a key)")
variable = attach.menu_options(screen("bash_variable_before.raw"))
assert variable == [("1", "Yes"), ("2", "Yes, and switch to auto mode · auto mode handles these prompts for you"),
                    ("3", "No")], variable
assert [attach.pick(variable, c) for c in ("allow", "always", "deny")] == ["1", None, "3"]
print("ok  menu_options + pick on a real 'variable can't be checked' prompt: no 'Yes, and' to always-allow")
# A boxed Yes / No menu with a line under the box, as the board reported it: "it offers: Yes |; No | | +1 file
# edited before this session (show)" (no capture of that screen).
boxed = ("│ Do you want to proceed?            │\r\n│ ❯ 1. Yes                           │\r\n"
         "│   2. No                            │\r\n╰────────────────────────────────────╯\r\n"
         "  +1 file edited before this session (show)\r\n")
for bar in ("│", "|"):
    opts = attach.menu_options(boxed.replace("│", bar))
    assert opts == [("1", "Yes"), ("2", "No")], (bar, opts)
    assert [attach.pick(opts, c) for c in ("allow", "deny")] == ["1", "2"], (bar, opts)
assert attach.menu_options("Do you want to proceed?\r ❯ 1. Yes, and don't ask again for: ls | grep x\r 2. No\r") == [
    ("1", "Yes, and don't ask again for: ls"), ("2", "No")]
print("ok  menu_options drops a box border and the text after it from a label (Allow failed on 'Yes |')")

# pick
for n, opts in menus.items():
    assert attach.pick(opts, "allow") == "1", (n, opts)
    assert attach.pick(opts, "always") == "2", (n, opts)
    assert attach.pick(opts, "deny") == "3", (n, opts)
auto = [("1", "Yes"), ("2", "Yes, and always allow access to /x from this project"), ("3", "Yes, and switch to auto mode"),
        ("4", "No")]
assert (attach.pick(auto, "always"), attach.pick(auto, "deny")) == ("2", "4")
assert attach.pick([("1", "Yes"), ("2", "No")], "always") is None
print("ok  pick: allow -> Yes, always -> the 'Yes, and' option (never auto mode), deny -> No, missing -> None")

# request_shown: the prompt matches the card's request
assert attach.request_shown(SHORT, screen("bash_short_before.raw"))
assert len("approve Bash: " + LONG) == 419 and attach.request_shown(LONG, screen("bash_long_before.raw"))
print("ok  request_shown matches a short command and a long command (419-char needs) the terminal wrapped")
ml = needs("bash_multiline.needs.txt")
assert len("approve Bash: " + ml) == 800 and ml.endswith("…"), (len(ml), ml[-10:])
assert attach.request_shown(ml, screen("bash_multiline_before.raw"))
assert attach.request_shown(ml[:-1] + "...", screen("bash_multiline_before.raw"))
print("ok  request_shown matches a multi-line command that state.json cut at 800 chars with '…' (bug 1)")
par_a, par_b = needs("bash_parallel_a.needs.txt"), needs("bash_parallel_b.needs.txt")
assert attach.request_shown(par_a, screen("bash_parallel_a_before.raw"))
print("ok  request_shown matches parallel call A on its own prompt")

# request_shown: refuses what the prompt does not ask
a_screen = screen("bash_parallel_a_before.raw")
assert par_b.split(" > ")[0] in " ".join(a_screen.split()), "fixture: B's command is in the chat above A's prompt"
assert not attach.request_shown(par_b, a_screen)
print("ok  request_shown refuses call B on A's prompt, though B's command shows in the chat above it")
assert not attach.request_shown(SHORT, screen("bash_multiline_before.raw"))
assert not attach.request_shown(ml.replace("line", "lime", 1), screen("bash_multiline_before.raw"))
assert not attach.request_shown(LONG, screen("bash_short_before.raw"))
print("ok  request_shown refuses a different command on the multi-line, short and long prompts")
for e in ("", "…", "...", "  "):
    assert not attach.request_shown(e, screen("bash_short_before.raw")), repr(e)
for n in ("idle_question_text.raw", "bash_short_after_allow.raw", "bash_long_after_always.raw"):
    assert not attach.request_shown(SHORT, screen(n)) and not attach.request_shown(LONG, screen(n)), n
print("ok  request_shown refuses an empty request and screens with no prompt")

# After the key: answer_prompt calls the request answered unless request_shown still finds it.
assert not attach.request_shown(SHORT, screen("bash_short_after_allow.raw"))
assert not attach.request_shown(LONG, screen("bash_long_after_always.raw"))
# Answering B brings up A's queued prompt at once (the capture order was A then B; either order looks the same).
assert attach.ASK in a_screen and not attach.request_shown(par_b, a_screen)
print("ok  after the key: a next queued prompt counts as answered, not 'still on screen' (bug 2)")


# answer_prompt end to end, with the terminal faked: the reads return captured screens in order, keys are recorded.
def run_answer(before, after, expect, choice="allow"):
    def raw(name):
        with open(os.path.join(FIX, name), "rb") as f:
            return f.read()
    reads, keys = [raw(before), raw(after)], []

    @attach.contextlib.contextmanager
    def fake_terminal(session_id, cmd):
        yield 99

    saved = attach._terminal, attach._read, attach.os.write
    attach._terminal, attach._read = fake_terminal, lambda fd, seconds: reads.pop(0)
    attach.os.write = lambda fd, data: keys.append(data)
    try:
        return attach.answer_prompt("x", choice, expect), keys
    finally:
        attach._terminal, attach._read, attach.os.write = saved


# Two identical parallel calls: after the key the CLI redraws the same prompt for the second one.
got, keys = run_answer("bash_parallel_a_before.raw", "bash_parallel_a_before.raw", par_a)
assert got == (True, "it asks the same thing again") and keys == [b"1"], (got, keys)
print("ok  answer_prompt: the same prompt after the key is answered, with a note (identical queued call)")
got, keys = run_answer("bash_short_before.raw", "bash_short_after_allow.raw", SHORT)
assert got == (True, "") and keys == [b"1"], (got, keys)
print("ok  answer_prompt: the prompt gone after the key is answered with no note")
got, keys = run_answer("bash_parallel_a_before.raw", "bash_parallel_a_before.raw", par_b)
assert got == (False, "the prompt on screen asks about something else") and keys == [], (got, keys)
print("ok  answer_prompt: another request on screen presses no key")

# A Yes / No prompt (no "Yes, and…" line; seen on the board, no capture): Allow always presses Yes and says so.
saved_menu = attach.menu_options
attach.menu_options = lambda shown: [("1", "Yes"), ("2", "No")]
try:
    got, keys = run_answer("bash_short_before.raw", "bash_short_after_allow.raw", SHORT, "always")
    assert got == (True, attach.ONCE) and keys == [b"1"], (got, keys)
    got, keys = run_answer("bash_parallel_a_before.raw", "bash_parallel_a_before.raw", par_a, "always")
    assert got == (True, attach.AGAIN + "; " + attach.ONCE) and keys == [b"1"], (got, keys)
    got, keys = run_answer("bash_short_before.raw", "bash_short_after_allow.raw", SHORT, "deny")
    assert got == (True, "") and keys == [b"2"], (got, keys)
finally:
    attach.menu_options = saved_menu
print("ok  answer_prompt: Allow always on a Yes / No prompt presses Yes and notes it was allowed once")
got, keys = run_answer("bash_short_before.raw", "bash_short_after_allow.raw", SHORT, "always")
assert got == (True, "") and keys == [b"2"], (got, keys)
print("ok  answer_prompt: Allow always on a full menu still presses the 'Yes, and' key")
print("PASS")
