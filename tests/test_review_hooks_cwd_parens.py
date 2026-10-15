"""cwd-guard.sh must not treat a quoted `(` or `)` as a subshell: `echo "(" ; cd sub ; echo ")"` moves the shell.
A real `( cd x && make )` or `$( cd x; pwd )` still passes. Runs the real hook with HOME and TMPDIR in a temp
folder."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).resolve().parent.parent / "claude" / "hooks" / "cwd-guard.sh"

CASES = [
    ('echo "(" ; cd sub ; echo ")"', 2),
    ("echo '(' ; cd sub ; echo ')'", 2),
    ("echo \\( ; cd sub ; echo \\)", 2),
    ("( cd sub && make )", 0),
    ("( ( cd sub ) && ls )", 0),
    ('( echo ")" ; cd sub )', 0),
    ("x=$(cd sub; pwd); echo $x", 0),
    ('echo "$(cd sub; pwd)"', 0),
    ("( cd sub ) ; cd sub", 2),
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
        print("PASS test_review_hooks_cwd_parens")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
