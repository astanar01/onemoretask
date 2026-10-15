"""The CMM gates must find the sentinel cmm-sentinel-writer.sh wrote, from a subfolder or a git worktree of the
project too, and must not miss a source read whose pattern has a quoted `|`. Each case runs the real hooks with
HOME and TMPDIR in a temp folder."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "claude" / "hooks"


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def main():
    tmp = Path(os.path.realpath(tempfile.mkdtemp()))
    try:
        home, tdir, repo, wt = tmp / "home", tmp / "tmp", tmp / "repo", tmp / "wt"
        (home / ".claude").mkdir(parents=True)
        tdir.mkdir()
        (home / ".claude" / "settings.json").write_text('{"mcpServers":{"codebase-memory-mcp":{}}}')
        (repo / "scripts").mkdir(parents=True)
        (repo / "sub").mkdir()
        (repo / "a.py").write_text("x = 1\n")
        (repo / "scripts" / "b.py").write_text("x = 1\n")
        (repo / "sub" / "keep").write_text("")
        git("init", "-q", cwd=repo)
        git("add", "-A", cwd=repo)
        git("-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm", "i", cwd=repo)
        git("worktree", "add", "-q", str(wt), "-b", "wtb", cwd=repo)
        env = {**os.environ, "HOME": str(home), "TMPDIR": str(tdir)}
        env.pop("CLAUDE_CONFIG_DIR", None)

        def hook(name, cwd, payload):
            payload = {"cwd": str(cwd), **payload}
            return subprocess.run(["bash", str(HOOKS / name)], input=json.dumps(payload), cwd=cwd, env=env,
                                  capture_output=True, text=True, timeout=60).returncode

        def arm(cwd):
            for f in tdir.glob("cmm-*"):
                f.unlink()
            hook("cmm-sentinel-writer.sh", cwd, {"tool_name": "mcp__codebase-memory-mcp__index_repository"})
            assert list(tdir.glob("cmm-session-ready-*")), "sentinel not written"

        def bash(cwd, cmd):
            return hook("cmm-grep-nudge.sh", cwd, {"tool_name": "Bash", "tool_input": {"command": cmd}})

        fails = []

        def check(label, got, want):
            print(f"{'ok ' if got == want else 'FAIL'} {label}: rc={got} want={want}")
            if got != want:
                fails.append(label)

        arm(repo)
        check("bash: cat a.py at root blocks", bash(repo, "cat a.py"), 2)
        check("bash: cat notes.md at root allowed", bash(repo, "cat notes.md"), 0)
        check("bash: quoted pipe in pattern still blocks", bash(repo, 'grep -E "foo|bar" a.py'), 2)
        check("bash: single-quoted pipe still blocks", bash(repo, "grep 'a|b' scripts/b.py"), 2)
        check("bash: real pipe after a script run allowed", bash(repo, "python3 a.py | tail"), 0)
        check("bash: cat from a subfolder blocks", bash(repo / "sub", "cat ../a.py"), 2)

        arm(wt)
        check("bash: cat a.py in a worktree blocks", bash(wt, "cat a.py"), 2)
        check("grep tool: in a worktree blocks", hook("grep-cmm-gate.sh", wt, {
            "tool_name": "Grep", "tool_input": {"pattern": "x", "glob": "*.py", "path": str(wt)}}), 2)
        check("ctx_execute: grep in a worktree blocks", hook("ctx-execute-cmm-nudge.sh", wt, {
            "tool_name": "mcp__plugin_context-mode_context-mode__ctx_execute",
            "tool_input": {"language": "shell", "code": "grep -rn foo scripts"}}), 2)

        for f in tdir.glob("cmm-session-ready-*"):
            f.unlink()
        check("bash: no sentinel, gate stays open", bash(wt, "cat a.py"), 0)
        if fails:
            print("FAILED:", ", ".join(fails))
            return 1
        print("PASS test_review_hooks_gate_root")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
