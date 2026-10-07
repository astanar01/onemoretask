"""attach.py's screen reading: the permission menu, the key for each choice, and whether the prompt asks about the
request the card showed. Pure functions over captured `claude attach` screens (tests/fixtures/attach); no terminal.

Run: python3 tests/test_attach.py  (stdlib only)."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import attach  # noqa: E402

FIX = os.path.join(HERE, "fixtures", "attach")
TMP = "/Users/antoinecm/.claude/jobs/9ec2395a/tmp"  # the directory the captured commands wrote to


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
print("PASS")
