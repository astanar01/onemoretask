"""reindex-after-commit.sh must mark the CMM sentinel stale only when a commit landed (git printed `[branch sha]`,
or a quiet commit), not when a pre-commit hook or an error rejected it. Runs the real hook with HOME and TMPDIR in a
temp folder and codebase-memory-mcp kept off PATH."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "claude" / "hooks"

CASES = [
    ("git commit -m x", "[main 1a2b3c4] x\n 1 file changed", "stale"),
    ("git commit -m x", "[main (root-commit) 1a2b3c4] x", "stale"),
    ("git commit -m x", "[detached HEAD 1a2b3c4d] x", "stale"),
    ("git -C /some/dir commit -m x", "[wtb 0123abc] x", "stale"),
    ("git commit -qm x", "", "stale"),
    ("git commit --quiet -m x", "", "stale"),
    ("git commit -m x", "", "ready"),
    ("git commit -m x", "black....Failed\n- hook id: black", "ready"),
    ("git commit -m x; echo done", "error: pathspec 'y' did not match any file(s) known to git\ndone", "ready"),
    ("git commit -m x", "On branch main\nnothing to commit, working tree clean", "ready"),
    ("git commit -m x && git push -q", "", "ready"),
]


def main():
    tmp = Path(os.path.realpath(tempfile.mkdtemp()))
    try:
        repo, tdir = tmp / "repo", tmp / "tmp"
        repo.mkdir()
        tdir.mkdir()
        (tmp / "home").mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        dirs = {os.path.dirname(shutil.which(t)) for t in ("python3", "git", "bash", "md5", "md5sum") if shutil.which(t)}
        path = os.pathsep.join(d for d in dirs | {"/usr/bin", "/bin"}
                               if not os.path.exists(os.path.join(d, "codebase-memory-mcp")))
        env = {**os.environ, "HOME": str(tmp / "home"), "TMPDIR": str(tdir), "PATH": path}
        sentinel = subprocess.run(["bash", "-c", f'. "{HOOKS}/lib/project-root.sh" && printf %s "$CMM_SENTINEL"'],
                                  cwd=repo, env=env, capture_output=True, text=True, check=True).stdout
        assert sentinel.startswith(str(tdir)), sentinel
        fails = []
        for cmd, out, want in CASES:
            Path(sentinel).write_text("ready\n")
            payload = {"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": cmd},
                       "tool_response": {"stdout": out, "stderr": ""}}
            subprocess.run(["bash", str(HOOKS / "reindex-after-commit.sh")], input=json.dumps(payload), cwd=repo,
                           env=env, capture_output=True, text=True, timeout=60)
            got = Path(sentinel).read_text().strip()
            print(f"{'ok ' if got == want else 'FAIL'} {got} want={want} :: {cmd} :: {out[:30]!r}")
            if got != want:
                fails.append((cmd, out))
        if fails:
            print("FAILED:", fails)
            return 1
        print("PASS test_review_hooks_commit_landed")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
