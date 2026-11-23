"""Give Codex and Antigravity (agy) the same MCP servers, rules, skills and project skills as Claude Code.

Usage: python3 setup/install_clis.py [--home DIR]
setup/run_setup.py runs main() after install_tools and install_hooks. The CLI folders are <home>/.codex (or
$CODEX_HOME) and <home>/.gemini/config; a CLI's part runs only when its folder exists. The rules come from the Claude
dir ($CLAUDE_CONFIG_DIR or <home>/.claude) plus the CLI note in clis/. Never fails: problems print "Note: ...".
ONEMORETASK_NO_TOOLS=1 or ONEMORETASK_NO_CLI_SETUP=1 skip it all.

The server and the worker scripts also call ensure_rules() (keeps the rules files current, cheap) and
ensure_project_skills() (links a project's .claude/skills into .agents/skills). They load this file by path, so it
imports no module from the repo root.
"""
import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(HERE))
import install_hooks  # noqa: E402

FAILED = []
CLIS = ("codex", "antigravity")
BAK = ".bak-onemoretask"
BEFORE = ".before-onemoretask"
SIZE_CAP = 24000  # Antigravity's per-file rules limit; Codex gets the same (its own is 32 KiB)
MARKER = re.compile(r"^<!-- Made by the task board from (.*) \(sources sha256 ([0-9a-f]{12}), body sha256 "
                    r"([0-9a-f]{12})\)\. Edit those files, not this one\. -->$")
HOOK_NAME = "onemoretask-cmm"
HOOK_FILE = "onemoretask-cmm-session-start.py"
CTX_RULE_HEAD = ("---\ntrigger: always_on\n---\n\n"
                 "<!-- onemoretask: copied from the context-mode package by the task board's setup. -->\n\n")


def opted_out():
    return os.environ.get("ONEMORETASK_NO_TOOLS") == "1" or os.environ.get("ONEMORETASK_NO_CLI_SETUP") == "1"


def home_of(home):
    return Path(os.path.expanduser(str(home) if home else "~"))


def claude_of(home):
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(os.path.expanduser(env)) if env else home / ".claude"


def codex_home(home):
    env = os.environ.get("CODEX_HOME")
    return Path(os.path.expanduser(env)) if env else home / ".codex"


def cli_root(cli, home):
    """The folder that says the CLI is installed (the step never creates it)."""
    return codex_home(home) if cli == "codex" else home / ".gemini"


def cli_conf(cli, home):
    return codex_home(home) if cli == "codex" else home / ".gemini" / "config"


def shown(path, home):
    p, h = str(path), str(home)
    return "~" + p[len(h):] if p == h or p.startswith(h.rstrip(os.sep) + os.sep) else p


def note(msg):
    print(f"Note: {msg}")


def write_file(path, data):
    """S55: write only when the bytes differ; back the old bytes up first; one rename, mode kept, links followed.
    True when it wrote. Raises OSError."""
    path = Path(path)
    if isinstance(data, str):
        data = data.encode("utf-8")
    try:
        old = path.read_bytes()
    except FileNotFoundError:
        old = None
    if old == data:
        return False
    if old is not None:
        path.with_name(path.name + BAK).write_bytes(old)
    install_hooks.write_atomic(path, data)
    if old is None:
        os.chmod(os.path.realpath(path), 0o644)  # mkstemp makes 0600; a new rules or config file is not secret
    return True


def read_json_object(path, what):
    """(data, ok): a missing or empty file is {}; anything but a JSON object gives ok=False with a Note."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, True
    if not text.strip():
        return {}, True
    try:
        data = json.loads(text)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        note(f"{path} is not a JSON object — left alone, {what} skipped.")
        return None, False
    return data, True


def tool_path(name, home):
    local = home / ".local" / "bin" / name
    if local.is_file():
        return str(local)
    found = shutil.which(name)
    return os.path.abspath(found) if found else None


# ---------------------------------------------------------------- rules (S60-S64)

def rule_sources(cli, home, claude):
    """[(label, path)] in body order: the CLI note, CLAUDE.md, then rules/*.md by name. Missing ones are left out."""
    out = [(f"onemoretask/clis/{cli}.md", REPO / "clis" / f"{cli}.md"),
           (shown(claude / "CLAUDE.md", home), claude / "CLAUDE.md")]
    try:
        names = sorted(n for n in os.listdir(claude / "rules") if n.endswith(".md") and not n.startswith("."))
    except OSError:
        names = []
    out += [(shown(claude / "rules" / n, home), claude / "rules" / n) for n in names]
    res = []
    for label, p in out:
        try:
            res.append((label, p.read_bytes()))
        except OSError:
            pass
    return res


def sources_key(srcs):
    return ", ".join(l for l, _ in srcs), hashlib.sha256(b"".join(b for _, b in srcs)).hexdigest()[:12]


def build_rules(srcs):
    """(text, left_out labels). The first source is the CLI note."""
    labels, sh = sources_key(srcs)
    note_text = srcs[0][1].decode("utf-8", "replace").rstrip("\n") + "\n" if srcs and srcs[0][0].startswith(
        "onemoretask/") else ""
    rest = srcs[1:] if note_text else srcs
    left = []

    def render():
        body = "\n" + note_text
        if left:
            body += "\n" + "".join(f"Not included because of size: read {l} yourself before starting.\n" for l in left)
        for label, data in rest:
            if label not in left:
                body += f"\n## From {label}\n\n" + data.decode("utf-8", "replace").rstrip("\n") + "\n"
        bh = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
        marker = (f"<!-- Made by the task board from {labels} (sources sha256 {sh}, body sha256 {bh}). "
                  "Edit those files, not this one. -->")
        return marker + "\n" + body

    text = render()
    droppable = sorted(((len(d), l) for l, d in rest if "/rules/" in l), reverse=True)
    while len(text.encode("utf-8")) > SIZE_CAP and droppable:
        left.append(droppable.pop(0)[1])
        text = render()
    return text, left


def _rules(cli, home, claude, takeover):
    root, conf = cli_root(cli, home), cli_conf(cli, home)
    if not root.is_dir():
        return False
    target = conf / "AGENTS.md"
    srcs = rule_sources(cli, home, claude)
    labels, sh = sources_key(srcs)
    first = None
    try:
        with open(target, encoding="utf-8", errors="replace") as f:
            first = f.readline().rstrip("\n")
    except FileNotFoundError:
        pass
    m = MARKER.match(first) if first is not None else None
    if first is not None and not m:
        if not takeover:
            return False  # a hand-made file: only the full step takes it over (S63)
        before = target.with_name(target.name + BEFORE)
        dest = target.with_name(target.name + BAK) if os.path.lexists(before) else before
        os.replace(target, dest)
        print(f"Moved your own {shown(target, home)} to {shown(dest, home)}; the task board writes it from now on.")
    elif m:
        unchanged = m.group(1) == labels and m.group(2) == sh
        if unchanged and not takeover:
            return False
        have = target.read_text(encoding="utf-8", errors="replace")
        body = have[len(first) + 1:]
        if hashlib.sha256(body.encode("utf-8")).hexdigest()[:12] != m.group(3):
            note(f"{shown(target, home)} was edited by hand; the old one is kept as {target.name + BAK}. "
                 "Edit the files it names instead.")
        elif unchanged:
            return False
    text, left = build_rules(srcs)
    for label in left:
        note(f"{label} left out of {shown(target, home)} (over {SIZE_CAP} bytes); the file tells the CLI to read it.")
    if write_file(target, text):
        print(f"Wrote the {cli} rules in {shown(target, home)}")
        return True
    return False


def ensure_rules(cli, home=None, claude=None):
    """Keep <cli>'s rules file current (S64). Cheap, never raises; True when it wrote."""
    try:
        if opted_out() or cli not in CLIS:
            return False
        h = home_of(home)
        return _rules(cli, h, Path(claude) if claude else claude_of(h), False)
    except Exception as e:  # a sandboxed worker cannot write ~/.codex: fine
        note(f"the {cli} rules were not updated ({e}) — skipped.")
        return False


# ---------------------------------------------------------------- MCP servers (S56-S59)

def agy_servers(home, claude):
    servers = {}
    for name, args in (("codebase-memory-mcp", None), ("muninn", ["mcp"])):
        p = tool_path(name, home)
        if not p:
            note(f"{name} not found — not added to Antigravity.")
            continue
        servers[name] = {"command": p, **({"args": args} if args else {})}
    return servers


def context_mode_cmd():
    found = shutil.which("context-mode")
    return os.path.abspath(found) if found else None


def agy_plugin_has_context_mode(home):
    """True when agy lists an installed context-mode plugin whose folder still exists."""
    agy = shutil.which("agy") or (str(home / ".local/bin/agy") if (home / ".local/bin/agy").is_file() else None)
    if not agy:
        return False
    try:
        p = subprocess.run([agy, "plugin", "list", "--json"], stdin=subprocess.DEVNULL, capture_output=True,
                           text=True, timeout=60, env={**os.environ, "HOME": str(home)})
        plugins = json.loads(p.stdout) if p.returncode == 0 else []
    except (OSError, subprocess.SubprocessError, ValueError):
        return False
    return any(isinstance(x, dict) and x.get("name") == "context-mode" and x.get("path")
               and os.path.isdir(x["path"]) for x in plugins if isinstance(plugins, list))


def setup_agy(home, claude):
    conf = home / ".gemini" / "config"
    ctx = context_mode_cmd()
    plugin = agy_plugin_has_context_mode(home)
    if plugin:
        note("the context-mode plugin is installed in Antigravity — its own server is used, none added.")
    # MCP servers
    path = conf / "mcp_config.json"
    data, ok = read_json_object(path, "Antigravity MCP servers")
    if ok and not isinstance(data.get("mcpServers", {}), dict):
        note(f"{path} has no mcpServers object — left alone.")
        ok = False
    if ok:
        want = agy_servers(home, claude)
        if not plugin:
            if ctx:
                want["context-mode"] = {"command": ctx, "env": {"CONTEXT_MODE_PLATFORM": "antigravity-cli",
                                                                "CONTEXT_MODE_DIR": str(claude / "context-mode")}}
            else:
                note("context-mode not found on PATH — not added to Antigravity.")
        have = data.setdefault("mcpServers", {})
        added = [n for n in want if n not in have]
        for n in added:
            have[n] = want[n]
        if added and write_file(path, json.dumps(data, indent=2) + "\n"):
            print(f"Added MCP server {', '.join(added)} to {shown(path, home)}")
    # context-mode routing rule
    if ctx and not plugin:
        pkg_rule = Path(os.path.realpath(ctx)).parent / "configs" / "antigravity-cli" / "rules" / "context-mode.md"
        rule = conf / "rules" / "context-mode.md"
        try:
            text = CTX_RULE_HEAD + pkg_rule.read_text(encoding="utf-8")
        except OSError:
            note(f"{pkg_rule} not found — the context-mode rule is not added to Antigravity.")
            text = None
        if text is not None:
            try:
                mine = rule.read_text(encoding="utf-8")
            except FileNotFoundError:
                mine = None
            if mine is not None and not mine.startswith(CTX_RULE_HEAD):
                note(f"{shown(rule, home)} is your own — kept.")
            elif write_file(rule, text):
                print(f"Wrote the context-mode rule in {shown(rule, home)}")
    # SessionStart hook (S67)
    src = REPO / "clis" / "agy-hooks" / "cmm-session-start.py"
    copy = conf / "hooks" / HOOK_FILE
    if write_file(copy, src.read_bytes()):
        print(f"Copied the code map hook to {shown(copy, home)}")
    hooks_path = conf / "hooks.json"
    hooks, ok = read_json_object(hooks_path, "the Antigravity hook")
    if ok and HOOK_NAME not in hooks:
        hooks[HOOK_NAME] = {"SessionStart": [{"type": "command", "command": f"python3 {shlex.quote(str(copy))}"}]}
        if write_file(hooks_path, json.dumps(hooks, indent=2) + "\n"):
            print(f"Added the {HOOK_NAME} hook to {shown(hooks_path, home)}")


def toml_table(name, entry):
    lines = [f"[mcp_servers.{name}]", f"command = {json.dumps(entry['command'])}"]
    if entry.get("args"):
        lines.append("args = [" + ", ".join(json.dumps(a) for a in entry["args"]) + "]")
    if entry.get("env"):
        lines += ["", f"[mcp_servers.{name}.env]"] + [f"{k} = {json.dumps(v)}" for k, v in entry["env"].items()]
    return "\n".join(lines) + "\n"


def setup_codex(home, claude):
    try:
        import tomllib
    except ImportError:
        note("this Python has no tomllib (3.11+) — Codex MCP servers skipped.")
        return
    path = codex_home(home) / "config.toml"
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        text = ""
    try:
        have = tomllib.loads(text).get("mcp_servers", {})
    except tomllib.TOMLDecodeError as e:
        note(f"{shown(path, home)} does not parse ({e}) — Codex MCP servers not added.")
        return
    if not isinstance(have, dict):
        note(f"{shown(path, home)}: mcp_servers is not a table — left alone.")
        return
    tables, added = [], []
    for name, args in (("muninn", ["mcp"]), ("codebase-memory-mcp", None), ("context-mode", None)):
        if name in have:
            continue
        if name == "context-mode":
            cmd = context_mode_cmd()
            entry = cmd and {"command": cmd, "env": {"CONTEXT_MODE_DIR": str(claude / "context-mode"),
                                                     "CONTEXT_MODE_PLATFORM": "codex"}}
        else:
            cmd = tool_path(name, home)
            entry = cmd and {"command": cmd, "args": args}
        if not entry:
            note(f"{name} not found — not added to Codex.")
            continue
        tables.append(toml_table(name, entry))
        added.append(name)
    if not tables:
        return
    new = text + ("\n" if text and not text.endswith("\n") else "") + "".join("\n" + t for t in tables)
    try:
        tomllib.loads(new)
    except tomllib.TOMLDecodeError as e:
        note(f"adding {', '.join(added)} would break {shown(path, home)} ({e}) — nothing written.")
        return
    if write_file(path, new):
        print(f"Added MCP server {', '.join(added)} to {shown(path, home)}")


# ---------------------------------------------------------------- global skills (S65)

def link_global_skills(claude, dest, home):
    src = claude / "skills"
    roots = {str(src), os.path.realpath(src)}
    try:
        names = sorted(os.listdir(src))
    except OSError:
        names = []
    made = []
    for name in names:
        if name.startswith(".") or not (src / name / "SKILL.md").is_file():
            continue
        if os.path.lexists(dest / name):
            continue
        try:
            dest.mkdir(parents=True, exist_ok=True)
            os.symlink(str(src / name), dest / name)
            made.append(name)
        except OSError as e:
            note(f"could not link {name} into {shown(dest, home)} ({e}) — skipped.")
    removed = []
    try:
        entries = sorted(os.listdir(dest))
    except OSError:
        entries = []
    for name in entries:
        p = dest / name
        if name.startswith(".") or not p.is_symlink():
            continue
        t = os.path.normpath(os.path.join(dest, os.readlink(p)))
        if any(t.startswith(r + os.sep) for r in roots) and not os.path.exists(t):
            try:
                p.unlink()
                removed.append(name)
            except OSError:
                pass
    if made:
        print(f"Linked {len(made)} skills into {shown(dest, home)}")
    if removed:
        print(f"Removed {len(removed)} broken skill links from {shown(dest, home)}: {', '.join(removed)}")


# ---------------------------------------------------------------- project skills (S70-S72)

def _skills_of(project):
    sk = os.path.join(project, ".claude", "skills")
    try:
        names = sorted(os.listdir(sk))
    except OSError:
        return {}
    return {n: os.path.join(sk, n) for n in names
            if not n.startswith(".") and os.path.isfile(os.path.join(sk, n, "SKILL.md"))}


def _link_text(project, target, name):
    if os.path.isfile(os.path.join(target, ".claude", "skills", name, "SKILL.md")):
        return "../../.claude/skills/" + name
    return os.path.join(project, ".claude", "skills", name)


def _is_board_link(path, project, name):
    try:
        t = os.readlink(path)
    except OSError:
        return False
    return t in ("../../.claude/skills/" + name, os.path.join(project, ".claude", "skills", name))


def _same_skill(a, b):
    try:
        with open(os.path.join(a, "SKILL.md"), "rb") as f1, open(os.path.join(b, "SKILL.md"), "rb") as f2:
            return f1.read() == f2.read()
    except OSError:
        return False


def _git(target, *args, stdin=None):
    try:
        return subprocess.run(["git", "-C", target, *args], input=stdin, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None


def _ignored(target, rels):
    r = _git(target, "check-ignore", "--stdin", stdin="".join(r + "\n" for r in rels))
    if r is None or r.returncode not in (0, 1):
        return None
    return {l.strip() for l in r.stdout.splitlines() if l.strip()}


def _hide_from_git(target, names):
    """Names whose link path git ignores now (S72), or None when target is not in a git work tree."""
    r = _git(target, "rev-parse", "--show-toplevel", "--git-path", "info/exclude")
    if r is None or r.returncode != 0:
        return None
    top, excl = (r.stdout.splitlines() + ["", ""])[:2]
    rels = {n: ".agents/skills/" + n for n in names}
    ignored = _ignored(target, list(rels.values()))
    if ignored is None:
        return set()
    missing = [n for n in names if rels[n] not in ignored]
    if missing:
        sub = os.path.relpath(os.path.realpath(target), os.path.realpath(top)).replace(os.sep, "/")
        pre = "" if sub == "." else "/" + sub
        excl = excl if os.path.isabs(excl) else os.path.join(target, excl)
        try:
            with open(excl, encoding="utf-8") as f:
                old = f.read()
        except FileNotFoundError:
            old = ""
        except OSError as e:
            note(f"could not read {excl} ({e}) — no project skill links made there.")
            return set()
        have = set(old.splitlines())
        add = [p for p in (f"{pre}/.agents/skills/{n}" for n in missing) if p not in have]
        if add:
            try:
                os.makedirs(os.path.dirname(excl), exist_ok=True)
                with open(excl, "a", encoding="utf-8") as f:
                    f.write(("\n" if old and not old.endswith("\n") else "") + "".join(a + "\n" for a in add))
            except OSError as e:
                note(f"could not write {excl} ({e}) — no project skill links made there.")
                return set()
        ignored = _ignored(target, list(rels.values())) or set()
    return {n for n in names if rels[n] in ignored}


def ensure_project_skills(project, target=None):
    """Link <project>/.claude/skills/<name> into <target>/.agents/skills/<name> (S70-S72); target defaults to the
    project. Returns (linked, kept): the names linked there now, and those left alone because of a real clash.
    Never raises."""
    try:
        return _project_skills(os.path.abspath(project), os.path.abspath(target or project))
    except Exception as e:
        note(f"project skills not linked ({e}) — skipped.")
        return [], []


def _project_skills(project, target):
    skills = {} if opted_out() else _skills_of(project)
    agents = os.path.join(target, ".agents")
    sk = os.path.join(agents, "skills")
    if os.path.islink(agents) or os.path.islink(sk):
        if skills:
            note(f"{sk} is a link — project skills not linked there.")
        return [], []
    # Remove board links whose skill is gone (or all of them when opted out), fix one that points wrong.
    try:
        entries = sorted(os.listdir(sk))
    except OSError:
        entries = []
    for name in entries:
        p = os.path.join(sk, name)
        if _is_board_link(p, project, name):
            if name not in skills:
                os.unlink(p)
            elif os.readlink(p) != _link_text(project, target, name):
                os.unlink(p)
    if not skills:
        return [], []
    want = [n for n in skills if not os.path.lexists(os.path.join(sk, n))]
    if want:
        ok = _hide_from_git(target, want)
        ok = set(want) if ok is None else ok
        for n in want:
            if n not in ok:
                continue
            try:
                os.makedirs(sk, exist_ok=True)
                os.symlink(_link_text(project, target, n), os.path.join(sk, n))
            except OSError as e:
                note(f"could not link the {n} skill into {sk} ({e}) — skipped.")
    linked, kept = [], []
    for n, src in skills.items():
        p = os.path.join(sk, n)
        if _is_board_link(p, project, n):
            linked.append(n)
        elif os.path.lexists(p) and not (os.path.isdir(p) and not os.path.islink(p) and _same_skill(p, src)):
            kept.append(n)
    return linked, kept


# ---------------------------------------------------------------- the full step

def main(argv=None):
    ap = argparse.ArgumentParser(description="Give Codex and Antigravity the Claude Code tools, rules and skills.")
    ap.add_argument("--home", help="default: ~")
    a = ap.parse_args(argv)
    FAILED.clear()
    if opted_out():
        print("Skipped the Codex and Antigravity setup (ONEMORETASK_NO_TOOLS or ONEMORETASK_NO_CLI_SETUP is 1).")
        return 0
    home = home_of(a.home)
    claude = claude_of(home)
    for cli in CLIS:
        if not cli_root(cli, home).is_dir():
            continue
        steps = [("MCP servers", (lambda: setup_codex(home, claude)) if cli == "codex"
                  else (lambda: setup_agy(home, claude))),
                 ("skills", lambda: link_global_skills(claude, cli_conf(cli, home) / "skills", home)),
                 ("rules", lambda: _rules(cli, home, claude, True))]
        for label, step in steps:
            try:
                step()
            except Exception as e:
                note(f"{cli} {label} failed ({e}) — skipped.")
                FAILED.append(f"{cli} {label}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
