"""Hook fixes from the p6 review. cwd-guard lets a worktree session `cd` to its own worktree (not to the main
checkout); the CMM gates stay open on a machine with no CMM server (hook matchers in settings.json do not count)
and on a `stale` sentinel; the Bash gate checks the lines after a heredoc; the Grep gate covers .gd/.mjs/.cjs and
globs with no fixed extension; `# cmm-exempt` never passes a source file in the ctx_execute and Grep gates; the
Agent gate accepts search_code and get_file_outline. Runs the real hooks with HOME and TMPDIR in a temp folder."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_DIR = Path(__file__).resolve().parent.parent
HOOKS = REPO_DIR / "claude" / "hooks"
CTX = "mcp__plugin_context-mode_context-mode__ctx_execute"


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.email=a@b", "-c", "user.name=a", *args], cwd=cwd, check=True,
                   capture_output=True)


def main():
    tmp = Path(os.path.realpath(tempfile.mkdtemp()))
    try:
        cmm_home, bare_home, tdir = tmp / "home", tmp / "bare", tmp / "tmp"
        repo, plain, wt = tmp / "repo", tmp / "plain", tmp / "wt"
        for h in (cmm_home, bare_home):
            (h / ".claude").mkdir(parents=True)
            # What the installer leaves: hook matchers naming mcp__codebase-memory-mcp__*, no server key.
            shutil.copy(REPO_DIR / "claude" / "settings-hooks.json", h / ".claude" / "settings.json")
        (cmm_home / ".claude.json").write_text('{"mcpServers":{"codebase-memory-mcp":{"command":"x"}}}')
        tdir.mkdir()
        for r in (repo, plain):
            for d in ("src", "scripts", "web"):
                (r / d).mkdir(parents=True)
            (r / "src" / "app.py").write_text("".join(f"def f{i}():\n    return {i}\n" for i in range(40)))
            (r / "scripts" / "x.gd").write_text("".join(f"func f{i}():\n\treturn {i}\n" for i in range(40)))
            (r / "notes.md").write_text("# notes\n")
            git("init", "-q", cwd=r)
            git("add", "-A", cwd=r)
            git("commit", "-qm", "i", cwd=r)
        git("worktree", "add", "-q", str(wt), "-b", "wtb", cwd=repo)
        env = {**os.environ, "TMPDIR": str(tdir)}
        env.pop("CLAUDE_CONFIG_DIR", None)

        def hook(name, cwd, payload, home=cmm_home):
            payload = {"session_id": "s1", "cwd": str(cwd), **payload}
            return subprocess.run(["bash", str(HOOKS / name)], input=json.dumps(payload), cwd=cwd,
                                  env={**env, "HOME": str(home)}, capture_output=True, text=True, timeout=60)

        def sentinel(root, state):
            (tdir / f"cmm-session-ready-{hashlib.md5(str(root).encode()).hexdigest()}").write_text(state + "\n")

        fails = []

        def check(label, p, want, text=None):
            ok = p.returncode == want and (text is None or text in p.stderr)
            print(f"{'ok ' if ok else 'FAIL'} {label}: rc={p.returncode} want={want}")
            if not ok:
                fails.append(label)
                print("   ", p.stderr.strip()[:300])

        def bash(cmd):
            return {"tool_name": "Bash", "tool_input": {"command": cmd}}

        def grep(**ti):
            return {"tool_name": "Grep", "tool_input": ti}

        def ctx(code):
            return {"tool_name": CTX, "tool_input": {"language": "shell", "code": code}}

        # 1. cwd-guard in a worktree session.
        check("cwd: worktree session may cd to its worktree", hook("cwd-guard.sh", wt, bash(f"cd {wt}")), 0)
        check("cwd: worktree cd && git log", hook("cwd-guard.sh", wt, bash(f"cd {wt} && git log -1")), 0)
        p = hook("cwd-guard.sh", wt, bash(f"cd {repo}"))
        check("cwd: worktree session may not cd to the main checkout", p, 2, f'cd "{wt}"')
        check("cwd: worktree cd into a subfolder still blocks", hook("cwd-guard.sh", wt, bash(f"cd {wt}/src")), 2)
        check("cwd: main checkout cd to root", hook("cwd-guard.sh", repo, bash(f"cd {repo}")), 0)
        check("cwd: main checkout cd src blocks", hook("cwd-guard.sh", repo, bash("cd src")), 2, f'cd "{repo}"')

        # 2. No CMM server registered: hook matchers in settings.json do not arm the gates.
        sentinel(plain, "ready")
        check("no cmm: Grep *.py open", hook("grep-cmm-gate.sh", plain, grep(pattern="def a", glob="*.py"),
                                             home=bare_home), 0)
        check("no cmm: bash grep src/app.py open", hook("cmm-grep-nudge.sh", plain, bash("grep -n f1 src/app.py"),
                                                       home=bare_home), 0)
        read = {"tool_name": "Read", "tool_input": {"file_path": str(plain / "src" / "app.py")}}
        outs = [hook("cmm-nudge.sh", plain, read, home=bare_home).stdout for _ in range(4)]
        print(f"{'ok ' if outs == [''] * 4 else 'FAIL'} no cmm: 4 reads give no nudge")
        if outs != [""] * 4:
            fails.append("no cmm: read nudge")
        check("cmm: Grep *.py blocks", hook("grep-cmm-gate.sh", plain, grep(pattern="def a", glob="*.py")), 2)
        check("cmm: bash grep src/app.py blocks", hook("cmm-grep-nudge.sh", plain, bash("grep -n f1 src/app.py")), 2)
        outs = [hook("cmm-nudge.sh", plain, {**read, "session_id": "s2"}).stdout for _ in range(4)]
        print(f"{'ok ' if 'additionalContext' in outs[3] else 'FAIL'} cmm: 4th read nudges")
        if "additionalContext" not in outs[3]:
            fails.append("cmm: read nudge")
        sentinel(plain, "stale")
        check("cmm, stale sentinel: Grep *.py open (as the Bash gate)",
              hook("grep-cmm-gate.sh", plain, grep(pattern="def a", glob="*.py")), 0)

        # 3. Heredocs: only the body is skipped.
        sentinel(repo, "ready")
        for cmd, want in [
            ("python3 - <<'E'\nprint(1)\nE\ngrep -n f1 src/app.py", 2),
            ('wc -l <<< "x"; grep -n f1 src/app.py', 2),
            ("cat <<-EOF > out.txt\n\tx\n\tEOF\ncat src/app.py", 2),
            ("cat > src/new.py <<'EOF'\ndef f(): pass\ngrep foo src/app.py\nEOF", 0),
            ("git commit -F - <<'MSG'\nfix: cat src/app.py\nMSG", 0),
            ("echo $((1<<2)); ls", 0),
        ]:
            check("heredoc: " + cmd.replace("\n", "\\n"), hook("cmm-grep-nudge.sh", repo, bash(cmd)), want)

        # 4. Grep globs and types.
        for ti, want in [({"glob": "*.gd"}, 2), ({"glob": "scripts/**/*.gd"}, 2), ({"glob": "*.mjs"}, 2),
                         ({"glob": "*.cjs"}, 2), ({"type": "gd"}, 2), ({"glob": "x*"}, 2), ({"glob": "*"}, 2),
                         ({"glob": "*.py"}, 2), ({"glob": "*.md"}, 0), ({"glob": "*.csv"}, 0), ({"type": "md"}, 0)]:
            check(f"grep tool: {ti}", hook("grep-cmm-gate.sh", repo, grep(pattern="f1", **ti)), want)

        # 5. # cmm-exempt never passes a source file.
        for code, want in [("grep -n foo src/app.py # cmm-exempt", 2), ('grep -rn "# cmm-exempt" src/', 2),
                           ("grep -n foo scripts/x.gd # cmm-exempt", 2), ("grep -rn foo src/", 2),
                           ("grep -rn foo src/ # cmm-exempt", 0), ("find src -name '*.py' # cmm-exempt", 0)]:
            check("ctx_execute: " + code, hook("ctx-execute-cmm-nudge.sh", repo, ctx(code)), want)
        for ti, want in [({"pattern": "def f1|# cmm-exempt", "glob": "*.py"}, 2),
                         ({"pattern": "f1|# cmm-exempt", "path": str(repo / "src" / "app.py")}, 2),
                         ({"pattern": "f1", "path": str(repo / "src")}, 2),
                         ({"pattern": "f1|# cmm-exempt", "path": str(repo / "src")}, 0)]:
            check(f"grep tool marker: {ti}", hook("grep-cmm-gate.sh", repo, grep(**ti)), want)

        # 7. Agent gate keywords.
        base = "Review the hook scripts under claude/hooks and report on the source of each block. " * 4
        for extra, want in [(" Use search_code for text.", 0), (" Use get_file_outline per file.", 0), ("", 2)]:
            p = hook("agent-cmm-gate.sh", repo, {"tool_name": "Agent",
                                                 "tool_input": {"prompt": base + extra, "subagent_type": "x"}})
            check(f"agent: {extra.strip() or 'no tool named'}", p, want)

        # 9. Two markers: cmm-exempt last passes both gates.
        two = "godot --headless --path . 2>&1 | grep ERROR; grep -rn foo src/ # ctx-exempt # cmm-exempt"
        check("two markers: ctx gate", hook("ctx-engine-pipe-gate.sh", repo, bash(two)), 0)
        check("two markers: cmm gate", hook("cmm-grep-nudge.sh", repo, bash(two)), 0)
        swapped = two.replace("# ctx-exempt # cmm-exempt", "# cmm-exempt # ctx-exempt")
        check("two markers, cmm not last: cmm gate blocks", hook("cmm-grep-nudge.sh", repo, bash(swapped)), 2)

        if fails:
            print("FAILED:", ", ".join(fails))
            return 1
        print("PASS test_review_hooks_gate_fixes")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
