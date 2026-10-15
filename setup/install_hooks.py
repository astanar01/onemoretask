"""Copy the Claude Code hooks, rules and skills in claude/ and skills/ into ~/.claude, and register the hooks.

Usage: python3 setup/install_hooks.py [--claude-dir DIR] [--dry-run]
The Claude dir defaults to $CLAUDE_CONFIG_DIR, else ~/.claude. ONEMORETASK_NO_TOOLS=1 skips everything.
A file we installed earlier is replaced by the new version unless it was edited since; any other file
already there is the user's own and is kept. Never fails the install: always exits 0. After main(), FAILED names
the steps that did not finish; setup/run_setup.py runs it again then.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MANIFEST = ".onemoretask-install-sums"
SKILLS = ("cmm-rules", "ctx-rules")
FAILED = []


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def shown(path):
    home = str(Path.home())
    p = str(path)
    return "~" + p[len(home):] if p == home or p.startswith(home + os.sep) else p


def shipped(claude):
    """(group, source, dest) for every file we ship."""
    out = []
    for group, src_dir, dest_dir in (("hooks", REPO / "claude" / "hooks", claude / "hooks"),
                                     ("rules", REPO / "claude" / "rules", claude / "rules")):
        for src in sorted(src_dir.rglob("*")):
            if src.is_file() and not src.name.startswith("."):
                out.append((group, src, dest_dir / src.relative_to(src_dir)))
    for name in SKILLS:
        out.append(("skills", REPO / "skills" / name / "SKILL.md", claude / "skills" / name / "SKILL.md"))
    return out


def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def install_files(claude, dry):
    manifest = claude / MANIFEST
    try:
        sums = json.loads(manifest.read_text(encoding="utf-8"))
        if not isinstance(sums, dict):
            sums = {}
    except (OSError, ValueError):
        sums = {}
    old_sums = dict(sums)
    report = {}  # group -> {"done": [...], "same": [...], "kept": [...], "dir": Path}
    for group, src, dest in shipped(claude):
        r = report.setdefault(group, {"done": [], "same": [], "kept": [], "dir": claude / group})
        key = dest.relative_to(claude).as_posix()
        new = sha256(src)
        if dest.exists():
            current = sha256(dest)
            if current == new:
                r["same"].append(key)
                sums[key] = new  # a lost manifest would otherwise keep every later update out as "yours"
                continue
            if sums.get(key) != current:  # edited since we installed it, or never ours
                r["kept"].append(dest.relative_to(r["dir"]).as_posix())
                continue
        if not dry:
            write_atomic(dest, src.read_bytes())
            if group == "hooks":
                dest.chmod(0o755)
        sums[key] = new
        r["done"].append(key)
    if not dry and sums != old_sums:
        write_atomic(manifest, (json.dumps(sums, indent=2, sort_keys=True) + "\n").encode())
    for group, r in report.items():
        verb = "Would install" if dry else "Installed"
        line = f"{verb} {len(r['done'])} {group} in {shown(r['dir'])}"
        extras = []
        if r["same"]:
            extras.append(f"{len(r['same'])} already up to date")
        if r["kept"]:
            extras.append(f"kept {len(r['kept'])} of yours: {', '.join(r['kept'])}")
        print(line + (f" ({'; '.join(extras)})" if extras else ""))


def script_name(command):
    m = re.search(r'([^/\\"\'\s]+\.(?:sh|mjs))', command or "")
    return m.group(1) if m else None


def merge_settings(claude, dry):
    path = claude / "settings.json"
    ours = json.loads((REPO / "claude" / "settings-hooks.json").read_text(encoding="utf-8"))["hooks"]
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            settings = None
        if not isinstance(settings, dict) or not isinstance(settings.get("hooks", {}), dict):
            print(f"Note: {shown(path)} is not valid settings JSON — left alone, hooks not registered.")
            return
    else:
        settings = {}
    hooks = settings.get("hooks", {})
    added = []
    for event, entries in ours.items():
        have = hooks.get(event, [])
        if not isinstance(have, list):
            print(f"Note: the {event} hooks in {shown(path)} are not a list — left alone.")
            continue
        for entry in entries:
            names = {script_name(h["command"]) for h in entry["hooks"]}
            registered = any(
                isinstance(e, dict) and e.get("matcher") == entry.get("matcher")
                and names & {script_name(h.get("command")) for h in e.get("hooks", []) if isinstance(h, dict)}
                for e in have)
            if not registered:
                have.append(entry)
                added.extend(sorted(names))
        hooks[event] = have
    if not added:
        print(f"All hooks already registered in {shown(path)}")
        return
    if dry:
        print(f"Would register {len(added)} hooks in {shown(path)}: {', '.join(added)}")
        return
    settings["hooks"] = hooks
    if path.exists():
        shutil.copy2(path, path.with_name("settings.json.bak-onemoretask"))
    write_atomic(path, (json.dumps(settings, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    print(f"Registered {len(added)} hooks in {shown(path)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Install the Claude Code hooks, rules and skills.")
    ap.add_argument("--claude-dir", help="default: $CLAUDE_CONFIG_DIR or ~/.claude")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    args = ap.parse_args(argv)
    if os.environ.get("ONEMORETASK_NO_TOOLS") == "1":
        print("ONEMORETASK_NO_TOOLS=1: skipped the Claude Code hooks, rules and skills.")
        return 0
    FAILED.clear()
    claude = Path(os.path.expanduser(args.claude_dir or os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude"))
    try:
        install_files(claude, args.dry_run)
    except Exception as e:
        # Registering hooks whose scripts did not land would break every session: stop here.
        print(f"Note: could not install the Claude Code hooks ({e}) — skipped.")
        FAILED.append("files")
        return 0
    try:
        merge_settings(claude, args.dry_run)
    except Exception as e:
        print(f"Note: could not register the hooks in settings.json ({e}) — skipped.")
        FAILED.append("settings")
    return 0


if __name__ == "__main__":
    sys.exit(main())
