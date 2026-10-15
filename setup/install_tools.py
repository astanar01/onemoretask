"""Set up the Claude Code tools the board's sessions use: codebase-memory-mcp, Muninn and context-mode.

Usage: python3 setup/install_tools.py [--bin-dir DIR] [--claude-dir DIR] [--dry-run]
install.sh and install.ps1 run this. It never fails the install: every problem prints a "Note: ... skipped"
line and the script still exits 0. A tool you already have is kept as is. After main(), FAILED names the steps a
later run may fix (a failed download or plugin command); setup/run_setup.py runs it again then.
Skip it all with ONEMORETASK_NO_TOOLS=1.
"""
import argparse
import contextlib
import hashlib
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

VERSIONS = {
    "codebase-memory-mcp": {"repo": "DeusData/codebase-memory-mcp", "tag": "v0.11.0",
                            "asset": "codebase-memory-mcp-{os}-{arch}", "archive": True},
    "muninn": {"repo": "scrypster/muninndb", "tag": "v0.11.0",
               "asset": "muninn-{os}-{arch}", "archive": False},
}
MUNINN_PORT = 8750
OLLAMA_LINE = "MUNINN_OLLAMA_URL=ollama://localhost:11434/nomic-embed-text"
PLUGIN = "context-mode@context-mode"
PLUGIN_CMDS = [["plugin", "marketplace", "add", "mksglu/context-mode"],
               ["plugin", "install", PLUGIN, "--scope", "user"]]
FAILED = []


def platform_tag():
    """(os, arch) as the release assets name them, or None for a computer they don't build for."""
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    arch = {"x86_64": "amd64", "amd64": "amd64", "arm64": "arm64", "aarch64": "arm64"}.get(platform.machine().lower())
    return (system, arch) if system and arch else None


def exe_name(name, os_name):
    return name + ".exe" if os_name == "windows" else name


def asset_name(name, os_name, arch):
    spec = VERSIONS[name]
    asset = spec["asset"].format(os=os_name, arch=arch)
    if spec["archive"]:
        return asset + (".zip" if os_name == "windows" else ".tar.gz")
    return asset + (".exe" if os_name == "windows" else "")


def download(url, dest):
    try:
        with urllib.request.urlopen(url, timeout=60) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    except Exception:
        # A python.org Python on macOS has no CA certificates until "Install Certificates" is run, so urllib
        # fails there. curl uses the system's. The sha256 check guards the file either way.
        curl = shutil.which("curl")
        if not curl or subprocess.run([curl, "-fsSL", "-o", dest, url], stdin=subprocess.DEVNULL,
                                      timeout=1800).returncode != 0:
            raise


def run(cmd, timeout=60, capture=True):
    """Run cmd; returns (ok, output). Never raises."""
    try:
        # A daemon started by cmd would hold a captured pipe open forever, so those runs pass capture=False.
        out = subprocess.PIPE if capture else subprocess.DEVNULL
        p = subprocess.run(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=out, timeout=timeout, text=True)
        return p.returncode == 0, ((p.stdout or "") + (p.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


def version_of(path):
    """Last line --version prints (muninn logs a line to stderr first), or None if it does not run."""
    try:
        p = subprocess.run([path, "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    lines = (p.stdout.strip() or p.stderr.strip()).splitlines()
    return lines[-1].strip() if p.returncode == 0 and lines else None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def extract(archive, is_zip, member, dest):
    """Copy the archive's file whose base name is member to dest; False if there is none."""
    if is_zip:
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                if os.path.basename(info.filename) == member:
                    with z.open(info) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
                    return True
    else:
        with tarfile.open(archive) as t:
            for info in t.getmembers():
                if info.isfile() and os.path.basename(info.name) == member:
                    with t.extractfile(info) as src, open(dest, "wb") as out:
                        shutil.copyfileobj(src, out)
                    return True
    return False


def install_binary(name, bin_dir, dry):
    """Path of a working name binary (the user's own, or one just installed), or None."""
    tag = platform_tag()
    exe = exe_name(name, tag[0] if tag else "")
    target = os.path.join(bin_dir, exe)
    own = shutil.which(name) or (target if os.path.isfile(target) else None)
    if own:
        print(f"Kept your own {name} (version {version_of(own) or 'unknown'}) at {own}")
        return own
    if not tag:
        print(f"Note: no {name} build for this computer ({platform.system()} {platform.machine()}) — skipped.")
        return None
    spec = VERSIONS[name]
    asset = asset_name(name, *tag)
    base = f"https://github.com/{spec['repo']}/releases/download/{spec['tag']}/"
    if dry:
        print(f"Would download {name} {spec['tag']} ({asset}) to {target}")
        return target
    os.makedirs(bin_dir, exist_ok=True)
    tmps = []

    def tmp():
        fd, p = tempfile.mkstemp(prefix=f".{name}-", dir=bin_dir)
        os.close(fd)
        tmps.append(p)
        return p

    try:
        sums = tmp()
        download(base + "checksums.txt", sums)
        with open(sums) as f:
            want = {parts[-1].lstrip("*"): parts[0] for parts in (line.split() for line in f) if len(parts) == 2}
        if asset not in want:
            print(f"Note: {name} {spec['tag']} has no {asset} — skipped.")
            return None
        got = tmp()
        download(base + asset, got)
        if sha256(got) != want[asset].lower():
            print(f"Note: the {name} download did not match its checksum — refused, {name} skipped.")
            return None
        binary = got
        if spec["archive"]:
            binary = tmp()
            if not extract(got, asset.endswith(".zip"), exe, binary):
                print(f"Note: {asset} has no {exe} inside — {name} skipped.")
                return None
        os.chmod(binary, 0o755)
        os.replace(binary, target)
    except Exception as e:
        print(f"Note: could not install {name} ({e}) — skipped.")
        FAILED.append(name)
        return None
    finally:
        for p in tmps:
            if os.path.exists(p):
                os.remove(p)
    if tag[0] == "darwin":
        # A downloaded binary carries quarantine and may lack a valid signature; macOS would block it.
        run(["xattr", "-cr", target])
        run(["codesign", "--force", "--sign", "-", target])
    version = version_of(target)
    if not version:
        print(f"Note: {name} was put in {target} but does not run — skipped.")
        FAILED.append(name)
        # Left in place, the next run would keep it as the user's own and never download it again.
        with contextlib.suppress(OSError):
            os.remove(target)
        return None
    print(f"Installed {name} (version {version}) in {target}")
    return target


def claude_json_path(claude_dir):
    if os.environ.get("CLAUDE_CONFIG_DIR"):
        return os.path.join(claude_dir, ".claude.json")
    return os.path.join(os.path.dirname(os.path.abspath(claude_dir)), ".claude.json")


def register_mcp(claude_dir, servers, dry):
    """Add the servers ({name: entry}) to Claude Code's user-scope MCP list; a same-name entry is kept."""
    path = claude_json_path(claude_dir)
    data = {}
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError) as e:
            print(f"Note: could not read {path} ({e}) — MCP servers not added.")
            FAILED.append("MCP servers")
            return
        if not isinstance(data, dict) or not isinstance(data.get("mcpServers", {}), dict):
            print(f"Note: {path} is not in the expected shape — MCP servers not added.")
            FAILED.append("MCP servers")
            return
    have = data.setdefault("mcpServers", {})
    added = []
    for name, entry in servers.items():
        if name in have:
            print(f"Kept your own {name} MCP server entry in {path}")
        else:
            have[name] = entry
            added.append(name)
    if not added:
        return
    if dry:
        print(f"Would add MCP server {', '.join(added)} to {path}")
        return
    try:
        if os.path.exists(path):
            shutil.copy2(path, path + ".bak-onemoretask")
        fd, tmp = tempfile.mkstemp(prefix=".claude.json-", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.write("\n")
        if os.path.exists(path):
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except OSError as e:
        print(f"Note: could not write {path} ({e}) — MCP servers not added.")
        FAILED.append("MCP servers")
        return
    print(f"Added MCP server {', '.join(added)} to {path}")


def port_open(port):
    try:
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return True
    except OSError:
        return False


def add_env_line(path, line, dry):
    """Append line to the env file unless its key is set there. True if it was (or would be) appended."""
    key = line.split("=", 1)[0]
    text = ""
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            text = f.read()
        # `export KEY=…` sets the same key.
        if any(l.split("=", 1)[0].split() in ([key], ["export", key]) for l in text.splitlines()):
            return False
    if not dry:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(("\n" if text and not text.endswith("\n") else "") + line + "\n")
    return True


def setup_muninn(muninn, dry):
    """Turn on Ollama embeddings if Ollama is here, then make sure the Muninn server runs."""
    added = False
    ollama = shutil.which("ollama")
    if not ollama:
        print("Note: Ollama not found — Muninn will remember without embeddings (word search only). "
              "Install Ollama and run the installer again for semantic recall.")
    elif dry:
        print("Would run: ollama pull nomic-embed-text")
    else:
        ok, out = run([ollama, "pull", "nomic-embed-text"], timeout=600)
        print("Got the nomic-embed-text model for Muninn" if ok
              else f"Note: ollama pull nomic-embed-text failed ({out[-200:]}) — skipped.")
        if not ok:
            FAILED.append("ollama pull")
    if ollama:
        env = os.path.join(os.path.expanduser("~"), ".muninn", "muninn.env")
        try:
            added = add_env_line(env, OLLAMA_LINE, dry)
        except OSError as e:
            print(f"Note: could not write {env} ({e}) — Muninn embeddings not turned on.")
        if added:
            print(f"{'Would add' if dry else 'Added'} {OLLAMA_LINE} to {env}")
    if port_open(MUNINN_PORT):
        if added:
            if dry:
                print("Would restart the Muninn server to use embeddings")
            else:
                ok, _ = run([muninn, "restart"], capture=False)
                print("Restarted the Muninn server to use embeddings" if ok
                      else "Note: muninn restart failed — run it yourself to turn on embeddings.")
        else:
            print(f"Muninn server already running on port {MUNINN_PORT}")
    elif dry:
        print("Would run: muninn start")
    else:
        ok, _ = run([muninn, "start"], capture=False)
        print("Started the Muninn server" if ok else "Note: muninn start failed — skipped. Run it yourself later.")
        if not ok:
            FAILED.append("muninn start")


def plugin_enabled(claude_dir):
    try:
        with open(os.path.join(claude_dir, "settings.json"), encoding="utf-8") as f:
            return json.load(f).get("enabledPlugins", {}).get(PLUGIN) is True
    except (OSError, ValueError, AttributeError):
        return False


def find_claude():
    """The Claude CLI, also when PATH lacks ~/.local/bin (a board started from the desktop shortcut)."""
    names = ("claude.exe", "claude") if os.name == "nt" else ("claude",)
    local = [os.path.join(os.path.expanduser("~"), ".local", "bin", n) for n in names]
    return shutil.which("claude") or next((p for p in local if os.path.isfile(p)), None)


def setup_context_mode(claude_dir, dry):
    if plugin_enabled(claude_dir):
        print("Kept your context-mode plugin (already on)")
        return
    claude = find_claude()
    if not claude:
        print("Note: Claude Code not found — context-mode plugin skipped. Once Claude Code is installed, run:")
        for args in PLUGIN_CMDS:
            print("  claude " + " ".join(args))
        FAILED.append("context-mode")
        return
    for args in PLUGIN_CMDS:
        if dry:
            print("Would run: claude " + " ".join(args))
            continue
        ok, out = run([claude] + args, timeout=300)
        if not ok and "already" not in out.lower():
            print(f"Note: claude {' '.join(args)} failed ({out[-200:]}) — context-mode plugin skipped.")
            FAILED.append("context-mode")
            return
    if not dry:
        print("Installed the context-mode plugin")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Set up codebase-memory-mcp, Muninn and context-mode for Claude Code.")
    ap.add_argument("--bin-dir", default=os.path.join(os.path.expanduser("~"), ".local", "bin"))
    ap.add_argument("--claude-dir",
                    default=os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude"))
    ap.add_argument("--dry-run", action="store_true", help="print what would be done, change nothing")
    a = ap.parse_args(argv)
    if os.environ.get("ONEMORETASK_NO_TOOLS") == "1":
        print("Skipped the Claude Code tools (ONEMORETASK_NO_TOOLS=1).")
        return 0
    FAILED.clear()
    steps = [("codebase-memory-mcp", []), ("muninn", ["mcp"])]
    servers = {}
    for name, args in steps:
        try:
            path = install_binary(name, a.bin_dir, a.dry_run)
        except Exception as e:
            print(f"Note: {name} failed ({e}) — skipped.")
            FAILED.append(name)
            path = None
        if path:
            servers[name] = {"type": "stdio", "command": os.path.abspath(path), "args": args, "env": {}}
    for label, step in [("MCP servers", lambda: servers and register_mcp(a.claude_dir, servers, a.dry_run)),
                        ("Muninn server", lambda: "muninn" in servers
                         and setup_muninn(servers["muninn"]["command"], a.dry_run)),
                        ("context-mode plugin", lambda: setup_context_mode(a.claude_dir, a.dry_run))]:
        try:
            step()
        except Exception as e:
            print(f"Note: {label} failed ({e}) — skipped.")
            FAILED.append(label)
    return 0


if __name__ == "__main__":
    sys.exit(main())
