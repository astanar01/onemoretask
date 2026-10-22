"""setup/install_observer.py: fresh install, upgrade of a copy install.sh made, an edited copy kept, a user's own copy
kept, install.ps1's sums format, ONEMORETASK_NO_OBSERVER, --dry-run, and run_setup's fingerprint seeing a skill change.
The install.sh step itself runs against the same folders, so both sides must accept each other's .install-sums.

Run: python3 tests/test_install_observer.py  (stdlib only; a fresh temp Claude dir per test)."""
import contextlib
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "setup"))
import install_observer  # noqa: E402

REAL_REPO = install_observer.REPO


def sh_step():
    """install.sh's task-observer block, run on its own with $dir and CLAUDE_CONFIG_DIR."""
    lines = (REAL_REPO / "install.sh").read_text().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith('if [ "${ONEMORETASK_NO_OBSERVER'))
    end = next(i for i in range(start, len(lines)) if lines[i] == "fi")
    return "\n".join(lines[start:end + 1]) + "\n"


class InstallObserverTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="install-observer-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.claude = self.tmp / "claude-home"
        self.claude.mkdir()
        self.repo = self.tmp / "repo"
        shutil.copytree(REAL_REPO / "skills" / "task-observer", self.repo / "skills" / "task-observer")
        install_observer.REPO = self.repo
        self.addCleanup(setattr, install_observer, "REPO", REAL_REPO)
        os.environ.pop("ONEMORETASK_NO_OBSERVER", None)
        self.dest = self.claude / "skills" / "task-observer"
        self.src = self.repo / "skills" / "task-observer"

    def run_py(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(install_observer.main(["--claude-dir", str(self.claude), *extra]), 0)
        self.assertEqual(install_observer.FAILED, [])
        return out.getvalue()

    def run_sh(self):
        env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.claude), dir=str(self.repo))
        return subprocess.run(["sh", "-c", sh_step()], env=env, capture_output=True, text=True, check=True).stdout

    def ship_change(self):
        f = self.src / "SKILL.md"
        f.write_text(f.read_text() + "\n<!-- shipped update -->\n")

    def same_as_repo(self):
        return install_observer.files(self.dest) == install_observer.files(self.src)

    def test_fresh_install_and_install_sh_accepts_its_sums(self):
        self.assertIn("Installed", self.run_py())
        self.assertTrue(self.same_as_repo())
        self.assertTrue((self.dest / ".install-sums").exists())
        self.assertIn("already up to date", self.run_py())
        self.ship_change()
        self.assertIn("Installed the task-observer skill", self.run_sh())  # not "edited since the last install"
        self.assertTrue(self.same_as_repo())

    def test_upgrades_a_copy_install_sh_made(self):
        self.run_sh()
        self.ship_change()
        self.assertIn("Installed", self.run_py())
        self.assertTrue(self.same_as_repo())
        self.assertNotIn(".new-", " ".join(p.name for p in self.dest.parent.iterdir()))

    def test_edited_copy_is_kept(self):
        self.run_sh()
        (self.dest / "SKILL.md").write_text("my edit\n")
        self.ship_change()
        self.assertIn("edited since the last install", self.run_py())
        self.assertEqual((self.dest / "SKILL.md").read_text(), "my edit\n")

    def test_finder_files_are_not_edits(self):
        self.run_sh()
        (self.dest / ".DS_Store").write_bytes(b"\0\1")
        self.ship_change()
        self.assertIn("Installed", self.run_py())
        self.assertTrue(self.same_as_repo())

    def test_users_own_copy_is_kept(self):
        self.dest.mkdir(parents=True)
        (self.dest / "SKILL.md").write_text("mine\n")
        self.assertIn("Kept your own", self.run_py())
        self.assertEqual(sorted(p.name for p in self.dest.iterdir()), ["SKILL.md"])

    def test_folder_without_skill_md_is_left_alone(self):
        self.dest.mkdir(parents=True)
        (self.dest / "notes.txt").write_text("x\n")
        self.assertIn("left alone", self.run_py())
        self.assertFalse((self.dest / "SKILL.md").exists())

    def test_install_ps1_sums_are_accepted(self):
        self.run_py()
        (self.dest / ".install-sums").write_text(install_observer.sums_ps1(self.dest), newline="")
        self.ship_change()
        self.assertIn("Installed", self.run_py())
        self.assertTrue(self.same_as_repo())

    def test_opt_out_and_dry_run_change_nothing(self):
        os.environ["ONEMORETASK_NO_OBSERVER"] = "1"
        self.addCleanup(os.environ.pop, "ONEMORETASK_NO_OBSERVER", None)
        self.run_py()
        self.assertFalse(self.dest.exists())
        del os.environ["ONEMORETASK_NO_OBSERVER"]
        self.assertIn("Would install", self.run_py("--dry-run"))
        self.assertFalse(self.dest.exists())

    def test_cksum_matches_the_system_tool(self):
        f = self.src / "SKILL.md"
        got = subprocess.run(["cksum", str(f)], capture_output=True, text=True, check=True).stdout.split()[:2]
        data = f.read_bytes()
        self.assertEqual(got, [str(install_observer.cksum(data)), str(len(data))])


class FingerprintTest(unittest.TestCase):
    def test_a_skill_change_reruns_the_setup(self):
        tmp = Path(tempfile.mkdtemp(prefix="install-observer-fp-"))
        self.addCleanup(shutil.rmtree, tmp)
        repo = tmp / "repo"
        for d in ("claude", "skills", "setup"):
            shutil.copytree(REAL_REPO / d, repo / d, ignore=shutil.ignore_patterns("__pycache__"))
        code = "import sys; sys.path.insert(0, sys.argv[1]); import run_setup, pathlib; " \
               "print(run_setup.fingerprint(pathlib.Path(sys.argv[2])))"

        def fp():
            return subprocess.run([sys.executable, "-c", code, str(repo / "setup"), str(tmp / "claude-home")],
                                  capture_output=True, text=True, check=True).stdout.strip()
        before = fp()
        self.assertEqual(before, fp())
        f = repo / "skills" / "task-observer" / "references" / "signals.md"
        f.write_text(f.read_text() + "\nx\n")
        self.assertNotEqual(before, fp())

if __name__ == "__main__":
    unittest.main(verbosity=1)
