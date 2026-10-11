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
        self.files = {
            "DeusData/codebase-memory-mcp/releases/download/v0.11.0/" + cmm_asset: cmm_bytes,
            "DeusData/codebase-memory-mcp/releases/download/v0.11.0/checksums.txt":
                f"{cmm_sum or sha(cmm_bytes)}  {cmm_asset}\n".encode(),
            "scrypster/muninndb/releases/download/v0.11.0/" + mun_asset: MUNINN,
            "scrypster/muninndb/releases/download/v0.11.0/checksums.txt": f"{sha(MUNINN)}  {mun_asset}\n".encode(),
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
        out = "tool 1.2.3\n" if cmd[1:] == ["--version"] else ""
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

    def test_context_mode_plugin(self):
        self.which["claude"] = "/usr/bin/claude"
        self.main()
        self.assertIn(["claude", "plugin", "marketplace", "add", "mksglu/context-mode"], self.calls)
        self.assertIn(["claude", "plugin", "install", "context-mode@context-mode", "--scope", "user"], self.calls)
        os.makedirs(self.claude)
        with open(os.path.join(self.claude, "settings.json"), "w") as f:
            json.dump({"enabledPlugins": {"context-mode@context-mode": True}}, f)
        self.calls.clear()
        out = self.main()
        self.assertFalse(any(c[0] == "claude" for c in self.calls))
        self.assertIn("Kept your context-mode plugin", out)

    def test_claude_found_off_path(self):
        # A board started from the desktop shortcut may lack ~/.local/bin on PATH.
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
        before = sorted(os.listdir(self.home))
        out = self.main("--dry-run")
        self.assertEqual(sorted(os.listdir(self.home)), before)
        self.assertEqual((self.calls, self.downloads), ([], []))
        for line in ("Would download codebase-memory-mcp", "Would download muninn", "Would add MCP server",
                     "Would run: muninn start", "Would run: claude plugin install"):
            self.assertIn(line, out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
