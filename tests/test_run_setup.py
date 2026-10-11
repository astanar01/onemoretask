"""setup/run_setup.py in a throwaway Claude dir: the two setup scripts are faked, only the stamp logic runs."""
import contextlib
import importlib.util
import io
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("run_setup", os.path.join(HERE, "..", "setup", "run_setup.py"))
rs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rs)


class RunSetup(unittest.TestCase):
    def setUp(self):
        self.claude = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.claude)
        for k, v in (("CLAUDE_CONFIG_DIR", str(self.claude)), ("ONEMORETASK_NO_TOOLS", None)):
            old = os.environ.get(k)
            self.addCleanup(lambda k=k, old=old: os.environ.pop(k, None) if old is None
                            else os.environ.__setitem__(k, old))
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        self.runs = []
        self.fail = {}
        for mod in (rs.install_tools, rs.install_hooks):
            self.patch(mod, "main", lambda argv, mod=mod: self.fake(mod))

    def patch(self, obj, name, value):
        old = getattr(obj, name)
        self.addCleanup(setattr, obj, name, old)
        setattr(obj, name, value)

    def fake(self, mod):
        self.runs.append(mod.__name__)
        mod.FAILED[:] = self.fail.get(mod.__name__, [])
        return 0

    def main(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(rs.main(list(args)), 0)
        return out.getvalue()

    def stamp(self):
        return rs.read_stamp(self.claude)

    def test_first_start_runs_both_and_stamps(self):
        self.assertTrue(rs.needed(self.claude))
        self.main("--if-needed")
        self.assertEqual(self.runs, ["install_tools", "install_hooks"])
        self.assertEqual(self.stamp(), rs.fingerprint(self.claude))
        self.assertFalse(rs.needed(self.claude))

    def test_matching_stamp_skips(self):
        self.main()
        self.runs.clear()
        self.main("--if-needed")
        self.assertEqual(self.runs, [])

    def test_changed_setup_files_run_again(self):
        self.main()
        self.runs.clear()
        old = rs.fingerprint(self.claude)
        self.patch(rs, "fingerprint", lambda claude: old + "x")  # what a pull touching claude/ or setup/ does
        self.assertTrue(rs.needed(self.claude))
        self.main("--if-needed")
        self.assertEqual(self.runs, ["install_tools", "install_hooks"])
        self.assertEqual(self.stamp(), old + "x")

    def test_failed_step_writes_no_stamp(self):
        self.fail["install_tools"] = ["muninn"]
        out = self.main("--if-needed")
        self.assertEqual(self.stamp(), "")
        self.assertIn("tries again", out)
        self.assertTrue(rs.needed(self.claude))

    def test_crashing_script_writes_no_stamp(self):
        self.patch(rs.install_hooks, "main", lambda argv: 1 / 0)
        self.main()
        self.assertEqual(self.stamp(), "")

    def test_opt_out_is_remembered(self):
        os.environ["ONEMORETASK_NO_TOOLS"] = "1"
        self.main()
        self.assertEqual(self.runs, [])
        self.assertEqual(self.stamp(), "off")
        del os.environ["ONEMORETASK_NO_TOOLS"]
        self.assertFalse(rs.needed(self.claude))
        self.main("--if-needed")
        self.assertEqual(self.runs, [])
        self.main()  # installing again without the opt-out turns it back on
        self.assertEqual(self.runs, ["install_tools", "install_hooks"])
        self.assertEqual(self.stamp(), rs.fingerprint(self.claude))

    def test_running_setup_is_not_doubled(self):
        (self.claude / (rs.STAMP + ".lock")).write_text("1")
        out = self.main("--if-needed")
        self.assertEqual(self.runs, [])
        self.assertIn("already running", out)

    def test_stale_lock_is_ignored(self):
        lock = self.claude / (rs.STAMP + ".lock")
        lock.write_text("1")
        old = time.time() - rs.LOCK_TTL - 60
        os.utime(lock, (old, old))
        self.main("--if-needed")
        self.assertEqual(self.runs, ["install_tools", "install_hooks"])
        self.assertFalse(lock.exists())

    def test_fingerprint_covers_shipped_files(self):
        files = {src for _, src, _ in rs.install_hooks.shipped(self.claude)}
        self.assertTrue(any("claude/hooks" in src.as_posix() for src in files))
        self.assertTrue(any(src.name == "SKILL.md" for src in files))


if __name__ == "__main__":
    unittest.main(verbosity=2)
