"""The context-mode and Muninn hooks, run as Claude Code runs them, in three PATH setups.

Run: python3 tests/test_hooks_ctx.py
Each class installs the hooks with setup/install_hooks.py into a fresh temp HOME / CLAUDE_CONFIG_DIR / TMPDIR,
makes a git project with one committed .py file, and runs `bash <installed hook>` with hook JSON on stdin.
"""
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY3 = os.path.realpath(sys.executable)
TOOLS = ("bash", "git", "cat", "grep", "sed", "awk", "cut", "tr", "head", "tail", "date", "id", "dirname",
         "basename", "mktemp", "rm", "mv", "ls", "touch", "mkdir", "md5", "md5sum", "cksum", "sort", "wc", "env",
         "uname", "pwd", "printf", "xcrun")
PLUGIN = "mcp__plugin_context-mode_context-mode__ctx_execute"
LEGACY = "mcp__context-mode__ctx_execute"


class Base(unittest.TestCase):
    PYTHON = "python3"  # "python3": normal PATH; None: no Python on PATH; "python": only `python`

    @classmethod
    def setUpClass(cls):
        cls.tmp = os.path.realpath(tempfile.mkdtemp(prefix="omt-ctx-hooks-"))
        cls.claude = os.path.join(cls.tmp, "claude")
        cls.tmpdir = os.path.join(cls.tmp, "tmp")
        cls.proj = os.path.join(cls.tmp, "proj")
        os.makedirs(cls.tmpdir)
        os.makedirs(os.path.join(cls.proj, "src"))
        base = dict(os.environ, HOME=cls.tmp, CLAUDE_CONFIG_DIR=cls.claude, TMPDIR=cls.tmpdir)
        subprocess.run([PY3, os.path.join(REPO, "setup", "install_hooks.py"), "--claude-dir", cls.claude],
                       env=base, check=True, capture_output=True)
        with open(os.path.join(cls.proj, "src", "app.py"), "w") as f:
            f.write("def foo():\n    return 1\n")
        git = ["git", "-C", cls.proj, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false"]
        subprocess.run(["git", "init", "-q", cls.proj], check=True, env=base)
        subprocess.run(git + ["add", "."], check=True, env=base)
        subprocess.run(git + ["commit", "-qm", "init"], check=True, env=base)
        # Context-mode on (plugin form) and CMM registered, as the installer leaves them.
        cls.settings = os.path.join(cls.claude, "settings.json")
        with open(cls.settings) as f:
            s = json.load(f)
        s["enabledPlugins"] = {"context-mode@context-mode": True}
        cls.write_json(cls.settings, s)
        cls.write_json(os.path.join(cls.claude, ".claude.json"),
                       {"mcpServers": {"codebase-memory-mcp": {"command": "codebase-memory-mcp"}}})
        cls.sentinel = os.path.join(cls.tmpdir, "cmm-session-ready-" + hashlib.md5(cls.proj.encode()).hexdigest())
        with open(cls.sentinel, "w") as f:
            f.write("ready\n")
        # A PATH holding only the shell tools, plus the chosen Python name.
        bindir = os.path.join(cls.tmp, "bin")
        os.makedirs(bindir)
        for t in TOOLS:
            p = shutil.which(t)
            if p:
                os.symlink(os.path.realpath(p), os.path.join(bindir, t))
        if cls.PYTHON:
            os.symlink(PY3, os.path.join(bindir, cls.PYTHON))
        cls.env = dict(base, PATH=bindir)
        cls.bash = os.path.join(bindir, "bash")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def write_json(path, data):
        with open(path, "w") as f:
            json.dump(data, f)

    def run_hook(self, name, payload):
        payload = dict({"session_id": "s1", "cwd": self.proj}, **payload)
        return subprocess.run([self.bash, os.path.join(self.claude, "hooks", name)], cwd=self.proj, env=self.env,
                              input=json.dumps(payload), capture_output=True, text=True, timeout=30)

    def ctx_execute(self, tool, code):
        return self.run_hook("ctx-execute-cmm-nudge.sh", {
            "hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {"language": "shell", "code": code}})

    def bash_cmd(self, cmd):
        return self.run_hook("ctx-engine-pipe-gate.sh", {
            "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": cmd}})

    def muninn(self):
        return self.run_hook("muninn-stop-reminder.sh", {"hook_event_name": "UserPromptSubmit", "prompt": "hi"})

    def subagent(self):
        return self.run_hook("subagent-ctx-startup.sh", {
            "hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "general-purpose"})

    def track(self):
        return self.run_hook("track-ctx-calls.sh", {
            "hook_event_name": "PostToolUse", "tool_name": "mcp__plugin_context-mode_context-mode__ctx_search",
            "tool_input": {"queries": ["x"]}, "tool_response": {"content": []}})


class WithPython(Base):
    def test_nudge_blocks_grep_under_both_tool_names(self):
        for tool in (PLUGIN, LEGACY):
            r = self.ctx_execute(tool, "grep -r foo src")
            self.assertEqual(r.returncode, 2, (tool, r.stderr))
            self.assertIn("BLOCKED", r.stderr)

    def test_nudge_allows_other_code(self):
        r = self.ctx_execute(PLUGIN, "ls -la")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_nudge_fails_open_without_sentinel(self):
        os.rename(self.sentinel, self.sentinel + ".off")
        try:
            r = self.ctx_execute(PLUGIN, "grep -r foo src")
        finally:
            os.rename(self.sentinel + ".off", self.sentinel)
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_nudge_fails_open_without_cmm(self):
        # settings.json still names mcp__codebase-memory-mcp__* in hook matchers; that must not count.
        cj = os.path.join(self.claude, ".claude.json")
        os.rename(cj, cj + ".off")
        try:
            r = self.ctx_execute(PLUGIN, "grep -r foo src")
        finally:
            os.rename(cj + ".off", cj)
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_pipe_gate(self):
        r = self.bash_cmd("godot --headless --path . -s t.gd | grep FAIL")
        self.assertEqual(r.returncode, 2, r.stderr)
        r = self.bash_cmd("ls | grep foo")
        self.assertEqual((r.returncode, r.stderr), (0, ""))

    def test_muninn_prints_json(self):
        r = self.muninn()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("muninn_recall", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_subagent_on_and_off(self):
        r = self.subagent()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("[ctx-startup]", json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"])
        with open(self.settings) as f:
            on = f.read()
        s = json.loads(on)
        s.pop("enabledPlugins")
        self.write_json(self.settings, s)
        try:
            r = self.subagent()
        finally:
            with open(self.settings, "w") as f:
                f.write(on)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, "", ""))

    def test_track_writes_counter(self):
        r = self.track()
        self.assertEqual((r.returncode, r.stderr), (0, ""))
        files = glob.glob(os.path.join(self.tmpdir, "ctx-call-counts-*.json"))
        self.assertEqual(len(files), 1, os.listdir(self.tmpdir))
        with open(files[0]) as f:
            self.assertGreaterEqual(json.load(f)["total_calls"], 1)


class NoPython(Base):
    PYTHON = None

    def test_every_hook_exits_0_silently(self):
        for name, r in (("nudge", self.ctx_execute(PLUGIN, "grep -r foo src")),
                        ("pipe-gate", self.bash_cmd("godot --headless --path . | grep FAIL")),
                        ("muninn", self.muninn()), ("subagent", self.subagent()), ("track", self.track())):
            self.assertEqual((r.returncode, r.stderr), (0, ""), name)


class OnlyPythonName(WithPython):
    PYTHON = "python"


if __name__ == "__main__":
    unittest.main(verbosity=2)
