"""setup/install_clis.py: Codex and Antigravity get the same MCP servers, rules, skills and project skills as Claude
(spec section I S55-S67, J S70-S74, K S78-S79).

Run: python3 tests/test_install_clis.py  (stdlib only). Every test runs on a temp HOME holding fake .codex, .gemini
and .claude folders (HOME and CLAUDE_CONFIG_DIR both point into it), fake codebase-memory-mcp / muninn / context-mode
binaries and a fake `agy` that records its argv and HOME. Nothing touches the real home."""
import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = REPO / "setup" / "install_clis.py"
sys.path.insert(0, str(REPO))

_MOD = []


def mod():
    """install_clis, loaded the way the server loads it (by file path)."""
    if not _MOD:
        spec = importlib.util.spec_from_file_location("install_clis", SRC)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        _MOD.append(m)
    return _MOD[0]


def h12(data):
    return hashlib.sha256(data).hexdigest()[:12]


FAKE_AGY = r'''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["FAKE_AGY_LOG"], "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "home": os.environ.get("HOME")}) + "\n")
if sys.argv[1:3] == ["plugin", "list"]:
    print(open(os.environ["FAKE_AGY_PLUGINS"]).read() if os.path.exists(os.environ["FAKE_AGY_PLUGINS"]) else "[]")
'''

# A hand-written look-alike of a real ~/.codex/config.toml: dotted keys at the top, comments, per-tool approval
# tables, hooks.state keys with quoted paths, an existing codebase-memory-mcp and context-mode, no muninn.
LOOKALIKE_TOML = '''tool_output_token_limit = 25000
tui.status_line = ["model-with-reasoning", "context-used"]
approvals_reviewer = "auto_review"

[tui.model_availability_nux]
"gpt-5.5" = 4
gpt-6-astra = 4

[mcp_servers.blender]
command = "/Users/USER/.local/bin/uvx"
args = ["--python", "3.11", "blender-mcp"]

[mcp_servers.blender.env]
DISABLE_TELEMETRY = "true"

[mcp_servers.codebase-memory-mcp]
command = "/Users/USER/.local/bin/codebase-memory-mcp"

[mcp_servers.codebase-memory-mcp.tools.search_graph]
approval_mode = "approve"

[mcp_servers.context-mode]
command = "/Users/USER/.nvm/versions/node/v24.12.0/bin/context-mode"

[mcp_servers.context-mode.env]
CONTEXT_MODE_DIR = "/Users/USER/.claude/context-mode"
CONTEXT_MODE_PLATFORM = "codex"

[sandbox_workspace_write]
# a comment about Godot
writable_roots = ["/Users/USER/Library/Caches/Godot"]
network_access = true

[projects."/Users/USER/claudeapps/mage"]
trust_level = "trusted"

[hooks.state]

[hooks.state."/Users/USER/.codex/hooks.json:pre_tool_use:0:0"]
trusted_hash = "sha256:5e8b137a8aadf32cf4c1afe2fdcf4eb2757203bddc325ad5eaffe4bc2d7e2725"

[mcp_servers.herd]
command = "herd"
args = ["mcp"]'''  # no newline at the end on purpose


def skill(folder, name, body="Say hi.\n"):
    d = Path(folder) / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: test skill {name}\n---\n\n{body}")
    return d


def git(*args, cwd):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if r.returncode:
        raise AssertionError(f"git {args}: {r.stderr}")
    return r.stdout


def new_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    git("init", "-q", cwd=path)
    (path / "README").write_text("x\n")
    git("add", "README", cwd=path)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init", cwd=path)
    return path


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-clis-test-")).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        self.claude = self.home / ".claude"
        self.codex = self.home / ".codex"
        self.gemini = self.home / ".gemini"
        self.gconf = self.gemini / "config"
        for d in (self.claude / "rules", self.codex, self.gconf):
            d.mkdir(parents=True)
        (self.claude / "CLAUDE.md").write_text("# Global\n\nWrite short and plain.\n"
                                               "Start every task with task-observer, then task-observer-extras.\n")
        (self.claude / "rules" / "muninn-rules.md").write_text("## Memory\n\nRecall first.\n")
        # Fake tools: codebase-memory-mcp and muninn in ~/.local/bin; context-mode as an npm package on PATH.
        lb = self.home / ".local" / "bin"
        lb.mkdir(parents=True)
        for name in ("codebase-memory-mcp", "muninn"):
            (lb / name).write_text("#!/bin/sh\n")
            (lb / name).chmod(0o755)
        self.pkg = self.tmp / "npm" / "lib" / "node_modules" / "context-mode"
        (self.pkg / "configs" / "antigravity-cli" / "rules").mkdir(parents=True)
        (self.pkg / "cli.bundle.mjs").write_text("#!/usr/bin/env node\n")
        (self.pkg / "cli.bundle.mjs").chmod(0o755)
        self.pkg_rule = "# context-mode routing for Antigravity CLI\n\nUse ctx tools.\n"
        (self.pkg / "configs" / "antigravity-cli" / "rules" / "context-mode.md").write_text(self.pkg_rule)
        npm_bin = self.tmp / "npm" / "bin"
        npm_bin.mkdir(parents=True)
        os.symlink(self.pkg / "cli.bundle.mjs", npm_bin / "context-mode")
        self.ctx_cmd = str(npm_bin / "context-mode")
        agy_bin = self.tmp / "agybin"
        agy_bin.mkdir()
        (agy_bin / "agy").write_text(FAKE_AGY)
        (agy_bin / "agy").chmod(0o755)
        self.agy_log = self.tmp / "agy.log"
        self.agy_plugins = self.tmp / "agy-plugins.json"
        env = {"HOME": str(self.home), "CLAUDE_CONFIG_DIR": str(self.claude),
               "PATH": f"{npm_bin}:{agy_bin}:/usr/bin:/bin:/usr/sbin:/sbin",
               "FAKE_AGY_LOG": str(self.agy_log), "FAKE_AGY_PLUGINS": str(self.agy_plugins),
               "CODEX_HOME": None, "ONEMORETASK_NO_TOOLS": None, "ONEMORETASK_NO_CLI_SETUP": None}
        for k, v in env.items():
            old = os.environ.get(k)
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None
                            else os.environ.__setitem__(k, old))
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    # helpers
    def main(self, *extra):
        m = mod()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(m.main(["--home", str(self.home), *extra]), 0)
        return out.getvalue()

    def ensure(self, cli):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            mod().ensure_rules(cli, home=str(self.home))
        return out.getvalue()

    def state(self, *roots):
        """{path: (bytes or link text, mtime_ns)} for everything under roots."""
        res = {}
        for root in roots:
            for p in sorted(Path(root).rglob("*")):
                st = p.lstat()
                if stat.S_ISLNK(st.st_mode):
                    res[str(p)] = ("link:" + os.readlink(p), st.st_mtime_ns)
                elif p.is_file():
                    res[str(p)] = (p.read_bytes(), st.st_mtime_ns)
        return res

    def agy_calls(self):
        if not self.agy_log.exists():
            return []
        return [json.loads(l) for l in self.agy_log.read_text().splitlines()]

    def mcp(self):
        return json.loads((self.gconf / "mcp_config.json").read_text())["mcpServers"]


class SectionI(Base):
    def test_s55_second_run_writes_nothing(self):
        (self.codex / "config.toml").write_text(LOOKALIKE_TOML)
        self.main()
        before = self.state(self.codex, self.gemini)
        time.sleep(0.02)
        self.main()
        self.assertEqual(self.state(self.codex, self.gemini), before)

    def test_s55_symlinked_file_written_through_with_mode_and_backup(self):
        dot = self.tmp / "dotfiles"
        dot.mkdir()
        real = dot / "mcp.json"
        real.write_text('{"mcpServers": {"other": {"command": "x"}}}\n')
        real.chmod(0o600)
        os.symlink(real, self.gconf / "mcp_config.json")
        self.main()
        self.assertTrue((self.gconf / "mcp_config.json").is_symlink())
        self.assertEqual(stat.S_IMODE(real.stat().st_mode), 0o600)
        data = json.loads(real.read_text())["mcpServers"]
        self.assertIn("muninn", data)
        self.assertEqual(data["other"], {"command": "x"})
        bak = self.gconf / "mcp_config.json.bak-onemoretask"
        self.assertEqual(json.loads(bak.read_text()), {"mcpServers": {"other": {"command": "x"}}})

    def test_s56_agy_mcp_entries(self):
        (self.gconf / "mcp_config.json").write_text("")  # the user's today: an empty file
        self.main()
        servers = self.mcp()
        self.assertEqual(servers["codebase-memory-mcp"]["command"], str(self.home / ".local/bin/codebase-memory-mcp"))
        self.assertNotIn("args", servers["codebase-memory-mcp"])
        self.assertEqual(servers["muninn"], {"command": str(self.home / ".local/bin/muninn"), "args": ["mcp"]})

    def test_s56_existing_entry_kept_missing_binary_noted_bad_file_left(self):
        (self.gconf / "mcp_config.json").write_text(json.dumps({"mcpServers": {"muninn": {"command": "mine"}}}))
        (self.home / ".local/bin/codebase-memory-mcp").unlink()
        out = self.main()
        servers = self.mcp()
        self.assertEqual(servers["muninn"], {"command": "mine"})
        self.assertNotIn("codebase-memory-mcp", servers)
        self.assertIn("Note", out)
        (self.gconf / "mcp_config.json").write_text("[1, 2]")
        out = self.main()
        self.assertEqual((self.gconf / "mcp_config.json").read_text(), "[1, 2]")
        self.assertIn("Note", out)

    def test_s57_codex_toml_lookalike_gains_only_muninn(self):
        cfg = self.codex / "config.toml"
        cfg.write_text(LOOKALIKE_TOML)
        self.main()
        new = cfg.read_text()
        self.assertTrue(new.startswith(LOOKALIKE_TOML + "\n"), "the old text must stay as it was, at the top")
        data = tomllib.loads(new)
        self.assertEqual(data["mcp_servers"]["muninn"], {"command": str(self.home / ".local/bin/muninn"),
                                                          "args": ["mcp"]})
        # existing entries byte for byte (S58), nothing else appended
        self.assertEqual(new.count("[mcp_servers.context-mode]"), 1)
        self.assertEqual(new.count("[mcp_servers.codebase-memory-mcp]"), 1)
        self.assertNotIn("tools.muninn", new)
        added = new[len(LOOKALIKE_TOML):]
        self.assertEqual(added.count("[mcp_servers."), 1)
        self.main()
        self.assertEqual(cfg.read_text(), new)

    def test_s57_empty_config_gets_all_three(self):
        cfg = self.codex / "config.toml"
        cfg.write_text("# mine\nmodel = \"x\"\n")
        self.main()
        data = tomllib.loads(cfg.read_text())["mcp_servers"]
        self.assertEqual(set(data), {"muninn", "codebase-memory-mcp", "context-mode"})
        self.assertEqual(data["context-mode"]["command"], self.ctx_cmd)
        self.assertEqual(data["context-mode"]["env"], {"CONTEXT_MODE_DIR": str(self.claude / "context-mode"),
                                                       "CONTEXT_MODE_PLATFORM": "codex"})
        self.assertTrue(cfg.read_text().startswith("# mine\nmodel = \"x\"\n"))

    def test_s57_inline_table_and_dotted_key_count_as_present(self):
        cfg = self.codex / "config.toml"
        text = ('mcp_servers.codebase-memory-mcp.command = "a"\n'
                '[mcp_servers]\nmuninn = { command = "m", args = ["mcp"] }\n'
                '"context-mode" = { command = "c" }\n')
        cfg.write_text(text)
        self.main()
        self.assertEqual(cfg.read_text(), text)

    def test_s57_unparsable_toml_left_alone(self):
        cfg = self.codex / "config.toml"
        cfg.write_text("this is = = not toml\n")
        out = self.main()
        self.assertEqual(cfg.read_text(), "this is = = not toml\n")
        self.assertIn("Note", out)

    def test_s58_codex_hooks_and_context_mode_entry_untouched(self):
        hooks = self.codex / "hooks.json"
        hooks.write_text('{"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": "x"}]}]}}\n')
        (self.codex / "hooks").mkdir()
        (self.codex / "hooks" / "cmm-session-start.py").write_text("print(1)\n")
        cfg = self.codex / "config.toml"
        cfg.write_text(LOOKALIKE_TOML)
        before = self.state(self.codex / "hooks")
        hb = hooks.read_bytes()
        self.main()
        self.assertEqual(hooks.read_bytes(), hb)
        self.assertEqual(self.state(self.codex / "hooks"), before)
        self.assertIn('command = "/Users/USER/.nvm/versions/node/v24.12.0/bin/context-mode"', cfg.read_text())

    def test_s59_agy_context_mode_entry_and_rule(self):
        self.main()
        entry = self.mcp()["context-mode"]
        self.assertEqual(entry, {"command": self.ctx_cmd,
                                 "env": {"CONTEXT_MODE_PLATFORM": "antigravity-cli",
                                         "CONTEXT_MODE_DIR": str(self.claude / "context-mode")}})
        rule = (self.gconf / "rules" / "context-mode.md").read_text()
        self.assertTrue(rule.startswith("---\ntrigger: always_on\n---\n"))
        self.assertTrue(rule.endswith(self.pkg_rule))
        calls = self.agy_calls()
        self.assertTrue(calls, "the full step asks agy which plugins are installed")
        self.assertEqual(calls[0]["argv"], ["plugin", "list", "--json"])
        self.assertEqual(calls[0]["home"], str(self.home))
        self.assertFalse(any(c["argv"][:2] == ["plugin", "install"] for c in calls))

    def test_s59_installed_plugin_skips_entry_and_rule(self):
        plug = self.gconf / "plugins" / "context-mode"
        plug.mkdir(parents=True)
        self.agy_plugins.write_text(json.dumps([{"name": "context-mode", "enabled": True, "path": str(plug)}]))
        self.main()
        self.assertNotIn("context-mode", self.mcp())
        self.assertFalse((self.gconf / "rules" / "context-mode.md").exists())
        shutil.rmtree(plug)  # an npm upgrade or uninstall left the listed folder gone
        self.main()
        self.assertIn("context-mode", self.mcp())

    def test_s59_user_rule_file_kept(self):
        (self.gconf / "rules").mkdir()
        (self.gconf / "rules" / "context-mode.md").write_text("mine\n")
        out = self.main()
        self.assertEqual((self.gconf / "rules" / "context-mode.md").read_text(), "mine\n")
        self.assertIn("Note", out)

    def test_s59_ensure_rules_never_calls_agy(self):
        self.ensure("antigravity")
        self.ensure("codex")
        self.assertEqual(self.agy_calls(), [])

    def fake_repo(self, note=b"You are Codex.\n"):
        repo = self.tmp / "repo"
        (repo / "clis" / "agy-hooks").mkdir(parents=True)
        (repo / "clis" / "codex.md").write_bytes(note)
        (repo / "clis" / "antigravity.md").write_bytes(b"You are Antigravity.\n")
        shutil.copy(REPO / "clis" / "agy-hooks" / "cmm-session-start.py",
                    repo / "clis" / "agy-hooks" / "cmm-session-start.py")
        m = mod()
        self.addCleanup(setattr, m, "REPO", m.REPO)
        m.REPO = repo
        return repo

    def expected(self, cli, left_out=()):
        repo = mod().REPO
        srcs = [(f"onemoretask/clis/{cli}.md", (repo / "clis" / f"{cli}.md").read_bytes()),
                ("~/.claude/CLAUDE.md", (self.claude / "CLAUDE.md").read_bytes())]
        for p in sorted((self.claude / "rules").glob("*.md")):
            if not p.name.startswith("."):
                srcs.append((f"~/.claude/rules/{p.name}", p.read_bytes()))
        labels = [l for l, _ in srcs]
        sh = h12(b"".join(b for _, b in srcs))
        body = "\n" + srcs[0][1].decode().rstrip("\n") + "\n"
        if left_out:
            body += "\n" + "".join(f"Not included because of size: read {l} yourself before starting.\n"
                                   for l in left_out)
        for label, data in srcs[1:]:
            if label in left_out:
                continue
            body += f"\n## From {label}\n\n" + data.decode().rstrip("\n") + "\n"
        marker = (f"<!-- Made by the task board from {', '.join(labels)} (sources sha256 {sh}, "
                  f"body sha256 {h12(body.encode())}). Edit those files, not this one. -->")
        return marker + "\n" + body

    def test_s60_exact_rules_text_both_clis(self):
        self.fake_repo()
        (self.claude / "rules" / "a-first.md").write_text("A rule.\n\n\n")
        (self.claude / "rules" / ".hidden.md").write_text("never\n")
        (self.claude / "rules" / "notes.txt").write_text("not a rule\n")
        self.ensure("codex")
        self.ensure("antigravity")
        self.assertEqual((self.codex / "AGENTS.md").read_text(), self.expected("codex"))
        self.assertEqual((self.gconf / "AGENTS.md").read_text(), self.expected("antigravity"))
        self.assertNotIn("never", (self.codex / "AGENTS.md").read_text())

    def test_s60_codex_home_env_and_missing_source(self):
        self.fake_repo()
        other = self.tmp / "codexhome"
        other.mkdir()
        os.environ["CODEX_HOME"] = str(other)
        (self.claude / "CLAUDE.md").unlink()
        self.ensure("codex")
        text = (other / "AGENTS.md").read_text()
        self.assertNotIn("CLAUDE.md", text.splitlines()[0])
        self.assertIn("## From ~/.claude/rules/muninn-rules.md", text)
        self.assertFalse((self.codex / "AGENTS.md").exists())

    def test_s61_notes_cover_a_to_f(self):
        for cli, own, tools in (("codex", "Codex", ("exec_command", "apply_patch", "spawn_agent")),
                                ("antigravity", "Antigravity", ("run_command", "view_file", "replace_file_content",
                                                                "write_to_file", "grep_search", "invoke_subagent"))):
            note = (REPO / "clis" / f"{cli}.md").read_text()
            self.assertIn(f"You are {own}", note)                            # (a)
            self.assertIn("not Claude Code", note)
            for word in ("Bash", "Read", "Edit", "Write", "Grep", "Glob", "Skill", "Agent", "ToolSearch") + tools:
                self.assertIn(word, note, f"{cli}: tool table lacks {word}")  # (b)
            self.assertIn("does not apply to you", note.lower())             # (c)
            for word in ("cmm-grep-nudge.sh", "agent-cmm-gate.sh", "# cmm-exempt", "ToolSearch", "Skill tool",
                         "auto-memory", "deferred tools", "recall Muninn yourself"):
                self.assertIn(word, note, f"{cli}: (c) lacks {word}")
            self.assertIn("1.x", note)                                         # (d)
            self.assertIn("ctx_batch_execute", note)
            self.assertIn("~/.claude/skills/<name>/SKILL.md", note)           # (e)
            self.assertIn("<project>/.claude/skills/<name>/SKILL.md", note)
            self.assertIn("CLAUDE.md", note)                                   # (f)
            self.assertIn("CLAUDE.local.md", note)
            self.assertFalse(note.startswith("#"), "no heading: the rules file adds none either")

    def test_s61_notes_name_every_hook_the_shipped_rules_name(self):
        hooks = {p.name for p in (REPO / "claude" / "hooks").iterdir()}
        named = set()
        for rule in (REPO / "claude" / "rules").glob("*.md"):
            text = rule.read_text()
            named |= {h for h in hooks if h in text}
            for word in ("ToolSearch", "Skill tool"):
                if word in text:
                    named.add(word)
        self.assertTrue(named)
        for cli in ("codex", "antigravity"):
            note = (REPO / "clis" / f"{cli}.md").read_text()
            for name in named:
                self.assertIn(name, note, f"clis/{cli}.md does not list {name} as not applying")

    def test_s62_size_cap_leaves_largest_rules_out(self):
        self.fake_repo()
        (self.claude / "rules" / "big.md").write_text("b" * 15000 + "\n")
        (self.claude / "rules" / "bigger.md").write_text("c" * 16000 + "\n")
        out = self.ensure("antigravity")
        data = (self.gconf / "AGENTS.md").read_bytes()
        self.assertLessEqual(len(data), 24000)
        self.assertEqual(data.decode(), self.expected("antigravity", left_out=("~/.claude/rules/bigger.md",)))
        self.assertIn("bigger.md", out)
        self.ensure("codex")
        self.assertLessEqual(len((self.codex / "AGENTS.md").read_bytes()), 24000)

    def test_s63_takeover_three_cases(self):
        self.fake_repo()
        target = self.codex / "AGENTS.md"
        before = self.codex / "AGENTS.md.before-onemoretask"
        bak = self.codex / "AGENTS.md.bak-onemoretask"
        target.write_text("my own rules\n")
        self.ensure("codex")  # ensure_rules never takes a hand file over
        self.assertEqual(target.read_text(), "my own rules\n")
        out = self.main()
        self.assertEqual(before.read_text(), "my own rules\n")
        self.assertEqual(target.read_text(), self.expected("codex"))
        self.assertIn("before-onemoretask", out)
        target.write_text("hand again\n")  # replaced by hand again later
        self.main()
        self.assertEqual(before.read_text(), "my own rules\n")
        self.assertEqual(bak.read_text(), "hand again\n")
        self.assertEqual(target.read_text(), self.expected("codex"))
        edited = target.read_text() + "a line added by hand\n"  # marker kept, body changed
        target.write_text(edited)
        out = self.main()
        self.assertEqual(bak.read_text(), edited)
        self.assertEqual(target.read_text(), self.expected("codex"))
        self.assertIn("Note", out)

    def test_s64_each_change_rebuilds_and_nothing_changed_is_cheap(self):
        self.fake_repo()
        target = self.codex / "AGENTS.md"
        rules = self.claude / "rules"
        self.ensure("codex")
        for change in (lambda: (rules / "muninn-rules.md").write_text("## Memory\n\nRecall twice.\n"),
                       lambda: (rules / "new.md").write_text("New rule.\n"),
                       lambda: (rules / "new.md").rename(rules / "renamed.md"),
                       lambda: (rules / "renamed.md").unlink(),
                       lambda: (mod().REPO / "clis" / "codex.md").write_text("You are Codex, v2.\n")):
            change()
            self.ensure("codex")
            self.assertEqual(target.read_text(), self.expected("codex"))
        st = target.stat().st_mtime_ns
        t = time.monotonic()
        self.ensure("codex")
        self.assertLess(time.monotonic() - t, 0.05)
        self.assertEqual(target.stat().st_mtime_ns, st)

    def test_s64_unwritable_target_raises_nothing(self):
        self.fake_repo()
        self.codex.chmod(0o500)
        self.addCleanup(self.codex.chmod, 0o755)
        self.ensure("codex")  # no exception
        self.assertFalse((self.codex / "AGENTS.md").exists())

    def test_s64_missing_cli_folder_and_opt_outs_do_nothing(self):
        shutil.rmtree(self.codex)
        shutil.rmtree(self.gemini)
        self.ensure("codex")
        self.ensure("antigravity")
        self.main()
        self.assertFalse(self.codex.exists())
        self.assertFalse(self.gemini.exists())
        self.codex.mkdir()
        self.gconf.mkdir(parents=True)
        for var in ("ONEMORETASK_NO_TOOLS", "ONEMORETASK_NO_CLI_SETUP"):
            os.environ[var] = "1"
            self.ensure("codex")
            self.main()
            self.assertEqual(self.state(self.codex, self.gemini), {}, var)
            self.assertEqual(self.agy_calls(), [])
            del os.environ[var]

    def test_s65_global_skill_links(self):
        sk = self.claude / "skills"
        skill(sk, "alpha")
        elsewhere = skill(self.tmp / "dotskills", "linked-skill")
        os.symlink(elsewhere, sk / "linked-skill")
        (sk / "synced").mkdir()                    # a folder without SKILL.md
        skill(sk, ".hidden")
        (sk / ".DS_Store").write_text("x")
        cs = self.codex / "skills"
        (cs / ".system" / "x").mkdir(parents=True)
        skill(cs, "alpha-own")
        (cs / "alpha").mkdir()                     # a real folder with a skill's name: never touched
        os.symlink(sk / "gone", cs / "gone")       # into the Claude skills, target gone: removed
        os.symlink(self.tmp / "nowhere", cs / "foreign-gone")  # outside: kept
        self.main()
        for dest in (cs, self.gconf / "skills"):
            self.assertEqual(os.readlink(dest / "linked-skill"), str(sk / "linked-skill"))
            self.assertFalse((dest / "synced").exists())
            self.assertFalse(os.path.lexists(dest / ".hidden"))
            self.assertFalse(os.path.lexists(dest / ".DS_Store"))
        self.assertEqual(os.readlink(self.gconf / "skills" / "alpha"), str(sk / "alpha"))
        self.assertFalse((cs / "alpha").is_symlink())
        self.assertTrue((cs / ".system" / "x").is_dir())
        self.assertFalse(os.path.lexists(cs / "gone"))
        self.assertTrue(os.path.lexists(cs / "foreign-gone"))

    def test_s67_agy_session_start_hook(self):
        hooks = self.gconf / "hooks.json"
        hooks.write_text(json.dumps({"mine": {"Stop": [{"type": "command", "command": "true"}]}}))
        self.main()
        copy = self.gconf / "hooks" / "onemoretask-cmm-session-start.py"
        self.assertEqual(copy.read_bytes(), (REPO / "clis" / "agy-hooks" / "cmm-session-start.py").read_bytes())
        data = json.loads(hooks.read_text())
        self.assertEqual(data["mine"], {"Stop": [{"type": "command", "command": "true"}]})
        self.assertEqual(data["onemoretask-cmm"], {"SessionStart": [{"type": "command", "command": f"python3 {copy}"}]})
        hooks.write_text("not json")
        out = self.main()
        self.assertEqual(hooks.read_text(), "not json")
        self.assertIn("Note", out)

    def test_s67_hook_script_prints_inject_steps(self):
        script = REPO / "clis" / "agy-hooks" / "cmm-session-start.py"
        r = subprocess.run([sys.executable, str(script)], input="{}", capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        msg = json.loads(r.stdout)["injectSteps"][0]["systemMessage"]["systemMessage"]
        for word in ("index", "context-mode", "Muninn"):
            self.assertIn(word, msg)
        self.assertNotIn("/Users/", script.read_text())

    def test_s78_observer_skills_linked_and_rule_carried(self):
        sk = self.claude / "skills"
        skill(sk, "task-observer")
        skill(sk, "task-observer-extras")
        self.main()
        for dest in (self.codex / "skills", self.gconf / "skills"):
            for name in ("task-observer", "task-observer-extras"):
                self.assertTrue((dest / name / "SKILL.md").is_file(), f"{dest}/{name}")
        for f in (self.codex / "AGENTS.md", self.gconf / "AGENTS.md"):
            self.assertIn("task-observer, then task-observer-extras", f.read_text())

    def test_s79_notes_say_how_to_run_the_observer(self):
        for cli in ("codex", "antigravity"):
            note = (REPO / "clis" / f"{cli}.md").read_text()
            for words in ("task-observer", "SKILL.md", "bash", "scripts/new-observation.sh <slug> <workspace root>",
                          "hooks"):
                self.assertIn(words, note, f"{cli}: {words}")

    def test_setup_modules_import_no_repo_root_module(self):
        tree = ast.parse(SRC.read_text())
        top = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                top |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom):
                top.add((node.module or "").split(".")[0])
        self.assertFalse(top & {"agy", "codex", "server", "reports", "tomllib"}, top)


class SectionJ(Base):
    def project(self, root=None):
        p = root or self.tmp / "proj"
        sk = p / ".claude" / "skills"
        skill(sk, "alpha")
        skill(p / "shared-skills", "beta")
        os.symlink("../../shared-skills/beta", sk / "beta")          # a skill that is itself a link
        (sk / "synced").mkdir()                                     # no SKILL.md
        skill(sk, ".dot")
        # Laravel Boost shape (as in a real project): the skill lives in .agents/skills and .claude/skills links to it
        boost = skill(p / ".agents" / "skills", "boost-x", body="Boost.\n")
        os.symlink("../../.agents/skills/boost-x", sk / "boost-x")
        # clashes
        skill(sk, "clash-real", body="Ours.\n")
        skill(p / ".agents" / "skills", "clash-real", body="Theirs.\n")
        skill(sk, "clash-file")
        (p / ".agents" / "skills" / "clash-file").write_text("a file\n")
        skill(sk, "clash-link")
        os.symlink(self.tmp / "elsewhere", p / ".agents" / "skills" / "clash-link")
        self.assertTrue(boost.is_dir())
        return p

    def eps(self, project, target=None):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            res = mod().ensure_project_skills(str(project), None if target is None else str(target))
        return res, out.getvalue()

    def test_s70_links_and_clashes(self):
        p = self.project()
        (linked, kept), _ = self.eps(p)
        ag = p / ".agents" / "skills"
        self.assertEqual(os.readlink(ag / "alpha"), "../../.claude/skills/alpha")
        self.assertEqual(os.readlink(ag / "beta"), "../../.claude/skills/beta")
        self.assertTrue((ag / "beta" / "SKILL.md").is_file())
        self.assertEqual(sorted(linked), ["alpha", "beta"])
        self.assertEqual(sorted(kept), ["clash-file", "clash-link", "clash-real"])  # boost-x: same SKILL.md
        self.assertTrue((ag / "boost-x").is_dir() and not (ag / "boost-x").is_symlink())
        self.assertEqual((ag / "clash-file").read_text(), "a file\n")
        self.assertEqual(os.readlink(ag / "clash-link"), str(self.tmp / "elsewhere"))
        self.assertFalse(os.path.lexists(ag / "synced"))
        self.assertFalse(os.path.lexists(ag / ".dot"))

    def test_s70_agents_link_skipped_and_no_skills_creates_nothing(self):
        p = self.tmp / "p1"
        skill(p / ".claude" / "skills", "alpha")
        (self.tmp / "realagents").mkdir()
        os.symlink(self.tmp / "realagents", p / ".agents")
        (res, out) = self.eps(p)
        self.assertEqual(res, ([], []))
        self.assertIn("Note", out)
        self.assertEqual(list((self.tmp / "realagents").iterdir()), [])
        q = self.tmp / "p2"
        q.mkdir()
        self.assertEqual(self.eps(q)[0], ([], []))
        self.assertFalse((q / ".agents").exists())

    def test_s70_other_target_absolute_unless_it_has_its_own_copy(self):
        p = self.tmp / "p"
        skill(p / ".claude" / "skills", "alpha")
        skill(p / ".claude" / "skills", "uncommitted")
        t = self.tmp / "answer" / "t1"                   # does not exist yet: made with parents
        (linked, _), _ = self.eps(p, t)
        self.assertEqual(os.readlink(t / ".agents" / "skills" / "alpha"), str(p / ".claude" / "skills" / "alpha"))
        self.assertEqual(sorted(linked), ["alpha", "uncommitted"])
        wt = self.tmp / "wt"
        skill(wt / ".claude" / "skills", "alpha")        # a worktree with the committed copy
        self.eps(p, wt)
        self.assertEqual(os.readlink(wt / ".agents" / "skills" / "alpha"), "../../.claude/skills/alpha")
        self.assertEqual(os.readlink(wt / ".agents" / "skills" / "uncommitted"),
                         str(p / ".claude" / "skills" / "uncommitted"))

    def test_s71_stale_board_links_removed_opt_out_removes_all(self):
        p = self.project()
        ag = p / ".agents" / "skills"
        self.eps(p)
        shutil.rmtree(p / ".claude" / "skills" / "alpha")
        (linked, _), _ = self.eps(p)
        self.assertFalse(os.path.lexists(ag / "alpha"))
        self.assertIn("beta", linked)                    # a board link to a skill that is a link stays ours
        self.assertTrue(os.path.islink(ag / "beta"))
        os.environ["ONEMORETASK_NO_CLI_SETUP"] = "1"
        self.assertEqual(self.eps(p)[0], ([], []))
        self.assertFalse(os.path.lexists(ag / "beta"))
        self.assertTrue(os.path.islink(ag / "clash-link"))  # never ours: kept
        self.assertTrue((ag / "boost-x").is_dir())

    def check_git(self, repo, project, target=None):
        status = git("status", "--porcelain", "--untracked-files=all", cwd=repo)
        (linked, _), _ = self.eps(project, target)
        t = Path(target or project)
        self.assertIn("alpha", linked)
        self.assertTrue((t / ".agents" / "skills" / "alpha" / "SKILL.md").is_file())
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=repo), status)
        self.eps(project, target)
        excl = Path(git("rev-parse", "--git-path", "info/exclude", cwd=t).strip())
        excl = excl if excl.is_absolute() else t / excl
        lines = [l for l in excl.read_text().splitlines() if l.strip() and not l.startswith("#")]
        self.assertEqual(len(lines), len(set(lines)), lines)
        return lines

    def test_s72_project_at_repo_top(self):
        repo = new_repo(self.tmp / "top")
        skill(repo / ".claude" / "skills", "alpha")
        git("add", ".claude", cwd=repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "skills", cwd=repo)
        lines = self.check_git(repo, repo)
        self.assertIn("/.agents/skills/alpha", lines)

    def test_s72_project_in_subfolder(self):
        repo = new_repo(self.tmp / "mono")
        proj = repo / "apps" / "web"
        skill(proj / ".claude" / "skills", "alpha")
        lines = self.check_git(repo, proj)
        self.assertIn("/apps/web/.agents/skills/alpha", lines)

    def test_s72_worktree_target_and_no_git_call_when_nothing_changes(self):
        repo = new_repo(self.tmp / "main")
        skill(repo / ".claude" / "skills", "alpha")
        git("add", ".claude", cwd=repo)
        git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "skills", cwd=repo)
        wt = repo / ".claude" / "worktrees" / "card1"
        git("worktree", "add", "-q", str(wt), cwd=repo)
        self.check_git(repo, repo, wt)
        self.assertEqual(git("status", "--porcelain", "--untracked-files=all", cwd=wt), "")
        self.assertEqual(os.readlink(wt / ".agents" / "skills" / "alpha"), "../../.claude/skills/alpha")
        self.eps(repo)  # the main checkout's own links, made once
        real_run = subprocess.run
        calls = []

        def counting(*a, **k):
            calls.append(a)
            return real_run(*a, **k)
        subprocess.run = counting
        try:
            self.eps(repo, wt)
            self.eps(repo)
        finally:
            subprocess.run = real_run
        self.assertEqual([c for c in calls if "git" in str(c)], [])

    def test_s72_unwritable_exclude_means_no_link(self):
        repo = new_repo(self.tmp / "ro")
        skill(repo / ".claude" / "skills", "alpha")
        info = repo / ".git" / "info"
        info.mkdir(exist_ok=True)
        (info / "exclude").write_text("# mine\n")
        (info / "exclude").chmod(0o400)
        info.chmod(0o500)
        self.addCleanup(info.chmod, 0o755)
        self.addCleanup((info / "exclude").chmod, 0o644)
        (linked, _), out = self.eps(repo)
        self.assertEqual(linked, [])
        self.assertFalse(os.path.lexists(repo / ".agents" / "skills" / "alpha"))
        self.assertIn("Note", out)


class Workers(Base):
    def test_s73_project_of_board(self):
        import reports
        self.assertEqual(reports.project_of_board("/x/proj/.task_board"), "/x/proj")
        self.assertEqual(reports.project_of_board("/x/proj/tools/task_board"), "/x/proj")
        self.assertEqual(reports.project_of_board("/x/proj/.task_board/"), "/x/proj")

    def test_s64_s73_codex_worker_sets_up_before_codex_starts(self):
        import codex_worker
        proj = new_repo(self.tmp / "proj")
        skill(proj / ".claude" / "skills", "alpha")
        board = proj / ".task_board"
        board.mkdir()
        wt = self.tmp / "wt"
        wt.mkdir()
        seen = {}

        def fake_spawn(args, cwd, prompt, log, err):
            seen["rules"] = (self.codex / "AGENTS.md").exists()
            seen["proj"] = os.path.lexists(proj / ".agents" / "skills" / "alpha")
            seen["wt"] = os.path.lexists(wt / ".agents" / "skills" / "alpha")
            raise RuntimeError("stop here")
        old = codex_worker.codex._spawn
        codex_worker.codex._spawn = fake_spawn
        self.addCleanup(setattr, codex_worker.codex, "_spawn", old)
        old_stdin = sys.stdin
        sys.stdin = io.StringIO("do it")
        self.addCleanup(setattr, sys, "stdin", old_stdin)
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(RuntimeError):
            codex_worker.main(["--board", str(board), "t1", "--model", "gpt-x", "--cwd", str(wt)])
        self.assertEqual(seen, {"rules": True, "proj": True, "wt": True})
        self.assertEqual(out.getvalue(), "", "setup notes must not mix into the worker's answer")


if __name__ == "__main__":
    unittest.main(verbosity=2)
