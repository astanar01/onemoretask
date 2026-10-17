#!/usr/bin/env python3
"""Claude Code hook that stops a session from switching the branch of a project's main checkout.

    python3 branch_guard.py --board DIR <task_id>      (reads the hook event JSON on stdin)

server.py installs it with --settings on every session it starts, as a PreToolUse hook on Bash. Every task without
a worktree shares the main checkout, and "Merge worktree to main" needs it on its base branch, so `git switch` or a
`git checkout` of a branch there breaks the other tasks. Switching back to the repo's default branch is allowed: it
is the repair. Worktrees are left alone. A command it cannot read (an unclosed quote) is let through: this is a
guard against a habit, not a sandbox.
"""
import json
import os
import shlex
import subprocess
import sys

SEPARATORS = {";", "&&", "||", "|", "&", "\n", "(", ")"}
# Options of `git checkout` that start or move a branch whatever follows them.
BRANCH_FLAGS = {"-b", "-B", "--orphan", "-d", "--detach"}
# Options of `git switch` that make a branch or leave the named one.
SWITCH_FLAGS = {"-c", "-C", "--create", "--force-create", "--orphan", "-d", "--detach"}
SHELLS = {"bash", "sh", "zsh", "dash"}


def segments(command):
    lex = shlex.shlex(command, posix=True, punctuation_chars=";&|()\n")
    lex.whitespace = " \t\r"
    lex.whitespace_split = True
    seg = []
    for tok in lex:
        if tok in SEPARATORS or set(tok) <= set(";&|\n"):
            if seg:
                yield seg
            seg = []
        else:
            seg.append(tok)
    if seg:
        yield seg


def git(where, *args):
    try:
        r = subprocess.run(["git", "-C", where, *args], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def main_checkout(where):
    """True when `where` is inside a repo's main checkout (not a linked worktree)."""
    out = git(where, "rev-parse", "--path-format=absolute", "--git-dir", "--git-common-dir")
    if not out or len(out.splitlines()) != 2:
        return False
    own, common = out.splitlines()
    return os.path.realpath(own) == os.path.realpath(common)


def default_branch(where):
    """Same rule as server.default_branch: origin/HEAD's branch, else main, else master; local branches only."""
    for b in ((git(where, "symbolic-ref", "--short", "-q", "refs/remotes/origin/HEAD") or "").removeprefix("origin/"),
              "main", "master"):
        if b and git(where, "rev-parse", "--verify", "--quiet", f"refs/heads/{b}") is not None:
            return b
    return None


def remote_branch(where, name):
    """True when `name` is a branch on a remote, which `git checkout <name>` turns into a new local branch."""
    out = git(where, "for-each-ref", "--format=%(refname:lstrip=3)", "refs/remotes")
    return bool(out) and name in out.splitlines()


def switches(where, sub, args):
    """True when `git <sub> <args>` run in `where` would move HEAD to another branch or commit, other than back
    to the default branch."""
    if sub == "switch":
        if {"-h", "--help"} & set(args):
            return False
        names = [a for a in args if not a.startswith("-")]
        return bool(SWITCH_FLAGS & set(args)) or names != [default_branch(where)]
    if sub != "checkout":
        return False
    head = args[:args.index("--")] if "--" in args else args
    if any(a in BRANCH_FLAGS or (a.startswith("-") and not a.startswith("--") and set(a[1:]) & set("bBd"))
           for a in head):
        return True
    if "--" in args:  # `git checkout <ref> -- <paths>` restores files, HEAD stays
        return False
    if {"-p", "--patch", "-h", "--help"} & set(head):
        return False
    names = [a for a in head if not a.startswith("-") or a == "-"]
    if len(names) != 1:  # none: a no-op; several: `git checkout <ref> <paths>`, a file restore
        return False
    name = names[0]
    if name == "-":
        return True
    if os.path.exists(os.path.join(where, name)):
        return False
    if git(where, "rev-parse", "--verify", "--quiet", name + "^{commit}") is None:
        return remote_branch(where, name)
    return name != default_branch(where)


def expand(path, where):
    """`path` with ~ and $VARS filled in; $PWD is the folder the command runs in, not this hook's."""
    path = path.replace("${PWD}", where).replace("$PWD", where)
    return os.path.join(where, os.path.expanduser(os.path.expandvars(path)))


def blocked(command, cwd):
    """The main-checkout folder a branch switch in `command` would hit, else None."""
    try:
        segs = list(segments(command))
    except ValueError:
        return None
    where = cwd
    for seg in segs:
        while seg and "=" in seg[0] and not seg[0].startswith("="):  # FOO=bar git ...
            seg = seg[1:]
        if not seg:
            continue
        if seg[0] == "cd":
            where = expand(seg[1], where) if len(seg) > 1 else os.path.expanduser("~")
            continue
        if os.path.basename(seg[0]) in SHELLS:  # bash -c "git checkout x"
            flags = [i for i, a in enumerate(seg) if a.startswith("-") and not a.startswith("--") and "c" in a]
            if flags and flags[0] + 1 < len(seg):
                folder = blocked(seg[flags[0] + 1], where)
                if folder:
                    return folder
            continue
        if os.path.basename(seg[0]) != "git":
            continue
        here, rest = where, seg[1:]
        while rest and rest[0].startswith("-"):
            opt = rest.pop(0)
            if opt == "-C" and rest:
                here = expand(rest.pop(0), here)
            elif opt == "-c" and rest:
                rest.pop(0)
            elif opt.startswith(("--git-dir", "--work-tree")):
                return None  # points git somewhere else; too unusual to judge
        if rest and switches(here, rest[0], rest[1:]) and main_checkout(here):
            return git(here, "rev-parse", "--show-toplevel") or here
    return None


def main():
    args = sys.argv[1:]
    if len(args) != 3 or args[0] != "--board":
        sys.exit(__doc__)
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(event, dict) or event.get("tool_name") != "Bash":
        return
    command = (event.get("tool_input") or {}).get("command") or ""
    folder = blocked(command, event.get("cwd") or os.getcwd())
    if not folder:
        return
    reason = (f"Blocked by the task board: this would switch the branch of the project's main folder ({folder}). "
              "Other tasks work in that folder and \"Merge worktree to main\" needs it on its branch, so a task must "
              "never switch or check out a branch there. Keep working on the branch it is on. If this work really "
              "needs its own branch, stop and ask the user on the card (they can tick \"Use a worktree\" and send "
              "the task again).")
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


if __name__ == "__main__":
    main()
