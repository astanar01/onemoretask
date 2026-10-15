"""cmm-grep-nudge.sh must honour `# cmm-exempt` only as the trailing comment (not inside a quoted pattern), must
treat `find … | xargs grep` as a read of what find printed, and must scan inside backticks and "$( )". Runs the
real hooks with HOME and TMPDIR in a temp folder, with the gate armed."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HOOKS = Path(__file__).resolve().parent.parent / "claude" / "hooks"

CASES = [
    # Marker placement.
    ("grep '# cmm-exempt' scripts/", 2),
    ('grep "x # cmm-exempt" scripts/', 2),
    ("grep -rn foo scripts/ # cmm-exempt", 0),
    ("grep -rn foo scripts/   # cmm-exempt  ", 0),
    ("grep -rn foo scripts/ # cmm-exempt; ls", 2),
    ("grep -rn foo scripts/ # cmm-exempt extra", 2),
    ("grep -rn foo scripts/ #cmm-exempt", 2),
    ("cat a.py # cmm-exempt", 2),
    # xargs fed by a finder.
    ("find scripts -name '*.py' | xargs grep foo", 2),
    ("find . -name '*.py' -print0 | xargs -0 grep -l foo", 2),
    ("find . -name '*.py' | xargs -n 1 cat", 2),
    ("ls scripts | xargs cat", 2),
    ("ls scripts | xargs cat # cmm-exempt", 0),
    ("find . -name '*.py' | xargs grep foo # cmm-exempt", 2),
    ("find . -name x | wc -l", 0),
    ("find . -name '*.py' | wc -l", 0),
    ("find . -name '*.md' | xargs grep foo", 0),
    ("ls | xargs echo", 0),
    # Substitutions.
    ("echo `cat a.py`", 2),
    ('echo "$(cat a.py)"', 2),
    ("x=$(head -5 a.py); echo $x", 2),
    ("echo `date`", 0),
    ('echo "$(date)"', 0),
    ("cat notes.md", 0),
]


def main():
    tmp = Path(os.path.realpath(tempfile.mkdtemp()))
    try:
        home, tdir, repo = tmp / "home", tmp / "tmp", tmp / "repo"
        (home / ".claude").mkdir(parents=True)
        tdir.mkdir()
        (home / ".claude" / "settings.json").write_text('{"mcpServers":{"codebase-memory-mcp":{}}}')
        (repo / "scripts").mkdir(parents=True)
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        env = {**os.environ, "HOME": str(home), "TMPDIR": str(tdir)}
        env.pop("CLAUDE_CONFIG_DIR", None)

        def hook(name, payload):
            payload = {"cwd": str(repo), **payload}
            return subprocess.run(["bash", str(HOOKS / name)], input=json.dumps(payload), cwd=repo, env=env,
                                  capture_output=True, text=True, timeout=60).returncode

        hook("cmm-sentinel-writer.sh", {"tool_name": "mcp__codebase-memory-mcp__index_repository"})
        assert list(tdir.glob("cmm-session-ready-*")), "sentinel not written"
        fails = []
        for cmd, want in CASES:
            got = hook("cmm-grep-nudge.sh", {"tool_name": "Bash", "tool_input": {"command": cmd}})
            print(f"{'ok ' if got == want else 'FAIL'} rc={got} want={want} :: {cmd}")
            if got != want:
                fails.append(cmd)
        if fails:
            print("FAILED:", fails)
            return 1
        print("PASS test_review_hooks_nav_bypass")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
