"""The user's login-shell PATH, for processes started without it.

A board opened from a desktop launcher, a login item or a scheduler (launchd, cron) gets the system's short PATH
(/usr/bin:/bin:/usr/sbin:/sbin). Claude Code then cannot start MCP servers whose command is a bare `node`, `npx` or
`uvx` (nvm, Homebrew, ~/.local/bin): they fail with "Executable not found in $PATH", and Claude Code skips that server
in every session on the machine for the next 15 minutes. Asking the user's own shell for its PATH once, at start-up,
gives every session the board or a scheduled job starts the same tools a terminal has.

It cannot give a newer Node, though: a terminal keeps the Node it opened with (nvm never switches a running shell),
and the shell asked here starts from that one and keeps its Node too. fix_node covers that case.

Stdlib only; safe to import from a scheduled job."""
import os
import re
import shutil
import subprocess

MARK = "__ONEMORETASK_PATH__"
# context-mode 2.x needs this Node: below it its server offers only ctx_doctor and its hooks do nothing.
# setup/install_tools.py holds the same number; tests/test_session_node.py keeps the two equal.
NODE_MIN = (24, 15, 0)


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


def node_version(node, timeout=15):
    """(major, minor, patch) that `node --version` prints, or None when it does not run."""
    try:
        r = subprocess.run([node, "--version"], capture_output=True, text=True, errors="replace",
                           stdin=subprocess.DEVNULL, timeout=timeout)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", r.stdout or "")
    return tuple(int(x) for x in m.groups()) if r.returncode == 0 and m else None


def node_ok(path):
    """True when the first `node` on the PATH string `path` is new enough for context-mode."""
    node = shutil.which("node", path=path) if path else None
    version = node_version(node) if node else None
    return bool(version and version >= NODE_MIN)


def nvm_node_dir(env=None):
    """The bin folder of the newest nvm-installed Node that is new enough for context-mode, or ""."""
    env = os.environ if env is None else env
    root = os.path.join(env.get("NVM_DIR") or os.path.join(os.path.expanduser("~"), ".nvm"), "versions", "node")
    try:
        names = os.listdir(root)
    except OSError:
        return ""
    found = []
    for name in names:
        m = re.fullmatch(r"v(\d+)\.(\d+)\.(\d+)", name)
        version = tuple(int(x) for x in m.groups()) if m else None
        if version and version >= NODE_MIN and os.path.isfile(os.path.join(root, name, "bin", "node")):
            found.append((version, name))
    return os.path.join(root, max(found)[1], "bin") if found else ""


def fix_node(env=None):
    """Put a Node new enough for context-mode first on env's PATH (default os.environ) when the first `node` there
    is older or missing and nvm has a newer one. Returns the folder it put first, "" when PATH stays as it is.
    ONEMORETASK_KEEP_PATH=1 leaves PATH alone (a Node pinned on purpose, tests)."""
    env = os.environ if env is None else env
    if env.get("ONEMORETASK_KEEP_PATH"):
        return ""
    old = env.get("PATH", "")
    if node_ok(old):
        return ""
    newer = nvm_node_dir(env)
    version = node_version(os.path.join(newer, "node")) if newer else None
    if not version or version < NODE_MIN:   # the folder's name is not proof that its node runs
        return ""
    env["PATH"] = merged(newer, old)
    return newer
