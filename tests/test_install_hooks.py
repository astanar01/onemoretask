"""setup/install_hooks.py: first install, idempotent rerun, upgrade vs a user-edited hook, settings merge,
invalid settings.json, ONEMORETASK_NO_TOOLS and --dry-run.

Run: python3 tests/test_install_hooks.py  (stdlib only; a fresh temp Claude dir per test)."""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "setup"))
import install_hooks  # noqa: E402

REAL_REPO = install_hooks.REPO


def snapshot(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()}


class InstallHooksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-hooks-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.claude = self.tmp / "claude-home"
        self.claude.mkdir()
        # A private copy of the shipped files, so a test can change a "repo" file.
        self.repo = self.tmp / "repo"
        shutil.copytree(REAL_REPO / "claude", self.repo / "claude")
        for name in install_hooks.SKILLS:
            shutil.copytree(REAL_REPO / "skills" / name, self.repo / "skills" / name)
        install_hooks.REPO = self.repo
        self.addCleanup(setattr, install_hooks, "REPO", REAL_REPO)
        os.environ.pop("ONEMORETASK_NO_TOOLS", None)

    def run_installer(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = install_hooks.main(["--claude-dir", str(self.claude), *extra])
        self.assertEqual(code, 0)
        return out.getvalue()

    def settings(self):
        return json.loads((self.claude / "settings.json").read_text())

    def commands(self, event):
        return [h["command"] for e in self.settings()["hooks"].get(event, []) for h in e["hooks"]]

    def test_lost_manifest_is_rebuilt_and_updates_still_land(self):
        self.run_installer()
        (self.claude / install_hooks.MANIFEST).unlink()
        self.run_installer()
        self.assertTrue((self.claude / install_hooks.MANIFEST).exists())
        hook = self.repo / "claude" / "hooks" / "session-gate.sh"
        hook.write_text(hook.read_text() + "\n# shipped update\n")
        self.run_installer()
        self.assertEqual((self.claude / "hooks" / "session-gate.sh").read_bytes(), hook.read_bytes())

    def test_first_install_copies_everything_and_merges_settings(self):
        out = self.run_installer()
        for _, src, dest in install_hooks.shipped(self.claude):
            self.assertEqual(dest.read_bytes(), src.read_bytes(), dest)
        self.assertTrue(os.access(self.claude / "hooks" / "session-gate.sh", os.X_OK))
        self.assertTrue((self.claude / "skills" / "cmm-rules" / "SKILL.md").exists())
        ours = json.loads((self.repo / "claude" / "settings-hooks.json").read_text())["hooks"]
        self.assertEqual(self.settings()["hooks"], ours)
        self.assertIn("Installed", out)
        self.assertIn("Registered", out)

    def test_second_run_changes_nothing(self):
        self.run_installer()
        before = snapshot(self.claude)
        out = self.run_installer()
        self.assertEqual(snapshot(self.claude), before)
        self.assertIn("All hooks already registered", out)
        self.assertIn("Installed 0 hooks", out)

    def test_user_edited_hook_kept_untouched_hook_upgraded(self):
        self.run_installer()
        edited = self.claude / "hooks" / "cwd-guard.sh"
        edited.write_text(edited.read_text() + "# my tweak\n")
        for name in ("cwd-guard.sh", "cmm-nudge.sh"):
            src = self.repo / "claude" / "hooks" / name
            src.write_text(src.read_text() + "# new version\n")
        out = self.run_installer()
        self.assertTrue(edited.read_text().endswith("# my tweak\n"))
        self.assertTrue((self.claude / "hooks" / "cmm-nudge.sh").read_text().endswith("# new version\n"))
        self.assertIn("kept 1 of yours: cwd-guard.sh", out)
        self.assertIn("Installed 1 hooks", out)

    def test_users_own_file_is_kept(self):
        own = self.claude / "rules" / "ctx-rules.md"
        own.parent.mkdir(parents=True)
        own.write_text("mine\n")
        out = self.run_installer()
        self.assertEqual(own.read_text(), "mine\n")
        self.assertIn("kept 1 of yours: ctx-rules.md", out)
        self.run_installer()  # still not ours on a second run
        self.assertEqual(own.read_text(), "mine\n")

    def test_users_extra_hook_stays_and_ours_is_added(self):
        mine = {"matcher": "Bash", "hooks": [{"type": "command", "command": "bash ~/my-hook.sh"}]}
        (self.claude / "settings.json").write_text(json.dumps({"model": "x", "hooks": {"PreToolUse": [mine]}}))
        self.run_installer()
        s = self.settings()
        self.assertEqual(s["model"], "x")
        self.assertEqual(s["hooks"]["PreToolUse"][0], mine)
        self.assertTrue(any("cwd-guard.sh" in c for c in self.commands("PreToolUse")))
        self.assertTrue((self.claude / "settings.json.bak-onemoretask").exists())

    def test_same_script_under_other_path_not_duplicated(self):
        theirs = {"matcher": "Bash", "hooks": [{"type": "command", "command": "bash /opt/hooks/cwd-guard.sh"}]}
        (self.claude / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [theirs]}}))
        self.run_installer()
        cwd = [c for c in self.commands("PreToolUse") if "cwd-guard.sh" in c]
        self.assertEqual(cwd, ["bash /opt/hooks/cwd-guard.sh"])

    def test_invalid_settings_left_alone(self):
        path = self.claude / "settings.json"
        path.write_text("{not json")
        out = self.run_installer()
        self.assertEqual(path.read_text(), "{not json")
        self.assertIn("not valid settings JSON", out)
        self.assertTrue((self.claude / "hooks" / "session-gate.sh").exists())

    def test_no_tools_env_does_nothing(self):
        os.environ["ONEMORETASK_NO_TOOLS"] = "1"
        self.addCleanup(os.environ.pop, "ONEMORETASK_NO_TOOLS", None)
        out = self.run_installer()
        self.assertEqual(snapshot(self.claude), {})
        self.assertEqual(out.count("\n"), 1)

    def test_dry_run_writes_nothing(self):
        (self.claude / "settings.json").write_text("{}")
        out = self.run_installer("--dry-run")
        self.assertEqual(snapshot(self.claude), {"settings.json": b"{}"})
        self.assertIn("Would install", out)
        self.assertIn("Would register", out)


if __name__ == "__main__":
    unittest.main()
