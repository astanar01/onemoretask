"""cwd-guard.sh must block a `cd` that really moves the shell (also inside if/then, { }, builtin) and must not
block quoted text that only mentions one, like a commit message. Runs the real hook with HOME and TMPDIR in a
temp folder."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "claude" / "hooks" / "cwd-guard.sh"

CASES = [
    ("cd sub", 2),
    ("( cd sub && ls )", 0),
    ("cd sub & wait", 0),
    ("ls 2>&1 | cat", 0),
    ("if true; then cd sub; fi", 2),
    ("{ cd sub; }", 2),
    ("builtin cd sub", 2),
    ("true && cd sub", 2),
    ('git commit -m "build && cd docs"', 0),
    ("echo 'a; cd sub'", 0),
    ('echo ")" ; cd sub', 2),
]


def main():
    tmp = Path(os.path.realpath(tempfile.mkdtemp()))
    try:
        repo = tmp / "repo"
        (repo / "sub").mkdir(parents=True)
        (tmp / "home").mkdir()
        (tmp / "tmp").mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        env = {**os.environ, "HOME": str(tmp / "home"), "TMPDIR": str(tmp / "tmp")}
        fails = []
        for cmd, want in CASES:
            payload = {"tool_name": "Bash", "cwd": str(repo), "tool_input": {"command": cmd}}
            got = subprocess.run(["bash", str(HOOK)], input=json.dumps(payload), cwd=repo, env=env,
                                 capture_output=True, text=True, timeout=60).returncode
            print(f"{'ok ' if got == want else 'FAIL'} rc={got} want={want} :: {cmd}")
            if got != want:
                fails.append(cmd)
        if fails:
            print("FAILED:", fails)
            return 1
        print("PASS test_review_hooks_cwd_guard")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
