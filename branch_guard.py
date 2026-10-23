#!/usr/bin/env python3
"""Claude Code hook that stops a session from switching the branch of a project's main checkout.

    python3 branch_guard.py --board DIR <task_id>      (reads the hook event JSON on stdin)

server.py installs it with --settings on every session it starts, as a PreToolUse hook on Bash. Every task without
a worktree shares the main checkout, and "Merge worktree to main" needs it on its base branch, so `git switch` or a
`git checkout` of a branch there breaks the other tasks. Switching back to the repo's default branch is allowed: it
is the repair. Worktrees are left alone. A command it cannot read (an unclosed quote) is let through: this is a
guard against a habit, not a sandbox.

On an answer-only card (tasks.json "answerOnly", read on every call, so the card's tick counts as it is now) it also
refuses Edit / Write / NotebookEdit on a file inside a git work tree or the session's folder (the task-observer folders
excepted) and every git command that is not a read. Shell redirects and scripts still get through.
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
OBSERVER_DIRS = ("skill-observations", "skill-updates", ".claude/skills")  # same as server.OBSERVER_DIRS
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
# git subcommands that only read. Some read only with certain arguments: see git_writes.
GIT_READS = {"status", "log", "diff", "show", "blame", "grep", "ls-files", "ls-tree", "ls-remote", "rev-parse",
             "rev-list", "cat-file", "describe", "shortlog", "merge-base", "name-rev", "for-each-ref", "show-ref",
             "check-ignore", "whatchanged", "range-diff", "count-objects", "help", "version",
             "show-branch", "cherry", "var", "diff-tree", "diff-index", "diff-files"}


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


def git_writes(sub, args):
    """True when `git <sub> <args>` may change a repo (an answer-only card may only read)."""
    if any(a.startswith("--output") for a in args):  # git diff / log --output=<file> writes that file
        return True
    if sub in GIT_READS or sub in {"-h", "--help", "--version"}:
        return False
    names = [a for a in args if not a.startswith("-")]
    if sub == "symbolic-ref":  # one name reads it; a second name or -d sets or deletes it
        return len(names) != 1 or bool({"-d", "--delete", "-m"} & set(args))
    if sub == "branch":
        if {"-d", "-D", "--delete", "-m", "-M", "--move", "-c", "-C", "--copy", "-f", "--force", "-u",
                "--unset-upstream", "--edit-description"} & set(args) or any(a.startswith("--set-upstream") for a in args):
            return True
        return bool(names) and not {"--list", "-l", "--contains", "--no-contains", "--merged", "--no-merged",
                                    "--points-at"} & set(args)
    if sub == "tag":
        return bool(names) and not {"-l", "--list", "--contains", "--merged", "--points-at"} & set(args)
    if sub == "worktree":
        return names[:1] != ["list"]
    if sub == "stash":
        return names[:1] not in (["list"], ["show"])
    if sub == "remote":
        return bool(names) and names[0] not in {"show", "get-url"}
    if sub == "reflog":
        return bool(names) and names[0] != "show"
    if sub == "config":
        return not any(a in {"-l", "--list", "--get-regexp", "--get-all"} or a.startswith("--get") for a in args) \
            and names[:1] not in (["get"], ["list"]) and len(names) != 1
    return True


def git_segments(command, cwd):
    """(folder, subcommand, args) of every git command in `command`, following cd, -C and `bash -c`."""
    segs = list(segments(command))
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
                yield from git_segments(seg[flags[0] + 1], where)
            continue
        if os.path.basename(seg[0]) != "git":
            continue
        here, rest = where, seg[1:]
        while rest and rest[0].startswith("-") and rest[0] not in {"-h", "--help", "--version"}:
            opt = rest.pop(0)
            if opt == "-C" and rest:
                here = expand(rest.pop(0), here)
            elif opt == "-c" and rest:
                rest.pop(0)
            elif opt.startswith(("--git-dir", "--work-tree")):
                here = None  # points git somewhere else
        if rest:
            yield here, rest[0], rest[1:]


def blocked(command, cwd):
    """The main-checkout folder a branch switch in `command` would hit, else None."""
    try:
        for here, sub, args in git_segments(command, cwd):
            if here and switches(here, sub, args) and main_checkout(here):
                return git(here, "rev-parse", "--show-toplevel") or here
    except ValueError:
        pass
    return None


def git_write(command, cwd):
    """The first git command in `command` that is not a read, as text, else None."""
    try:
        for _, sub, args in git_segments(command, cwd):
            if git_writes(sub, args):
                return " ".join(["git", sub, *args])
    except ValueError:
        pass
    return None


def answer_only(board, task_id):
    try:
        with open(os.path.join(board, "tasks.json"), encoding="utf-8") as f:
            tasks = json.load(f).get("tasks") or []
    except (OSError, ValueError, AttributeError):
        return False
    return any(isinstance(t, dict) and t.get("id") == task_id and t.get("answerOnly") for t in tasks)


def edit_blocked(path, cwd):
    """True when an answer-only card may not write `path`: it sits in a git work tree or in the session's folder,
    outside that folder's task-observer folders."""
    path = os.path.realpath(os.path.join(cwd, os.path.expanduser(path)))
    near = path
    while near and not os.path.isdir(near):
        up = os.path.dirname(near)
        if up == near:
            break
        near = up
    roots = [r for r in (git(near, "rev-parse", "--show-toplevel"), cwd) if r]
    for root in roots:
        root = os.path.realpath(root)
        if path == root or path.startswith(root + os.sep):
            return not any(path.startswith(os.path.join(root, d) + os.sep) for d in OBSERVER_DIRS)
    return False


NEW_CARD = ("This card is answer-only: it never makes changes, even after a \"go ahead\". Do not try another way. "
            "Answer what you can, then post a done report saying the work needs a new card (the \"Create new task\" "
            "button on this card starts one from this answer).")


def deny(reason):
    sys.stdout.write(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": reason}}))


def main():
    args = sys.argv[1:]
    if len(args) != 3 or args[0] != "--board":
        sys.exit(__doc__)
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return
    if not isinstance(event, dict):
        return
    tool, cwd = event.get("tool_name"), event.get("cwd") or os.getcwd()
    tool_input = event.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return
    if tool in EDIT_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if path and answer_only(args[1], args[2]) and edit_blocked(path, cwd):
            deny(f"Blocked by the task board: no file edits on an answer-only card ({path}). " + NEW_CARD)
        return
    if tool != "Bash":
        return
    command = tool_input.get("command") or ""
    folder = blocked(command, cwd)
    if folder:
        deny(f"Blocked by the task board: this would switch the branch of the project's main folder ({folder}). "
             "Other tasks work in that folder and \"Merge worktree to main\" needs it on its branch, so a task must "
             "never switch or check out a branch there. Keep working on the branch it is on. If this work really "
             "needs its own branch, stop and ask the user on the card (they can tick \"Use a worktree\" and send "
             "the task again).")
        return
    write = answer_only(args[1], args[2]) and git_write(command, cwd)
    if write:
        deny(f"Blocked by the task board: an answer-only card only reads the repository ({write}). " + NEW_CARD)


if __name__ == "__main__":
    main()
