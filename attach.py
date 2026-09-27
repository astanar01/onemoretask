"""Type a message into a running Claude Code session, the way someone at its terminal would.

    python3 attach.py <session short id> <text>      (manual test; prints whether it was typed)

`claude attach <id>` opens the session's own input box in a terminal. Text entered there is queued like anything
typed in Claude Code: a busy session reads it at its next step, an idle one (even one waiting on a background shell)
starts a turn with it. There is no other supported way to hand text to a live session from outside.

The board drives that terminal through a pseudo-terminal, so it needs a POSIX system (not Windows).
"""
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
PASTE_START, PASTE_END = b"\x1b[200~", b"\x1b[201~"


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


def type_into(session_id, text, cmd=("claude",)):
    """Paste `text` into the session's input box and press Enter. Returns (typed, why-not)."""
    if not pty:
        return False, "needs a POSIX terminal"
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp(cmd[0], [*cmd, "attach", session_id])
        finally:
            os._exit(127)
    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 50, 200, 0, 0))
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
        _read(fd, 2)
        return True, ""
    finally:
        # Leaving the terminal only closes this view; the session keeps running.
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        _read(fd, 0.5)
        try:
            os.waitpid(pid, 0)
        except OSError:
            pass
        os.close(fd)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    print(type_into(sys.argv[1], sys.argv[2]))
