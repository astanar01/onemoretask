"""Copy our task-observer fork in skills/task-observer to ~/.claude/skills, the same way install.sh and install.ps1 do.

Usage: python3 setup/install_observer.py [--claude-dir DIR] [--dry-run]
run_setup.py runs it, so the board's Update brings a newer fork too; the installers keep their own step for
ONEMORETASK_NO_TOOLS users. Rules (shared with both installers): copied in when missing; an earlier copy of our
fork (marked by FORKED_FROM) is replaced unless a file in it changed since, checked against its .install-sums;
a user's own copy (no FORKED_FROM) and a folder without SKILL.md are left alone. ONEMORETASK_NO_OBSERVER=1 skips
it. Never fails: always exits 0, FAILED names a step that did not finish.

.install-sums is written in the format of this platform's installer, which compares it byte for byte: install.sh's
sorted `cksum` lines (POSIX CRC, size, ./path), or install.ps1's sorted "SHA256 \\path" lines joined by "\\n".
Either format is accepted when reading, so a copy installed by the other one still upgrades.
"""
import argparse
import hashlib
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NAME = "task-observer"
SUMS = ".install-sums"
IGNORED = {SUMS, ".DS_Store", "Thumbs.db", "desktop.ini"}  # Finder/Explorer add these; they are not edits
FAILED = []


def _crc_table():
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else (c << 1)
        table.append(c & 0xFFFFFFFF)
    return table


CRC_TABLE = _crc_table()


def cksum(data):
    """POSIX cksum CRC (not zlib's crc32): the data, then its length bytes, then inverted."""
    crc = 0
    t = CRC_TABLE
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ t[(crc >> 24) ^ b]
    n = len(data)
    while n:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ t[(crc >> 24) ^ (n & 0xFF)]
        n >>= 8
    return ~crc & 0xFFFFFFFF


def files(d):
    """(relative posix path, bytes) of every file the sums cover."""
    out = []
    for p in sorted(d.rglob("*")):
        if p.is_file() and p.name not in IGNORED:
            out.append((p.relative_to(d).as_posix(), p.read_bytes()))
    return out


def sums_sh(d):
    return "\n".join(sorted(f"{cksum(b)} {len(b)} ./{rel}" for rel, b in files(d))) + "\n"


def sums_ps1(d):
    return "\n".join(sorted(f"{hashlib.sha256(b).hexdigest().upper()} \\{rel.replace('/', chr(92))}"
                            for rel, b in files(d)))


def sums_native(d):
    return sums_ps1(d) if os.name == "nt" else sums_sh(d)


def unchanged(d, recorded):
    """True when the folder still matches the .install-sums either installer wrote."""
    def lines(text):
        return {ln.strip() for ln in text.splitlines() if ln.strip()}
    want = lines(recorded)
    return any(lines(f(d)) == want for f in (sums_sh, sums_ps1))


def install(claude, dry=False):
    src = REPO / "skills" / NAME
    dest = claude / "skills" / NAME
    if not (src / "SKILL.md").is_file():
        return
    if (dest / "SKILL.md").exists() and not (dest / "FORKED_FROM").exists():
        print(f"Kept your own {NAME} skill in {dest} as is.")
        return
    if not (dest / "SKILL.md").exists() and dest.is_dir() and any(dest.iterdir()):
        print(f"Note: {dest} has files but no SKILL.md — left alone, {NAME} skill not installed.")
        return
    sums = dest / SUMS
    if sums.exists() and not unchanged(dest, sums.read_text(encoding="utf-8", errors="replace")):
        print(f"Kept the {NAME} skill in {dest}: it was edited since the last install.")
        print("  To get the new version instead, delete that folder and run the install again.")
        return
    if dest.is_dir() and files(dest) == files(src):
        if not sums.exists() and not dry:
            sums.write_text(sums_native(dest), encoding="utf-8", newline="")
        print(f"The {NAME} skill in {dest} is already up to date.")
        return
    if dry:
        print(f"Would install the {NAME} skill in {dest}")
        return
    # Copy next to it first and swap only once the copy is whole, so a failed copy keeps the old skill.
    new = dest.with_name(f"{NAME}.new-{os.getpid()}")
    old = dest.with_name(f"{NAME}.old-{os.getpid()}")
    try:
        shutil.rmtree(new, ignore_errors=True)
        shutil.copytree(src, new, ignore=shutil.ignore_patterns(*IGNORED))
        (new / SUMS).write_text(sums_native(new), encoding="utf-8", newline="")
        if dest.exists():
            dest.rename(old)
        new.rename(dest)
    except BaseException:
        if not dest.exists() and old.exists():
            old.rename(dest)
        shutil.rmtree(new, ignore_errors=True)
        raise
    shutil.rmtree(old, ignore_errors=True)
    print(f"Installed the {NAME} skill in {dest}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=f"Install the bundled {NAME} skill.")
    ap.add_argument("--claude-dir", help="default: $CLAUDE_CONFIG_DIR or ~/.claude")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    args = ap.parse_args(argv)
    FAILED.clear()
    if os.environ.get("ONEMORETASK_NO_OBSERVER") == "1":
        return 0
    claude = Path(os.path.expanduser(args.claude_dir or os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))
    try:
        install(claude, args.dry_run)
    except Exception as e:
        print(f"Note: could not install the {NAME} skill ({e}) — skipped, the old copy is kept.")
        FAILED.append("observer")
    return 0


if __name__ == "__main__":
    sys.exit(main())
