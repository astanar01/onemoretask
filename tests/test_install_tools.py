"""setup/install_tools.py in a throwaway HOME, with no network: downloads, PATH and commands are faked."""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("install_tools", os.path.join(HERE, "..", "setup", "install_tools.py"))
it = importlib.util.module_from_spec(spec)
spec.loader.exec_module(it)

CMM = b"#!/bin/sh\necho cmm\n"
MUNINN = b"#!/bin/sh\necho muninn\n"


class InstallTools(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.home)
        self.bin = os.path.join(self.home, ".local", "bin")
        self.claude = os.path.join(self.home, ".claude")
        self.cj = os.path.join(self.home, ".claude.json")
        self.calls = []
        self.downloads = []
        self.which = {}
        self.versions = {}
        self.failing = set()
        self.port = False
        self.platform = ("linux", "amd64")
        env = {"HOME": self.home}
        for k in ("CLAUDE_CONFIG_DIR", "ONEMORETASK_NO_TOOLS"):
            env[k] = None
        for k, v in env.items():
            old = os.environ.get(k)
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None
                            else os.environ.__setitem__(k, old))
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.files = {}
        self.serve()
        for name, fake in [("download", self.fake_download), ("platform_tag", lambda: self.platform),
                           ("port_open", lambda port: self.port)]:
            self.addCleanup(setattr, it, name, getattr(it, name))
            setattr(it, name, fake)
        self.addCleanup(setattr, it.shutil, "which", it.shutil.which)
        it.shutil.which = lambda name: self.which.get(name)
        self.addCleanup(setattr, it.subprocess, "run", it.subprocess.run)
        it.subprocess.run = self.fake_run

    def serve(self, cmm_sum=None):
        """Release files the fake download serves, built for self.platform."""
        os_name, arch = self.platform
        src = tempfile.mkdtemp(dir=self.home)
        exe = it.exe_name("codebase-memory-mcp", os_name)
        arc = os.path.join(src, "a")
        if os_name == "windows":
            with zipfile.ZipFile(arc, "w") as z:
                z.writestr("LICENSE", "x")
                z.writestr(exe, CMM)
        else:
            with open(os.path.join(src, exe), "wb") as f:
                f.write(CMM)
            with tarfile.open(arc, "w:gz") as t:
                t.add(os.path.join(src, exe), arcname=exe)
        with open(arc, "rb") as f:
            cmm_bytes = f.read()
        cmm_asset = it.asset_name("codebase-memory-mcp", os_name, arch)
        mun_asset = it.asset_name("muninn", os_name, arch)
        sha = lambda b: hashlib.sha256(b).hexdigest()
        cmm_rel = "DeusData/codebase-memory-mcp/releases/download/" + it.VERSIONS["codebase-memory-mcp"]["tag"] + "/"
        mun_rel = "scrypster/muninndb/releases/download/" + it.VERSIONS["muninn"]["tag"] + "/"
        self.files = {
            cmm_rel + cmm_asset: cmm_bytes,
            cmm_rel + "checksums.txt": f"{cmm_sum or sha(cmm_bytes)}  {cmm_asset}\n".encode(),
            mun_rel + mun_asset: MUNINN,
            mun_rel + "checksums.txt": f"{sha(MUNINN)}  {mun_asset}\n".encode(),
        }

    def fake_download(self, url, dest):
        self.downloads.append(url)
        with open(dest, "wb") as f:
            f.write(self.files[url.replace("https://github.com/", "")])

    def fake_run(self, cmd, **kw):
        call = [os.path.basename(cmd[0])] + list(cmd[1:])
        self.calls.append(call)
        if " ".join(call) in self.failing:
            return subprocess.CompletedProcess(cmd, 1, "", "boom")
        out = ""
        if cmd[1:] == ["--version"]:
            # A path in self.versions answers with its own version; bytes on disk pick the answer for files.
            out = self.versions.get(cmd[0], "tool 1.2.3\n")
            if os.path.isfile(cmd[0]) and self.read(cmd[0]) in self.versions:
                out = self.versions[self.read(cmd[0])]
            if out is None:
                return subprocess.CompletedProcess(cmd, 1, "", "dyld: bad")
        return subprocess.CompletedProcess(cmd, 0, out, "")

    def main(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = it.main(["--bin-dir", self.bin, "--claude-dir", self.claude] + list(extra))
        self.assertEqual(rc, 0)
        return buf.getvalue()

    def servers(self):
        with open(self.cj) as f:
            return json.load(f)["mcpServers"]

    def read(self, p):
        with open(p, "rb") as f:
            return f.read()

    def test_installs_both_and_registers(self):
        out = self.main()
        cmm, mun = os.path.join(self.bin, "codebase-memory-mcp"), os.path.join(self.bin, "muninn")
        self.assertEqual(self.read(cmm), CMM)
        self.assertEqual(self.read(mun), MUNINN)
        self.assertTrue(os.access(cmm, os.X_OK) and os.access(mun, os.X_OK))
        self.assertEqual(sorted(os.listdir(self.bin)), ["codebase-memory-mcp", "muninn"])  # no temp files left
        self.assertIn("Installed codebase-memory-mcp (version tool 1.2.3)", out)
        self.assertEqual(self.servers(), {
            "codebase-memory-mcp": {"type": "stdio", "command": cmm, "args": [], "env": {}},
            "muninn": {"type": "stdio", "command": mun, "args": ["mcp"], "env": {}}})
        self.assertIn(["muninn", "start"], self.calls)
        self.assertIn("Note: Ollama not found", out)
        self.assertIn("Note: Claude Code not found", out)

    def test_windows_zip(self):
        self.platform = ("windows", "amd64")
        self.serve()
        self.main()
        self.assertEqual(self.read(os.path.join(self.bin, "codebase-memory-mcp.exe")), CMM)
        self.assertEqual(self.read(os.path.join(self.bin, "muninn.exe")), MUNINN)

    def test_macos_clears_quarantine(self):
        self.platform = ("darwin", "arm64")
        self.serve()
        self.main()
        cmm = os.path.join(self.bin, "codebase-memory-mcp")
        self.assertIn(["xattr", "-cr", cmm], self.calls)
        self.assertIn(["codesign", "--force", "--sign", "-", cmm], self.calls)

    def test_checksum_mismatch_refused(self):
        self.serve(cmm_sum="0" * 64)
        out = self.main()
        self.assertIn("did not match its checksum — refused", out)
        self.assertFalse(os.path.exists(os.path.join(self.bin, "codebase-memory-mcp")))
        self.assertEqual(os.listdir(self.bin), ["muninn"])
        self.assertNotIn("codebase-memory-mcp", self.servers())

    def test_existing_binary_kept(self):
        os.makedirs(self.bin)
        mine = os.path.join(self.bin, "muninn")
        with open(mine, "wb") as f:
            f.write(b"mine")
        self.which["codebase-memory-mcp"] = "/opt/cmm/codebase-memory-mcp"
        out = self.main()
        self.assertEqual(self.read(mine), b"mine")
        self.assertFalse(any("muninndb" in u or "codebase-memory" in u for u in self.downloads))
        self.assertIn("Kept your own muninn (version tool 1.2.3)", out)
        self.assertEqual(self.servers()["codebase-memory-mcp"]["command"], "/opt/cmm/codebase-memory-mcp")

    def old_cmm(self, version="codebase-memory-mcp 0.8.1\n"):
        os.makedirs(self.bin, exist_ok=True)
        cmm = os.path.join(self.bin, "codebase-memory-mcp")
        with open(cmm, "wb") as f:
            f.write(b"old")
        self.versions[b"old"] = version
        return cmm

    def test_older_code_search_replaced_with_backup(self):
        cmm = self.old_cmm()
        out = self.main()
        self.assertEqual(self.read(cmm), CMM)
        self.assertEqual(self.read(cmm + "-0.8.1.bak"), b"old")
        self.assertTrue(any("codebase-memory" in u for u in self.downloads))
        self.assertIn("Updated codebase-memory-mcp from codebase-memory-mcp 0.8.1 to tool 1.2.3", out)
        self.assertNotIn("codebase-memory-mcp", it.FAILED)
        self.assertEqual(self.servers()["codebase-memory-mcp"]["command"], cmm)

    def test_code_search_at_or_above_pin_kept(self):
        pin = it.VERSIONS["codebase-memory-mcp"]["tag"].lstrip("v")
        for have in (pin, "9.0.0"):
            with self.subTest(have=have):
                self.downloads.clear()
                cmm = self.old_cmm(f"codebase-memory-mcp {have}\n")
                out = self.main()
                self.assertEqual(self.read(cmm), b"old")
                self.assertFalse(any("codebase-memory" in u for u in self.downloads))
                self.assertIn(f"Kept your own codebase-memory-mcp (version codebase-memory-mcp {have})", out)

    def test_muninn_never_replaced_for_age(self):
        os.makedirs(self.bin)
        mine = os.path.join(self.bin, "muninn")
        with open(mine, "wb") as f:
            f.write(b"mine")
        self.versions[b"mine"] = "muninn 0.0.1\n"
        self.main()
        self.assertEqual(self.read(mine), b"mine")
        self.assertFalse(any("muninndb" in u for u in self.downloads))

    def test_old_code_search_put_back_when_new_one_does_not_run(self):
        cmm = self.old_cmm()
        self.versions[CMM] = None
        out = self.main()
        self.assertEqual(self.read(cmm), b"old")
        self.assertFalse(os.path.exists(cmm + "-0.8.1.bak"))
        self.assertIn("put your old one (codebase-memory-mcp 0.8.1) back", out)
        self.assertNotIn("codebase-memory-mcp", it.FAILED)  # no retry download on every board start

    def test_old_code_search_in_folder_we_cannot_write_kept(self):
        path = os.path.join(self.home, "nowhere", "codebase-memory-mcp")  # folder does not exist: not writable
        self.which["codebase-memory-mcp"] = path
        self.versions[path] = "codebase-memory-mcp 0.8.1\n"
        out = self.main()
        self.assertIn("older than", out)
        self.assertIn("can't be replaced — kept it", out)
        self.assertFalse(any("codebase-memory" in u for u in self.downloads))
        self.assertEqual(self.servers()["codebase-memory-mcp"]["command"], path)

    def test_dry_run_names_the_replace(self):
        cmm = self.old_cmm()
        out = self.main("--dry-run")
        self.assertIn(f"Would replace your codebase-memory-mcp (codebase-memory-mcp 0.8.1) at {cmm}", out)
        self.assertEqual(self.read(cmm), b"old")
        self.assertEqual(self.downloads, [])

    def test_mcp_entries_not_clobbered(self):
        mine = {"type": "stdio", "command": "/my/muninn", "args": []}
        with open(self.cj, "w") as f:
            json.dump({"theme": "dark", "mcpServers": {"other": {"command": "x"}, "muninn": mine}}, f)
        before = self.read(self.cj)
        out = self.main()
        with open(self.cj) as f:
            data = json.load(f)
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["mcpServers"]["other"], {"command": "x"})
        self.assertEqual(data["mcpServers"]["muninn"], mine)
        self.assertIn("codebase-memory-mcp", data["mcpServers"])
        self.assertEqual(self.read(self.cj + ".bak-onemoretask"), before)
        self.assertIn("Kept your own muninn MCP server entry", out)

    def test_claude_config_dir(self):
        os.environ["CLAUDE_CONFIG_DIR"] = self.claude
        os.makedirs(self.claude)
        self.main()
        self.assertTrue(os.path.exists(os.path.join(self.claude, ".claude.json")))
        self.assertFalse(os.path.exists(self.cj))

    def test_ollama_env_line_once(self):
        self.which["ollama"] = "/usr/bin/ollama"
        env = os.path.join(self.home, ".muninn", "muninn.env")
        os.makedirs(os.path.dirname(env))
        with open(env, "w") as f:
            f.write("OTHER=1")  # no trailing newline
        self.main()
        self.assertIn(["ollama", "pull", "nomic-embed-text"], self.calls)
        self.port = True  # second run: the daemon is up and the line is there
        self.calls.clear()
        out = self.main()
        with open(env) as f:
            self.assertEqual(f.read(), "OTHER=1\n" + it.OLLAMA_LINE + "\n")
        self.assertNotIn(["muninn", "restart"], self.calls)
        self.assertNotIn(["muninn", "start"], self.calls)
        self.assertIn("Muninn server already running", out)

    def test_env_line_added_to_running_daemon_restarts(self):
        self.which["ollama"] = "/usr/bin/ollama"
        self.port = True
        self.main()
        self.assertIn(["muninn", "restart"], self.calls)

    def test_existing_ollama_value_kept(self):
        self.which["ollama"] = "/usr/bin/ollama"
        env = os.path.join(self.home, ".muninn", "muninn.env")
        os.makedirs(os.path.dirname(env))
        with open(env, "w") as f:
            f.write("MUNINN_OLLAMA_URL=ollama://other:1/x\n")
        self.main()
        self.assertEqual(self.read(env), b"MUNINN_OLLAMA_URL=ollama://other:1/x\n")

    def node(self, version="v24.15.0"):
        """A fake node on PATH answering --version with version."""
        path = os.path.join(self.home, "nodebin", "node")  # never created: fake_run answers for it
        self.which["node"] = path
        self.versions[path] = version + "\n"
        return path

    def settings(self):
        with open(os.path.join(self.claude, "settings.json")) as f:
            return json.load(f)

    def claude_calls(self):
        return [c for c in self.calls if c[0] == "claude"]

    def test_context_mode_plugin(self):
        self.which["claude"] = "/usr/bin/claude"
        self.node()
        out = self.main()
        self.assertEqual(self.claude_calls(), [
            ["claude", "plugin", "marketplace", "add", "mksglu/context-mode"],
            ["claude", "plugin", "install", "context-mode@context-mode", "--scope", "user"]])
        self.assertIn(["node", "--version"], self.calls)
        self.assertIn("Installed the context-mode plugin", out)
        self.assertNotIn("context-mode", it.FAILED)

    def test_context_mode_already_on_is_updated(self):
        self.which["claude"] = "/usr/bin/claude"
        self.node("v25.0.1")
        os.makedirs(self.claude)
        with open(os.path.join(self.claude, "settings.json"), "w") as f:
            json.dump({"enabledPlugins": {"context-mode@context-mode": True}}, f)
        out = self.main()
        self.assertEqual(self.claude_calls(), [["claude", "plugin", "update", "context-mode@context-mode"]])
        self.assertIn("Updated the context-mode plugin. Restart Claude Code, then run /ctx-upgrade in it.", out)
        self.assertNotIn("context-mode", it.FAILED)
        self.assertTrue(self.settings()["enabledPlugins"]["context-mode@context-mode"])

    def test_context_mode_update_failure_is_failed(self):
        self.which["claude"] = "/usr/bin/claude"
        self.node()
        os.makedirs(self.claude)
        with open(os.path.join(self.claude, "settings.json"), "w") as f:
            json.dump({"enabledPlugins": {"context-mode@context-mode": True}}, f)
        self.failing = {"claude plugin update context-mode@context-mode"}
        out = self.main()
        self.assertIn("Note: claude plugin update context-mode@context-mode failed (boom)", out)
        self.assertNotIn("Restart Claude Code", out)
        self.assertIn("context-mode", it.FAILED)

    def test_old_node_skips_context_mode(self):
        self.which["claude"] = "/usr/bin/claude"
        self.node("v24.14.9")
        out = self.main()
        self.assertEqual(self.claude_calls(), [])
        self.assertIn("Note: context-mode 2.x needs Node 24.15.0 or newer (you have v24.14.9). "
                      "Install a newer Node, then run setup again", out)
        self.assertEqual(it.FAILED, ["context-mode"])
        self.assertFalse(os.path.exists(os.path.join(self.claude, "settings.json")))

    def test_old_node_names_newer_nvm_node(self):
        self.which["claude"] = "/usr/bin/claude"
        self.node("v24.12.0")
        root = os.path.join(self.home, ".nvm", "versions", "node")
        for v in ["v24.12.0", "v24.22.3", "v24.9.9", "v25.1.0"]:
            os.makedirs(os.path.join(root, v, "bin"))
            if v != "v25.1.0":  # a folder without its node binary does not count
                open(os.path.join(root, v, "bin", "node"), "w").close()
        out = self.main()
        self.assertIn("Node v24.22.3 is already installed with nvm", out)
        self.assertIn("nvm alias default 24.22.3", out)
        self.assertEqual(it.FAILED, ["context-mode"])

    def test_old_node_without_nvm_has_no_nvm_hint(self):
        self.which["claude"] = "/usr/bin/claude"
        self.node("v24.12.0")
        self.assertNotIn("nvm", self.main())

    def test_missing_or_broken_node_skips_context_mode(self):
        self.which["claude"] = "/usr/bin/claude"
        out = self.main()
        self.assertIn("needs Node 24.15.0 or newer (Node was not found)", out)
        self.assertEqual((self.claude_calls(), it.FAILED), ([], ["context-mode"]))
        for version, why in [(None, "--version did not work"), ("node: weird", "you have node: weird")]:
            with self.subTest(version=version):
                path = self.node()
                self.versions[path] = version
                self.calls.clear()
                out = self.main()
                self.assertIn(why, out)
                self.assertEqual((self.claude_calls(), it.FAILED), ([], ["context-mode"]))

    def test_context_mode_env_added_once(self):
        self.which["claude"] = "/usr/bin/claude"
        self.node()
        os.makedirs(self.claude)
        with open(os.path.join(self.claude, "settings.json"), "w") as f:
            json.dump({"theme": "dark", "env": {"OTHER": "1"}}, f)
        out = self.main()
        self.assertEqual(self.settings(), {"theme": "dark", "env": {"OTHER": "1", "CTX_REPLACE_TRIGGER_BYTES": "1048576"}})
        self.assertIn("Added CTX_REPLACE_TRIGGER_BYTES=1048576 to the env in", out)
        before = self.read(os.path.join(self.claude, "settings.json"))
        out = self.main()
        self.assertEqual(self.read(os.path.join(self.claude, "settings.json")), before)
        self.assertIn("Kept your own CTX_REPLACE_TRIGGER_BYTES (1048576)", out)

    def test_context_mode_env_user_value_kept(self):
        self.which["claude"] = "/usr/bin/claude"
        self.node()
        os.makedirs(self.claude)
        with open(os.path.join(self.claude, "settings.json"), "w") as f:
            json.dump({"env": {"CTX_REPLACE_TRIGGER_BYTES": "8192"}}, f)
        before = self.read(os.path.join(self.claude, "settings.json"))
        out = self.main()
        self.assertEqual(self.read(os.path.join(self.claude, "settings.json")), before)
        self.assertIn("Kept your own CTX_REPLACE_TRIGGER_BYTES (8192)", out)

    def test_symlinked_settings_and_claude_json_written_through(self):
        # Kept in a dotfiles repo: the links stay, the targets get the change and keep their mode.
        self.which["claude"] = "/usr/bin/claude"
        self.node()
        dot = os.path.join(self.home, "dotfiles")
        os.makedirs(dot)
        os.makedirs(self.claude)
        for name, link in (("settings.json", os.path.join(self.claude, "settings.json")), ("claude.json", self.cj)):
            with open(os.path.join(dot, name), "w") as f:
                json.dump({"theme": "dark"}, f)
            os.chmod(os.path.join(dot, name), 0o644)
            os.symlink(os.path.join(dot, name), link)
        self.main()
        for name, link in (("settings.json", os.path.join(self.claude, "settings.json")), ("claude.json", self.cj)):
            self.assertTrue(os.path.islink(link), link)
            self.assertEqual(os.stat(os.path.join(dot, name)).st_mode & 0o777, 0o644)
        self.assertEqual(self.settings()["env"], {"CTX_REPLACE_TRIGGER_BYTES": "1048576"})
        self.assertIn("muninn", self.servers())

    def test_context_mode_env_written_with_no_settings_file(self):
        self.which["claude"] = "/usr/bin/claude"
        self.node()
        self.main()
        self.assertEqual(self.settings(), {"env": {"CTX_REPLACE_TRIGGER_BYTES": "1048576"}})

    def test_claude_found_off_path(self):
        # A board started from the desktop shortcut may lack ~/.local/bin on PATH.
        self.node()
        os.makedirs(self.bin)
        with open(os.path.join(self.bin, "claude"), "w") as f:
            f.write("")
        self.main()
        self.assertIn(["claude", "plugin", "install", "context-mode@context-mode", "--scope", "user"], self.calls)
        self.assertNotIn("context-mode", it.FAILED)

    def test_skipped_steps_are_failed(self):
        # run_setup.py writes its stamp only when FAILED is empty, so each of these must land there to be retried.
        self.which["ollama"] = "/usr/bin/ollama"
        self.failing = {"muninn start", "ollama pull nomic-embed-text", "codebase-memory-mcp --version"}
        self.main()
        self.assertEqual(sorted(it.FAILED), ["codebase-memory-mcp", "context-mode", "muninn start", "ollama pull"])
        # A download that does not run is removed, so the next run downloads it again.
        self.assertFalse(os.path.exists(os.path.join(self.bin, "codebase-memory-mcp")))
        with open(self.cj, "w") as f:
            f.write("{not json")
        self.failing = set()
        self.main()
        self.assertIn("MCP servers", it.FAILED)

    def test_no_tools_env(self):
        os.environ["ONEMORETASK_NO_TOOLS"] = "1"
        out = self.main()
        self.assertEqual(out.count("\n"), 1)
        self.assertFalse(os.path.exists(self.bin) or os.path.exists(self.cj))
        self.assertEqual((self.calls, self.downloads), ([], []))

    def test_dry_run_changes_nothing(self):
        self.which.update(ollama="/usr/bin/ollama", claude="/usr/bin/claude")
        self.node()
        before = sorted(os.listdir(self.home))
        out = self.main("--dry-run")
        self.assertEqual(sorted(os.listdir(self.home)), before)
        self.assertEqual((self.calls, self.downloads), ([["node", "--version"]], []))  # only the read-only check
        for line in ("Would download codebase-memory-mcp", "Would download muninn", "Would add MCP server",
                     "Would run: muninn start", "Would run: claude plugin install",
                     "Would add CTX_REPLACE_TRIGGER_BYTES=1048576 to the env in"):
            self.assertIn(line, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
