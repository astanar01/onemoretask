"""reindex-after-commit.sh must mark the CMM sentinel stale for every way a commit is written (`git -C dir commit`,
`git add -A && git commit`), and leave it alone for commands that are not a commit. Runs the real hook with HOME and
TMPDIR in a temp folder and codebase-memory-mcp kept off PATH."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "claude" / "hooks"

CASES = [
    ("git commit -m x", "stale"),
    ("git -C /some/dir commit -m x", "stale"),
    ("git add -A && git commit -m x", "stale"),
    ("git -c user.name=a commit -m x", "stale"),
    ("git log --grep commit", "ready"),
    ("echo commit", "ready"),
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
        for cmd, want in CASES:
            Path(sentinel).write_text("ready\n")
            payload = {"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": cmd},
                       "tool_response": {"stdout": "[main 1a2b3c] x"}}
            subprocess.run(["bash", str(HOOKS / "reindex-after-commit.sh")], input=json.dumps(payload), cwd=repo,
                           env=env, capture_output=True, text=True, timeout=60)
            got = Path(sentinel).read_text().strip()
            print(f"{'ok ' if got == want else 'FAIL'} {got} want={want} :: {cmd}")
            if got != want:
                fails.append(cmd)
        if fails:
            print("FAILED:", fails)
            return 1
        print("PASS test_review_hooks_commit_stale")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
