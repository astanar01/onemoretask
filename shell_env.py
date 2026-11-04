"""The user's login-shell PATH, for processes started without it.

A board opened from a desktop launcher, a login item or a scheduler (launchd, cron) gets the system's short PATH
(/usr/bin:/bin:/usr/sbin:/sbin). Claude Code then cannot start MCP servers whose command is a bare `node`, `npx` or
`uvx` (nvm, Homebrew, ~/.local/bin): they fail with "Executable not found in $PATH", and Claude Code skips that server
in every session on the machine for the next 15 minutes. Asking the user's own shell for its PATH once, at start-up,
gives every session the board or a scheduled job starts the same tools a terminal has.

Stdlib only; safe to import from a scheduled job."""
import os
import re
import subprocess

MARK = "__ONEMORETASK_PATH__"


def login_path(shell=None, timeout=15):
    """PATH as the user's interactive login shell sets it ("" on Windows, on a failure or a timeout).
    Interactive too (-i): nvm and most PATH lines live in .zshrc / .bashrc, which a login-only shell skips."""
    if os.name == "nt":
        return ""
    shell = shell or os.environ.get("SHELL") or "/bin/sh"
    if os.path.basename(shell) == "fish":   # fish's "$PATH" joins with spaces
        script = f'printf "\\n{MARK}%s{MARK}\\n" (string join : $PATH)'
    else:
        script = f'printf "\\n{MARK}%s{MARK}\\n" "$PATH"'
    try:
        r = subprocess.run([shell, "-ilc", script], capture_output=True, text=True, errors="replace",
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return ""
    m = re.search(re.escape(MARK) + r"(.*?)" + re.escape(MARK), r.stdout or "")
    return m.group(1).strip() if m else ""


def merged(first, then):
    """PATH with `first`'s folders in their order, then the folders of `then` it lacks. Empty parts dropped."""
    out = []
    for part in (first or "").split(os.pathsep) + (then or "").split(os.pathsep):
        if part and part not in out:
            out.append(part)
    return os.pathsep.join(out)


def fix_path(env=None, shell=None):
    """Put the login shell's PATH in front of env's (default os.environ). Returns the folders it added.
    ONEMORETASK_KEEP_PATH=1 leaves PATH alone (tests that pin it)."""
    env = os.environ if env is None else env
    if env.get("ONEMORETASK_KEEP_PATH"):
        return []
    login = login_path(shell)
    if not login:
        return []
    old = env.get("PATH", "")
    new = merged(login, old)
    have = set(old.split(os.pathsep))
    env["PATH"] = new
    return [p for p in new.split(os.pathsep) if p not in have]
