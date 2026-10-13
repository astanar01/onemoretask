"""CMM hooks under claude/hooks run the same with python3, with only `python`, and with no Python at all.

Each environment gets a fresh temp HOME, CLAUDE_CONFIG_DIR (hooks copied like setup/install_hooks.py does),
TMPDIR and a git project. Without Python every hook must exit 0 with nothing on stderr (fail open).

Run: python3 tests/test_hooks_cmm.py  (stdlib only)."""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOOKS_SRC = REPO / "claude" / "hooks"
SID = "test-session-cmm-hooks-7f3a"
# Tools the hooks call. Linked one by one so no python sneaks in from /usr/bin.
TOOLS = ["bash", "sh", "git", "cat", "grep", "egrep", "sed", "awk", "head", "tail", "tr", "cut", "dirname",
         "basename", "md5", "md5sum", "cksum", "find", "touch", "rm", "mkdir", "mv", "wc", "id", "date",
         "base64", "env", "ls", "mktemp", "sort", "uname", "xcrun"]


def link_tools(bindir, extra=()):
    bindir.mkdir(parents=True, exist_ok=True)
    for name in TOOLS:
        path = shutil.which(name)
        if path:
            (bindir / name).symlink_to(path)
    for name, target in extra:
        (bindir / name).symlink_to(target)
    return str(bindir)


class HookEnv:
    """One sandbox: HOME, CLAUDE_CONFIG_DIR, TMPDIR and a git project with a committed .py file."""

    def __init__(self, mode):
        self.root = Path(os.path.realpath(tempfile.mkdtemp(prefix="omt-hooks-")))
        self.home, self.claude, self.tmp = self.root / "home", self.root / "claude", self.root / "tmp"
        self.project = self.root / "project"
        for d in (self.home, self.tmp, self.project / "src", self.project / "sub"):
            d.mkdir(parents=True)
        shutil.copytree(HOOKS_SRC, self.claude / "hooks")
        shutil.copy(REPO / "claude" / "settings-hooks.json", self.claude / "settings.json")
        (self.claude / ".claude.json").write_text(json.dumps(
            {"mcpServers": {"codebase-memory-mcp": {"command": "codebase-memory-mcp"}}}))
        (self.project / "src" / "x.py").write_text("".join(f"def f{i}():\n    return {i}\n" for i in range(40)))
        (self.project / "README.md").write_text("# demo\n")
        git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-C", str(self.project)]
        for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "init"]):
            subprocess.run(git + args, check=True, env={**os.environ, "HOME": str(self.home)})
        if mode == "normal":
            path = os.environ["PATH"]
        elif mode == "nopython":
            path = link_tools(self.root / "bin")
        else:  # only `python`, no python3
            path = link_tools(self.root / "bin", [("python", os.path.realpath(sys.executable))])
        self.env = {"PATH": path, "HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.claude),
                    "TMPDIR": str(self.tmp), "LANG": "C"}
        self.hash = hashlib.md5(str(self.project).encode()).hexdigest()

    def run(self, hook, event, **fields):
        payload = {"session_id": SID, "cwd": str(self.project), "hook_event_name": event, **fields}
        p = subprocess.run(["bash", str(self.claude / "hooks" / hook)], input=json.dumps(payload),
                           cwd=self.project, env=self.env, capture_output=True, text=True, timeout=60)
        return p.returncode, p.stdout, p.stderr

    def files(self):
        return {p for p in self.root.rglob("*") if p.is_file() and ".git" not in p.parts}


def pre(tool, **ti):
    return ("PreToolUse", {"tool_name": tool, "tool_input": ti})


def post(tool, response=None, **ti):
    return ("PostToolUse", {"tool_name": tool, "tool_input": ti, "tool_response": response or {}})


LONG_PROMPT = "Please implement the feature in the file and fix the function. " * 8
GATE_EDIT = pre("Edit", file_path="README.md", old_string="a", new_string="b")
# (hook, (event, fields), expected exit with Python, stdout must be hook JSON?)
# Order matters: session-gate runs before the sentinel exists, the commit hook runs last.
CASES = [
    ("session-gate.sh", GATE_EDIT, 2, False),
    ("cmm-session-start.sh", ("SessionStart", {"source": "startup"}), 0, False),
    ("cmm-sentinel-writer.sh", post("mcp__codebase-memory-mcp__index_repository", {"status": "ok"}), 0, False),
    ("session-gate.sh", GATE_EDIT, 0, False),
    ("grep-cmm-gate.sh", pre("Grep", pattern="foo", path="src/x.py"), 2, False),
    ("grep-cmm-gate.sh", pre("Grep", pattern="foo", path="README.md"), 0, False),
    ("cmm-grep-nudge.sh", pre("Bash", command="grep foo src/x.py"), 2, False),
    ("cmm-grep-nudge.sh", pre("Bash", command="grep foo README.md"), 0, False),
    ("cmm-grep-nudge.sh", pre("Grep", pattern="foo", path="src/x.py"), 2, False),
    # A path counts only as an argument of the nav verb that receives it.
    ("cmm-grep-nudge.sh", pre("Bash", command="python3 /u/claudeapps/p/t.py | tail -1"), 0, False),
    ("cmm-grep-nudge.sh", pre("Bash", command="python3 src/x.py 2>&1 | tail -1"), 0, False),
    ("cmm-grep-nudge.sh", pre("Bash", command="cat /u/claudeapps/notes.txt"), 0, False),
    ("cmm-grep-nudge.sh", pre("Bash", command="cat > src/new.py <<'EOF'\ndef f(): pass\nEOF"), 0, False),
    ("cmm-grep-nudge.sh", pre("Bash", command="tail -5 src/notes.txt"), 2, False),
    ("cmm-grep-nudge.sh", pre("Bash", command="echo hi > out.txt; grep foo src/x.py"), 2, False),
    ("agent-cmm-gate.sh", pre("Agent", prompt=LONG_PROMPT, subagent_type="general-purpose"), 2, False),
    ("agent-cmm-gate.sh", pre("Agent", prompt=LONG_PROMPT + " # cmm-exempt"), 0, False),
    ("cwd-guard.sh", pre("Bash", command="cd sub"), 2, False),
    ("cwd-guard.sh", pre("Bash", command="( cd sub && ls )"), 0, False),
    ("cmm-nudge.sh", pre("Read", file_path="src/x.py"), 0, False),
    ("cmm-orient-nudge.sh", post("mcp__codebase-memory-mcp__search_graph", name_pattern="f1"), 0, True),
    ("cmm-query-stale-advisory.sh", post("mcp__codebase-memory-mcp__search_graph"), 0, False),
    ("track-cmm-calls.sh", post("mcp__codebase-memory-mcp__search_graph"), 0, False),
    ("subagent-cmm-startup.sh", ("SubagentStart", {"agent_type": "general-purpose"}), 0, True),
    ("user-prompt-submit-skill-nudge.sh", ("UserPromptSubmit", {"prompt": "where is f1 defined?"}), 0, True),
    ("reindex-after-edit.sh", post("Edit", {"filePath": "x"}, file_path="src/x.py"), 0, False),
    ("reindex-after-commit.sh", post("Bash", {"stdout": "[main 1a2b3c] x\n 1 file changed"},
                                     command="git commit -m x"), 0, False),
]


class CmmHooks(unittest.TestCase):
    def check_env(self, mode):
        e = HookEnv(mode)
        self.addCleanup(shutil.rmtree, e.root, True)
        before = e.files()
        has_py = mode != "nopython"
        probe = subprocess.run(["bash", "-c", "command -v python3 python py"], env=e.env, capture_output=True)
        self.assertEqual(probe.returncode == 0, has_py, probe.stdout)
        for hook, (event, fields), code, wants_json in CASES:
            fields = json.loads(json.dumps(fields).replace('"src/x.py"', json.dumps(str(e.project / "src/x.py")))
                                .replace('"README.md"', json.dumps(str(e.project / "README.md"))))
            with self.subTest(mode=mode, hook=hook, input=fields.get("tool_input")):
                rc, out, err = e.run(hook, event, **fields)
                if not has_py:
                    self.assertEqual((rc, err), (0, ""), out)
                    continue
                self.assertEqual(rc, code, err)
                if code == 2:
                    self.assertIn("BLOCKED", err)
                if wants_json:
                    self.assertIn("additionalContext", json.loads(out)["hookSpecificOutput"])
        sentinel = e.tmp / f"cmm-session-ready-{e.hash}"
        self.assertTrue(sentinel.exists(), "sentinel not at TMPDIR/cmm-session-ready-<md5(root)>")
        if has_py:
            self.assertEqual(sentinel.read_text().strip(), "stale")  # the commit hook ran last
        allowed = (e.tmp, e.claude, e.project, e.home / ".cache" / "codebase-memory-mcp")
        stray = [p for p in e.files() - before if not any(a in p.parents for a in allowed)]
        self.assertEqual(stray, [])
        for d in {"/tmp", os.environ.get("TMPDIR", "/tmp")}:
            leaked = [n for n in os.listdir(d) if e.hash in n or SID in n]
            self.assertEqual(leaked, [], f"hook wrote to {d}")

    def test_normal(self):
        self.check_env("normal")

    def test_no_python(self):
        self.check_env("nopython")

    def test_only_python_not_python3(self):
        self.check_env("python-only")


if __name__ == "__main__":
    unittest.main(verbosity=2)
