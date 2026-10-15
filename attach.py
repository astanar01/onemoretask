"""Type a message into a running Claude Code session, or answer its permission prompt, the way someone at its
terminal would.

    python3 attach.py <session short id> <text>      (manual test; prints whether it was typed)

`claude attach <id>` opens the session's own input box in a terminal. Text entered there is queued like anything
typed in Claude Code: a busy session reads it at its next step, an idle one (even one waiting on a background shell)
starts a turn with it. There is no other supported way to hand text to a live session from outside. A queued
message is then pushed in at once with Claude Code's "send now" key, so it does not wait out a long command.

The board drives that terminal through a pseudo-terminal, so it needs a POSIX system (not Windows).
"""
import contextlib
import os
import re
import sys
import time

try:
    import fcntl
    import pty
    import signal
    import struct
    import termios
except ImportError:  # Windows
    pty = None

SCREEN = re.compile(r"\x1b\[[0-9;?<>=]*[A-Za-z~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[=>]")
# A permission prompt or question menu on screen: Enter would answer it, so don't type.
DIALOG = re.compile(r"Esc to cancel|Do you want to|❯\s*1\.")
# Shown while a typed message waits in the queue. Ctrl+X Ctrl+S sends it now: a running shell command moves to the
# background (not killed; Claude is told when it ends) and a reply being written stops where it is.
SEND_NOW = re.compile(r"to send now")
PASTE_START, PASTE_END = b"\x1b[200~", b"\x1b[201~"
ASK = "Do you want to"
# Notes answer_prompt returns after a key it pressed.
AGAIN = "it asks the same thing again"
ONCE = "it could only be allowed once"


def available():
    return pty is not None


def _read(fd, seconds):
    out, end = b"", time.time() + seconds
    while time.time() < end:
        import select
        r, _, _ = select.select([fd], [], [], 0.1)
        if r:
            try:
                chunk = os.read(fd, 65536)
            except OSError:
                break
            if not chunk:
                break
            out += chunk
    return out


def screen_text(raw):
    return SCREEN.sub(" ", raw.decode("utf-8", "replace"))


@contextlib.contextmanager
def _terminal(session_id, cmd):
    """`claude attach <id>` in a pseudo-terminal; yields its fd."""
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(cmd[0], [*cmd, "attach", session_id])
        finally:
            os._exit(127)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
        yield fd
    finally:
        # Leaving the terminal only closes this view; the session keeps running.
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        _read(fd, 0.5)
        _reap(pid)
        os.close(fd)


def _reap(pid, seconds=2):
    """Wait for a child told to stop; SIGKILL it if it ignores SIGTERM, so the caller never hangs."""
    end = time.time() + seconds
    try:
        while time.time() < end:
            if os.waitpid(pid, os.WNOHANG)[0]:
                return
            time.sleep(0.05)
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    except OSError:
        pass


def type_into(session_id, text, cmd=("claude",)):
    """Paste `text` into the session's input box and press Enter. Returns (typed, why-not)."""
    if not pty:
        return False, "needs a POSIX terminal"
    with _terminal(session_id, cmd) as fd:
        shown = screen_text(_read(fd, 4))
        if "❯" not in shown:
            return False, "no input box on screen: " + " ".join(shown.split())[-200:]
        if DIALOG.search(shown):
            return False, "a prompt is on screen"
        # Bracketed paste keeps newlines inside the message instead of submitting at the first one.
        body = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x1b", "").encode("utf-8")
        os.write(fd, PASTE_START + body + PASTE_END)
        shown = screen_text(_read(fd, 1))
        if DIALOG.search(shown):
            return False, "a prompt opened while pasting (left unsent in the input box)"
        os.write(fd, b"\r")
        shown = screen_text(_read(fd, 3))
        if SEND_NOW.search(shown) and not DIALOG.search(shown):
            os.write(fd, b"\x18\x13")
            _read(fd, 2)
        return True, ""


def _squash(s):
    """Text without spaces or box lines, so a command the terminal wrapped still matches its one-line form."""
    return re.sub(r"[\s│─╌]+", "", s)


def request_shown(expect, shown):
    """Whether the last permission prompt on screen asks about `expect`."""
    at = shown.rfind(ASK)
    if at < 0:
        return False
    box = re.split(r"─{20,}", shown[:at])[-1]  # the prompt box; commands above it in the chat must not count
    # state.json `needs` is cut at 800 characters and ends with "…".
    want = _squash(re.sub(r"(…|\.\.\.)\s*$", "", expect))
    return bool(want) and want in _squash(box)


def menu_options(shown):
    """The numbered choices of the last permission menu on screen: [(key, label)]."""
    at = shown.rfind(ASK)
    if at < 0:
        return []
    menu = shown[at:].split("Esc to cancel")[0]
    return [(k, " ".join(v.split())) for k, v in
            re.findall(r"(\d)\.\s+(.*?)(?=\s+(?:❯\s*)?\d\.\s|\s*$)", menu, re.S)]


def pick(options, choice):
    """The menu key for allow / always / deny, or None when the menu has no such choice."""
    for key, label in options:
        if (choice == "allow" and label == "Yes"
                or choice == "always" and label.startswith("Yes, and") and "auto mode" not in label
                or choice == "deny" and label.startswith("No")):
            return key
    return None


def answer_prompt(session_id, choice, expect, cmd=("claude",)):
    """Press the menu key for `choice` (allow / always / deny) on the permission prompt on screen, only when the
    prompt is about `expect` (the request the card showed): a key pressed on another menu would approve something
    nobody saw. Returns (answered, why): on success `why` is "" or notes joined by "; ": AGAIN (a second identical
    tool call waiting), ONCE (`always` on a menu with no "Yes, and…" choice, e.g. a plain Yes / No: Yes is pressed)."""
    if not pty:
        return False, "needs a POSIX terminal"
    with _terminal(session_id, cmd) as fd:
        shown = screen_text(_read(fd, 4))
        options = menu_options(shown)
        if not options:
            return False, "no permission prompt on screen"
        if not request_shown(expect, shown):
            return False, "the prompt on screen asks about something else"
        key = pick(options, choice)
        once = not key and choice == "always" and pick(options, "allow")
        if once:
            key = once
        if not key:
            return False, "the prompt has no such choice (it offers: " + "; ".join(label for _, label in options) + ")"
        os.write(fd, key.encode())
        shown = screen_text(_read(fd, 2))
        # A queued tool call shows its own prompt right after the key; an identical one redraws the same box.
        notes = ([AGAIN] if request_shown(expect, shown) else []) + ([ONCE] if once else [])
        return True, "; ".join(notes)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(type_into(sys.argv[1], sys.argv[2]))
