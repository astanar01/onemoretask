"""Set up the Claude Code tools (install_tools.py), then the hooks, rules and skills (install_hooks.py).

Usage: python3 setup/run_setup.py [--if-needed]
install.sh and install.ps1 run it. The board runs it with --if-needed each time its server starts, so an Update
or a git pull that brings new tools, hooks or skills sets them up too, and so does a first start on a copy that
was installed before the tools existed. A finished run writes a hash of the setup files to STAMP in the Claude dir
($CLAUDE_CONFIG_DIR or ~/.claude); --if-needed does nothing while the stamp matches. ONEMORETASK_NO_TOOLS=1
writes "off" there instead, which --if-needed also respects, until a run without it. A run where a step failed
(a download, the plugin command) writes no stamp, so the next start tries again. Always exits 0.
"""
import argparse
import hashlib
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import install_hooks  # noqa: E402
import install_tools  # noqa: E402

STAMP = ".onemoretask-setup"
LOCK_TTL = 1800  # seconds; an older lock is a run that crashed


def claude_dir():
    return Path(os.path.expanduser(os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))


def fingerprint(claude):
    """Hash of the setup scripts and every file install_hooks copies: it changes when a pull changes any of them."""
    h = hashlib.sha256()
    files = [HERE / "run_setup.py", HERE / "install_tools.py", HERE / "install_hooks.py"]
    for f in files + [src for _, src, _ in install_hooks.shipped(claude)]:
        h.update(f.relative_to(install_hooks.REPO).as_posix().encode() + b"\0")
        try:
            h.update(hashlib.sha256(f.read_bytes()).digest())
        except OSError:
            h.update(b"missing")
    return h.hexdigest()[:16]


def read_stamp(claude):
    try:
        return (claude / STAMP).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def write_stamp(claude, value):
    try:
        install_hooks.write_atomic(claude / STAMP, (value + "\n").encode())
    except OSError as e:
        print(f"Note: could not write {claude / STAMP} ({e}) — the setup runs again next time the board starts.")


def needed(claude):
    """True when --if-needed would run the setup."""
    if os.environ.get("ONEMORETASK_NO_TOOLS") == "1":
        return False
    return read_stamp(claude) not in ("off", fingerprint(claude))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Set up the Claude Code tools, hooks, rules and skills.")
    ap.add_argument("--if-needed", action="store_true",
                    help="only when the setup files changed since the last finished run")
    a = ap.parse_args(argv)
    claude = claude_dir()
    if os.environ.get("ONEMORETASK_NO_TOOLS") == "1":
        print("Skipped the Claude Code tools, hooks, rules and skills (ONEMORETASK_NO_TOOLS=1).")
        write_stamp(claude, "off")
        return 0
    if a.if_needed and not needed(claude):
        return 0
    lock = claude / (STAMP + ".lock")
    try:
        claude.mkdir(parents=True, exist_ok=True)
        if lock.exists() and time.time() - lock.stat().st_mtime < LOCK_TTL:
            # An Update during a setup restarts the board, whose start-up check would run a second one.
            print("The Claude Code tools setup is already running.")
            return 0
        lock.write_text(str(os.getpid()), encoding="utf-8")
    except OSError:
        pass
    try:
        return run(claude)
    finally:
        try:
            lock.unlink()
        except OSError:
            pass


def run(claude):
    want = fingerprint(claude)
    failed = []
    for script in (install_tools, install_hooks):
        try:
            script.main([])
            failed += script.FAILED
        except Exception as e:
            print(f"Note: {script.__name__} did not finish ({e}).")
            failed.append(script.__name__)
    if failed:
        print(f"Note: not finished ({', '.join(failed)}) — the board tries again next time it starts.")
    else:
        write_stamp(claude, want)
    return 0


if __name__ == "__main__":
    sys.exit(main())
