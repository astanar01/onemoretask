"""branch_guard.py refuses a branch switch in a project's main checkout and lets everything else through,
and server.py installs it on every Claude session the board starts.

Run: python3 tests/test_branch_guard.py  (stdlib only; a temp git repo with one worktree)."""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TMP = os.path.realpath(tempfile.mkdtemp(prefix="branch-guard-"))
os.environ["HOME"] = os.path.join(TMP, "home")  # before importing server: it reads ~ at import
os.makedirs(os.environ["HOME"])
sys.path.insert(0, ROOT)
import server  # noqa: E402

main = os.path.join(TMP, "proj")
wt = os.path.join(main, ".claude", "worktrees", "w")
os.makedirs(os.path.join(main, "src"))


def git(*args, where=main):
    return subprocess.run(["git", "-C", where, *args], capture_output=True, text=True, check=True).stdout.strip()


git("init", "-q", "-b", "main")
git("config", "user.email", "t@example.com")
git("config", "user.name", "Test")
git("config", "commit.gpgsign", "false")
for name in ("a.txt", "src/b.txt"):
    with open(os.path.join(main, name), "w") as f:
        f.write("one\n")
git("add", ".")
git("commit", "-q", "-m", "Initial commit")
git("branch", "feature")
git("worktree", "add", "-q", "-b", "w-branch", wt)
git("update-ref", "refs/remotes/origin/remote-only", "HEAD")  # a branch only the remote has

fails = []


def check(name, ok):
    print(("ok  " if ok else "FAIL") + " " + name)
    if not ok:
        fails.append(name)


def guard(command, cwd=main, tool="Bash", raw=None, tool_input=None):
    stdin = raw if raw is not None else json.dumps(
        {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_input": tool_input or {"command": command},
         "cwd": cwd})
    r = subprocess.run([sys.executable, os.path.join(ROOT, "branch_guard.py"), "--board", TMP, "t1"],
                       input=stdin, capture_output=True, text=True)
    if r.returncode or "Traceback" in r.stderr:
        return "error: " + r.stderr
    if not r.stdout:
        return "allow"
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"]


denied = [
    "git checkout -b map-only-guardrail",
    "git checkout feature",
    "git checkout -",
    "git checkout -q -b x",
    "git checkout --detach",
    "git switch feature",
    "git switch -c new",
    "git status && git checkout feature",
    "git add . ; git switch feature",
    f"git -C {main} checkout feature",
    f"cd {main} && git checkout -b x",
    "cd src && git checkout feature",
    "FOO=1 git checkout feature",
    "git -c core.pager=cat switch feature",
    "git checkout feature\n",
    "echo hi\ngit switch feature",
    "git checkout remote-only",
    "git switch remote-only",
    "git switch -c main2",
    "git switch -",
    "bash -c \"git checkout -b x\"",
    "sh -lc 'cd src && git switch feature'",
    "cd \"$PWD\" && git checkout -b y",
    "cd \"${PWD}/src\" && git checkout feature",
    "git -C \"$PWD\" checkout feature",
]
for cmd in denied:
    check(f"denies in the main checkout: {cmd!r}", guard(cmd) == "deny")
check("denies a main-checkout switch run from a worktree via -C", guard(f"git -C {main} switch feature", cwd=wt) == "deny")
check("denies cd from a worktree into the main checkout", guard(f"cd {main} && git switch feature", cwd=wt) == "deny")

allowed = [
    "git status",
    "git checkout -- a.txt",
    "git checkout a.txt",
    "git checkout HEAD -- a.txt",
    "git checkout feature -- a.txt",
    "git checkout HEAD a.txt src/b.txt",
    "git checkout -p",
    "git checkout",
    "git commit -m 'never git checkout -b x; or git switch y'",
    "echo \"git switch feature\"",
    "git worktree add -b z ../z",
    "git log feature",
    "git switch --help",
    "git checkout 'unclosed",
    "ls; grep -r checkout .",
    "bash -c 'git status'",
    "bash script.sh",
]
for cmd in allowed:
    check(f"allows in the main checkout: {cmd!r}", guard(cmd) == "allow")
check("allows a switch inside a worktree", guard("git switch -c x", cwd=wt) == "allow")
check("allows checkout -b inside a worktree", guard("git checkout -b y", cwd=wt) == "allow")
check("allows a switch outside any repo", guard("git switch feature", cwd=TMP) == "allow")
check("ignores other tools", guard("git switch feature", tool="Read") == "allow")
for raw in ("", "null", "[]", "not json"):
    check(f"exits quietly on stdin {raw!r}", guard("", raw=raw) == "allow")
check("the main checkout is still on main", git("branch", "--show-current") == "main")

git("checkout", "-q", "feature")  # stuck off the default branch, like FableDraft was
for cmd in ("git checkout main", "git switch main", "git -C src checkout main", "bash -c 'git switch -q main'"):
    check(f"allows going back to the default branch: {cmd!r}", guard(cmd) == "allow")
for cmd in ("git checkout -b main2", "git switch -c main", "git checkout -", "git checkout --detach main"):
    check(f"still denies off the default branch: {cmd!r}", guard(cmd) == "deny")
git("checkout", "-q", "main")

p = server.Project(main)
s = json.loads(server.inbox_settings(p, "t1"))
pre = s["hooks"]["PreToolUse"]
check("inbox_settings installs branch_guard.py on Bash and the edit tools",
      set(pre[0]["matcher"].split("|")) == {"Bash", "Edit", "Write", "MultiEdit", "NotebookEdit"}
      and "branch_guard.py" in pre[0]["hooks"][0]["command"])
check("inbox_settings keeps inbox.py and bgIsolation none",
      "inbox.py" in s["hooks"]["Stop"][0]["hooks"][0]["command"] and s["worktree"]["bgIsolation"] == "none")
lines = " ".join(server.answer_only_lines("report"))
check("answer-only prompt: no reply lifts the rule, work goes to a new card",
      "no reply lifts the no-changes rule" in lines and "Create new task" in lines and "that reply lifts" not in lines)


# ---- answer-only card: edits and git writes refused, reads and observer files allowed
def edit(path, tool="Write", cwd=main):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return guard("", cwd=cwd, tool=tool, tool_input={key: path, "content": "x"})


def board(answer):
    with open(os.path.join(TMP, "tasks.json"), "w") as f:
        json.dump({"tasks": [{"id": "t1", "answerOnly": answer}, {"id": "t2"}]}, f)


outside = os.path.join(TMP, "scratch.txt")
board(False)
check("not answer-only: Write in the project is allowed", edit(os.path.join(main, "a.txt")) == "allow")
check("not answer-only: git commit is allowed", guard("git commit -m x") == "allow")
board(True)
for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
    check(f"answer-only: {tool} in the project is denied", edit(os.path.join(main, "a.txt"), tool) == "deny")
for path in ("src/new.py", "src/deep/new/file.py", os.path.join(wt, "a.txt")):
    check(f"answer-only: Write {path!r} is denied", edit(path) == "deny")
for d in ("skill-observations/observation-log/0001-x.md", "skill-updates/2026-10-23/x.md", ".claude/skills/s/SKILL.md"):
    check(f"answer-only: observer file {d!r} is allowed", edit(os.path.join(main, d)) == "allow")
check("answer-only: a file outside any repo is allowed", edit(outside) == "allow")
check("answer-only: Read is allowed", guard("", tool="Read", tool_input={"file_path": os.path.join(main, "a.txt")}) == "allow")
git_denied = ["git commit -m x", "git add .", "git push", "git merge feature", "git stash", "git reset --hard",
              "git branch new", "git branch -D feature", "git worktree add ../x", "git tag v1", "git restore a.txt",
              "git apply p.diff", "git rebase main", "git status && git commit -am x", f"git -C {main} add a.txt",
              "bash -c 'git commit -m x'", "git config user.name X", "git checkout a.txt", "git rm a.txt",
              "git symbolic-ref HEAD refs/heads/feature", "git symbolic-ref -d HEAD", "git symbolic-ref -m why HEAD x",
              "git diff --output=x.txt", "git log --output x.txt", "git config --unset user.name",
              "git config --global --unset user.name", "git config --unset-all x.y", "git config --remove-section core",
              "git config --rename-section core x", "git config --add x.y", "git config --edit", "git config -e"]
for cmd in git_denied:
    check(f"answer-only: denies {cmd!r}", guard(cmd) == "deny")
git_allowed = ["git status", "git log --oneline -5", "git diff HEAD~1", "git show HEAD", "git branch",
               "git branch -a", "git branch --show-current", "git worktree list", "git stash list", "git tag",
               "git remote -v", "git config --get user.name", "git config user.name", "git blame a.txt",
               "git rev-parse HEAD", "git --version", "ls; cat a.txt", "echo 'git commit'", "git symbolic-ref HEAD",
               "git symbolic-ref --short HEAD", "git symbolic-ref -q HEAD"]
for cmd in git_allowed:
    check(f"answer-only: allows {cmd!r}", guard(cmd) == "allow")
os.remove(os.path.join(TMP, "tasks.json"))
check("no tasks.json: git commit is allowed", guard("git commit -m x") == "allow")
check("symbolic-ref write really was refused: HEAD still on main", git("symbolic-ref", "HEAD") == "refs/heads/main")

# ---- answer-only card: its Claude session gets a read-only sandbox for shell commands
os.makedirs(p.board, exist_ok=True)
with open(p.data, "w") as f:
    json.dump({"tasks": [{"id": "t1", "answerOnly": True}, {"id": "t2"}]}, f)
sb = json.loads(server.inbox_settings(p, "t1")).get("sandbox") or {}
check("answer-only session: sandbox on, no way out of it",
      sb.get("enabled") is True and sb.get("allowUnsandboxedCommands") is False)
check("answer-only session: project folder is read-only",
      sb.get("filesystem", {}).get("denyWrite") == [os.path.realpath(main)])
check("answer-only session: the report command runs outside the sandbox",
      server.report_cmd(p, "t1") + " *" in sb.get("excludedCommands", []))
check("other cards: no sandbox", "sandbox" not in json.loads(server.inbox_settings(p, "t2")))
check("answer-only prompt names the read-only sandbox", "read-only" in " ".join(server.answer_only_lines("report")))
check("answer-only prompt: observer log files go through the edit tools",
      "new-observation.sh" in " ".join(server.answer_only_lines("report")))
check("answer-only prompt: a message with a backtick goes through --file",
      "report done --file <that file>" in " ".join(server.answer_only_lines("report")))
check("answer-only prompt: the report command runs on its own, nothing chained",
      "Anything chained to it" in " ".join(server.answer_only_lines("report")))
check("answer-only reply restates the rule", "no reply lifts" in server.ANSWER_ONLY_WORK)

print(f"\n{len(fails)} failed" if fails else "\nall passed")
sys.exit(1 if fails else 0)
