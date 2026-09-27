#!/usr/bin/env python3
"""Task board: a local kanban with subtasks, for any project folder.

Run from anywhere:  python3 server.py [--port 8765] [--project PATH]
Then open http://127.0.0.1:8765 in a browser.

The header's project menu switches boards: "Open folder…" picks any folder
(a native dialog, or a pasted path) and remembers it in
~/.config/task_board/projects.json. Each project keeps its own board in
<project>/.task_board/ (see reports.py; a repo that already has
tools/task_board/tasks.json keeps using that). Commit tasks.json and images/.
With no projects remembered yet, the git repo containing the current directory
(or --project) is opened. Opening index.html directly (file://) also works, but
then tasks live only in that browser's localStorage.

"Send to Claude" (task panel) starts `claude --bg` in the project folder with
the task's title, notes, subtasks and parent chain as the prompt. The session
shows in `claude agents`; `claude attach <id>` opens it. Permission mode
defaults to auto (--permission-mode to change). The model picker beside the
button passes --model (an alias like fable / opus / sonnet / haiku, or blank for
the CLI default, which the picker names: the "model" key from the settings files,
else a one-off `claude -p` probe of the account default, about $0.09, cached 24h in
~/.config/task_board/default_model.json). The session keeps it across replies; Haiku has no auto mode,
so it runs with manual permission prompts. A folder Claude does not trust yet makes the
card ask first; "Trust folder and send" sets hasTrustDialogAccepted for it in ~/.claude.json
(what accepting the CLI's trust prompt does) and retries.

A task ticked "Divide in subtasks / use subagents" (task fields `delegate`,
`subagentModel`) gets a delegation brief in its prompt (never plan mode): analyse the task, split
it, and hand the independent parts to subagents on the chosen model.

The agent posts progress / question / done onto its card with report.py. A
watcher thread polls `claude agents --json --all` every few seconds and, for
every remembered project, turns session state + the latest report into a phase:
working, needs_you (a question, a permission prompt, or it stopped without
reporting) or finished. Entering needs_you or finished raises a macOS
notification (none on other systems). A reply typed on the card stops the idle session and resumes it
with the reply as its next message (`claude --bg --resume` on a live session
would start a copy instead). If the session then stops without calling report.py, the
watcher copies its chat answer from the transcript onto the card (status "answer").

A message typed on the card while Claude works is logged as a "note" instead. Every session the board starts
gets inbox.py as a PostToolUse + Stop hook (--settings; a flagless --resume keeps it), which hands the note over
after the current tool call, or blocks the stop if the turn is ending. Notes left over (the turn ended first, or
the session predates the hook) are delivered by the watcher resuming the idle session.

"Review code" (a finished card in Review) starts a fresh `claude --bg` session on the task's model that picks
the task's commits from `git log --since=<launch>` (other sessions share the branch, so it matches them to the
card's report), runs the commit-review skill (skills/commit-review/SKILL.md; one cheap pass, not /code-review) on them at the
chosen budget (low / medium / high), and posts the findings on the card. The card then follows the reviewer, so a reply asking for fixes goes to it.

The server does not reload its own code: after server.py or reports.py change, restart it (Ctrl-C, then
onemoretask). Until then new /api routes answer a bare 404, which the page reports as "runs older code".

GET /api/observations?p=<pid> lists the open entries of the task-observer skill's observation logs
(<project>/skill-observations/ and <claude config dir>/skill-observations/, read-only; see observations.py).
When that skill is installed, the task prompt (build_prompt, not the review prompt) tells the session to run it
and log to <project>/skill-observations/.

Each card with a session also shows its prompt-cache countdown (cache_info reads the
last API call and its 5m/1h TTL tier from the session transcript under
~/.claude/projects/). Once it runs out the card says "cache cold": a reply then
re-reads the whole context at full price.

Images pasted into a card's notes save to <board>/images/ (listed on the task as
"images"); images pasted into a reply, or attached by the agent with
`report.py --image`, save to <board>/agent_reports/images/. The prompt and
replies hand Claude the absolute paths so it can open them with Read.
"""
import argparse
import base64
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import observations
import reports

WINDOWS = os.name == "nt"
HERE = os.path.dirname(os.path.abspath(__file__))
REGISTRY = os.path.expanduser("~/.config/task_board/projects.json")
TEXT = {"text": True, "encoding": "utf-8", "errors": "replace"}  # Windows would decode with the ANSI code page


def claude_cmd(found=None):
    """argv prefix that runs the Claude CLI. An npm .cmd shim runs through cmd.exe, which cuts a multi-line
    argument at its first newline, so a shim is swapped for the node script it wraps when that can be found."""
    exe = found or shutil.which("claude")
    if not exe:
        names = ("claude.exe", "claude") if WINDOWS else ("claude",)
        paths = [os.path.expanduser("~/.local/bin/" + n) for n in names]
        exe = next((x for x in paths if os.path.exists(x)), paths[0])
    if not exe.lower().endswith((".cmd", ".bat")):
        return [exe]
    here = os.path.dirname(exe)
    try:
        with open(exe, encoding="utf-8", errors="replace") as f:
            scripts = re.findall(r'%~?dp0%?\\([^"%*\r\n]+\.(?:c?js|mjs|exe))', f.read(), re.I)
    except OSError:
        scripts = []
    rel = next((x.replace("\\", os.sep) for x in scripts if x.lower() != "node.exe"),
               os.path.join("node_modules", "@anthropic-ai", "claude-code", "cli.js"))
    target = os.path.join(here, rel)
    if os.path.exists(target) and target.lower().endswith(".exe"):
        return [target]
    node = next((x for x in (os.path.join(here, "node.exe"), shutil.which("node")) if x and os.path.exists(x)), None)
    return [node, target] if node and os.path.exists(target) else [exe]


CLAUDE_CMD = claude_cmd()


def run_claude(args, **kw):
    cmd = CLAUDE_CMD + args
    if CLAUDE_CMD[0].lower().endswith((".cmd", ".bat")) and any(c in a for a in args for c in '\r\n"%'):
        raise RuntimeError(f"claude resolves to {CLAUDE_CMD[0]}, a cmd.exe script that cuts or mangles this prompt "
                           "(newlines, quotes, %). Install the native claude.exe (see https://claude.com/claude-code) "
                           "or keep node_modules/@anthropic-ai/claude-code/cli.js beside the shim.")
    if WINDOWS and len(subprocess.list2cmdline(cmd)) > 32000:
        raise ValueError("prompt too long for a Windows command line (32767 characters) — shorten the notes")
    return subprocess.run(cmd, capture_output=True, **TEXT, **kw)


def shell_path(path):
    """A path an agent types into its shell: on Windows that is Git Bash, which eats backslashes."""
    return path.replace("\\", "/") if WINDOWS else path


def replace_file(tmp, dst):
    """os.replace, retried on Windows while another process (a reader, a Claude session) has `dst` open."""
    for tries in range(40 if WINDOWS else 1, 0, -1):
        try:
            return os.replace(tmp, dst)
        except PermissionError:
            if tries == 1:
                os.unlink(tmp)
                raise
            time.sleep(0.05)


ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._\[\]-]{0,63}")
PERMISSION_MODE = "auto"
IDLE_GRACE = 20  # seconds a busy session's turn must have been over before a card note resumes it


# ---------------------------------------------------------------- projects
class Project:
    def __init__(self, path):
        self.path = os.path.realpath(path)
        self.id = hashlib.sha1(self.path.encode()).hexdigest()[:10]
        self.name = os.path.basename(self.path) or self.path
        self.board = reports.board_dir(self.path)
        self.data = os.path.join(self.board, "tasks.json")

    def info(self):
        return {"id": self.id, "path": self.path, "name": self.name, "board": self.board}


REG_LOCK = threading.Lock()


def _read_registry():
    try:
        with open(REGISTRY, encoding="utf-8") as f:
            return json.load(f).get("projects", [])
    except (OSError, ValueError):
        return []


def _write_registry(entries):
    os.makedirs(os.path.dirname(REGISTRY), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(REGISTRY), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"projects": entries}, f, indent=2)
    replace_file(tmp, REGISTRY)


def projects():
    """Remembered projects whose folder still exists, most recently opened first."""
    entries = sorted(_read_registry(), key=lambda e: e.get("opened", ""), reverse=True)
    return [Project(e["path"]) for e in entries if os.path.isdir(e["path"])]


def project(pid):
    p = next((x for x in projects() if x.id == pid), None)
    if not p:
        raise LookupError("unknown project (open its folder again)")
    return p


def open_project(path):
    path = os.path.expanduser(path.strip())
    if not os.path.isdir(path):
        raise ValueError(f"not a folder: {path}")
    p = Project(path)
    with REG_LOCK:
        entries = [e for e in _read_registry() if os.path.realpath(e["path"]) != p.path]
        entries.append({"path": p.path, "opened": datetime.now(timezone.utc).isoformat()})
        _write_registry(entries)
    return p


def forget_project(pid):
    with REG_LOCK:
        _write_registry([e for e in _read_registry() if Project(e["path"]).id != pid])


def pick_folder():
    """Native folder dialog; None when cancelled."""
    if WINDOWS and shutil.which("powershell"):
        ps = ("[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false;"
              "Add-Type -AssemblyName System.Windows.Forms;"
              "$d = New-Object System.Windows.Forms.FolderBrowserDialog;"
              "$d.Description = 'Choose a project folder for the task board';"
              "$top = New-Object System.Windows.Forms.Form -Property @{TopMost = $true};"
              "if ($d.ShowDialog($top) -eq 'OK') { $d.SelectedPath }")
        r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True, timeout=600,
                           **TEXT)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "folder dialog failed")
        return r.stdout.strip().lstrip("\ufeff") or None
    if shutil.which("osascript") is None:
        raise RuntimeError("no folder dialog on this system — paste the folder's path instead")
    r = subprocess.run(["osascript", "-e", "activate", "-e",
                        'POSIX path of (choose folder with prompt "Choose a project folder for the task board")'],
                       capture_output=True, timeout=600, **TEXT)
    if r.returncode != 0:
        if "-128" in r.stderr:  # user cancelled
            return None
        raise RuntimeError(r.stderr.strip() or "folder dialog failed")
    return r.stdout.strip()


# ---------------------------------------------------------------- prompt
def load_tasks(p):
    with open(p.data, encoding="utf-8") as f:
        return json.load(f)


def image_lines(names, folder, indent=""):
    return [f"{indent}- {shell_path(reports.image_path(folder, n))}" for n in names]


def build_prompt(p, state, t):
    by_id = {x["id"]: x for x in state["tasks"]}
    cols = {c["id"]: c for c in state["columns"]}
    chain = []
    parent = by_id.get(t.get("parent"))
    while parent:
        chain.insert(0, parent)
        parent = by_id.get(parent.get("parent"))

    def subtree(pid, depth):
        out = []
        for k in sorted((x for x in state["tasks"] if x.get("parent") == pid), key=lambda x: x["order"]):
            done = "x" if cols.get(k["column"], {}).get("done") else " "
            out.append(f"{'  ' * depth}- [{done}] {k['title']}")
            if k.get("notes"):
                out.append(f"{'  ' * depth}  notes: {k['notes']}")
            if k.get("images"):
                out.append(f"{'  ' * depth}  images:")
                out += image_lines(k["images"], reports.task_images(p.board), "  " * depth + "    ")
            out += subtree(k["id"], depth + 1)
        return out

    board_rel = os.path.relpath(p.board, p.path)
    lines = [f"Work on this task from the project task board ({board_rel}).", "", f"Task: {t['title']}",
             f"Task id: {t['id']}"]
    if chain:
        lines.append("Part of: " + " > ".join(x["title"] for x in chain))
        if chain[-1].get("notes"):
            lines.append(f"Parent notes: {chain[-1]['notes']}")
    if t.get("priority"):
        lines.append(f"Priority: {t['priority']}")
    if t.get("tags"):
        lines.append("Tags: " + ", ".join(t["tags"]))
    if t.get("notes"):
        lines += ["", "Notes:", t["notes"]]
    if t.get("images"):
        lines += ["", "Images pasted on this task (open each with the Read tool; they are part of the brief):",
                  *image_lines(t["images"], reports.task_images(p.board))]
    subs = subtree(t["id"], 0)
    if subs:
        lines += ["", "Subtasks ([x] = already done):", *subs]
    # The board may live outside this project's tree, so the command carries absolute paths.
    report = report_cmd(p, t["id"])
    if t.get("delegate"):
        lines += ["", *delegate_lines(t.get("subagentModel") or "", report)]
    lines.append("")
    if observations.installed():
        lines.append("Invoke the task-observer skill at the start and log its observations to "
                     f"{os.path.join(os.path.abspath(p.path), 'skill-observations')}. Skills it creates or "
                     "updates stay local: never commit " + ", ".join(d + "/" for d in OBSERVER_DIRS) +
                     " (the board keeps them out of git), and never commit a change it makes to a skill file.")
    lines += [f"Follow CLAUDE.md if the project has one. Commit your work; never push. Do not edit "
              f"{os.path.join(board_rel, 'tasks.json')} (the board owns it).", "", *card_lines(report)]
    return "\n".join(lines)


def card_lines(report):
    return ["Keep the card current with the report script (it shows on the board and alerts the user):",
              "- Format every message for reading on the card: short paragraphs and `- ` bullet lists separated by "
              "blank lines (real newlines inside the quoted argument), never one run-on block. A progress line may be "
              "a single sentence.",
              f'- `{report} progress "<one line>"` at each milestone.',
              f'- `{report} question "<the question, with options>"` when you need a decision, then END YOUR TURN; '
              "the answer arrives as your next message. Do not guess on decisions the user should make.",
              f'- `{report} done "<what you did, commits, what is left>"` as your last action when the task is finished.',
              "- The user reads only the card, never your chat output. Every turn that answers a reply from the card "
              f"(an explanation, an answer to a question, a follow-up change) ends with a `{report}` call carrying "
              "that full answer.",
              "- " + reports.LANGUAGE_RULE,
              "- Screenshots: any report about something you captured (an app or game frame, a UI shot, a render, a "
              "before/after) MUST attach the image files with `--image <file>` (repeatable), e.g. "
              f'`{report} progress "new HUD layout" --image /path/shot.png`. They show on the card.',
              # A quoted path opening a PowerShell line is a string, not a command, so report.py would never run.
              *(["- Run the report script with the Bash tool (Git Bash), never PowerShell: the command is bash syntax."]
                if WINDOWS else [])]


def notify(title, subtitle, message):
    if shutil.which("osascript") is None:
        return  # macOS only
    esc = lambda x: x.replace("\\", "\\\\").replace('"', '\\"')[:200]
    subprocess.run(["osascript", "-e", f'display notification "{esc(message)}" with title "{esc(title)}" '
                    f'subtitle "{esc(subtitle)}" sound name "Glass"'], capture_output=True, timeout=10)


def delegate_lines(model, report):
    if model and not MODEL.fullmatch(model):
        model = ""
    use = (f'- Run every subagent on the "{model}" model: pass `model: "{model}"` on each Agent call.' if model
           else "- Subagents use this session's model (leave the Agent `model` unset).")
    return ["Subagents (this task is marked \"divide in subtasks / use subagents\"):",
            "- Do NOT use plan mode (no EnterPlanMode / ExitPlanMode): work out the split yourself and carry on "
            "without waiting for approval.",
            "- Before changing anything, analyse the brief carefully and work out how to split it: the parts, what "
            "each needs to know, and which depend on others.",
            "- Delegate each part that can stand alone to a subagent (the Agent tool). Launch independent parts in one "
            "message so they run in parallel; run dependent parts after what they need. Keep tiny or tightly coupled "
            "parts yourself.",
            "- Give each subagent a self-contained prompt: the goal, the files, the constraints (incl. CLAUDE.md "
            "rules), how to verify, and what to return. Subagents do not commit or post to the card; you do.",
            use,
            f'- Post the plan (the parts, who does each) with `{report} progress "..."` before launching subagents, '
            "then check and integrate their results yourself before reporting done."]


# ---------------------------------------------------------------- agents
def model_args(model):
    if not model:
        return []
    if not MODEL.fullmatch(model):
        raise ValueError(f"bad model name: {model!r}")
    return ["--model", model]


DEFAULT_CACHE = os.path.expanduser("~/.config/task_board/default_model.json")
DEFAULT_TTL = 24 * 3600
_default_lock = threading.Lock()
_default_probing = False


def settings_model(path):
    """The "model" the CLI would start with from env or settings files (highest precedence first), or None."""
    if os.environ.get("ANTHROPIC_MODEL"):
        return os.environ["ANTHROPIC_MODEL"]
    for f in (os.path.join(path, ".claude", "settings.local.json"), os.path.join(path, ".claude", "settings.json"),
              os.path.expanduser("~/.claude/settings.json")):
        try:
            with open(f, encoding="utf-8") as fh:
                m = json.load(fh).get("model")
        except (OSError, ValueError, AttributeError):
            continue
        if m:
            return m
    return None


def _probe_account_default():
    global _default_probing
    try:
        # No tools, MCP or project dir, to keep the probe cheap; the model is all we read.
        r = run_claude(["-p", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                        "--disable-slash-commands", "--tools", "", "--output-format", "json", "--max-turns", "1",
                        "Reply: ok"], cwd=tempfile.gettempdir(), timeout=180)
        models = list(json.loads(r.stdout).get("modelUsage") or {})
        if models:
            os.makedirs(os.path.dirname(DEFAULT_CACHE), exist_ok=True)
            with open(DEFAULT_CACHE, "w", encoding="utf-8") as fh:
                json.dump({"model": models[0], "at": time.time()}, fh)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as e:
        print("default model probe:", e)
    finally:
        _default_probing = False


def default_model(p):
    """{"model", "source"} of what a launch without --model gets; model is None while the probe runs."""
    m = settings_model(p.path)
    if m:
        return {"model": m, "source": "settings"}
    try:
        with open(DEFAULT_CACHE, encoding="utf-8") as fh:
            cached = json.load(fh)
    except (OSError, ValueError):
        cached = {}
    global _default_probing
    if time.time() - cached.get("at", 0) > DEFAULT_TTL:
        with _default_lock:
            if not _default_probing:
                _default_probing = True
                threading.Thread(target=_probe_account_default, daemon=True).start()
    return {"model": cached.get("model"), "source": "account"}


CLAUDE_JSON = os.path.expanduser("~/.claude.json")


class UntrustedError(Exception):
    """The CLI refuses `--bg` in a folder whose trust prompt was never accepted."""


def trust_folder(p):
    """Record what accepting Claude's trust prompt records: projects[<path>].hasTrustDialogAccepted.
    Running sessions rewrite this file too, so read-modify-replace in one short step, then read back."""
    with open(CLAUDE_JSON, encoding="utf-8") as f:
        cfg = json.load(f)
    keys = {p.path, shell_path(p.path)}  # unverified which spelling the Windows CLI keys projects by
    for k in keys:
        cfg.setdefault("projects", {}).setdefault(k, {})["hasTrustDialogAccepted"] = True
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(CLAUDE_JSON), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.chmod(tmp, os.stat(CLAUDE_JSON).st_mode & 0o777)
    replace_file(tmp, CLAUDE_JSON)
    with open(CLAUDE_JSON, encoding="utf-8") as f:
        saved = json.load(f).get("projects", {})
        if not all(saved.get(k, {}).get("hasTrustDialogAccepted") for k in keys):
            raise RuntimeError("trust was not saved (a running Claude session rewrote ~/.claude.json) — retry")


def launch(p, task_id, model=""):
    state = load_tasks(p)
    t = next((x for x in state["tasks"] if x["id"] == task_id), None)
    if not t:
        raise LookupError("no such task (save first?)")
    if t.get("agent"):
        raise PermissionError("already sent to Claude — the task is locked; reply on the card instead")
    name = "task: " + t["title"][:60]
    if observations.installed():
        exclude_observer_dirs(p)
    prompt = build_prompt(p, state, t)
    path = reports.prompt_path(p.board, task_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(prompt)
    aid = start_session(p, task_id, name, prompt, model)
    reports.append(p.board, task_id, "launch", f"Sent to Claude (session {aid}, model {model or 'default'})",
                   "you", session=aid)
    WATCH.expect(p, task_id, aid)
    return aid


OBSERVER_DIRS = ("skill-observations", "skill-updates", ".claude/skills")   # log, staged skills, project skills


def exclude_observer_dirs(p):
    """Keep the task-observer log and the skills it stages or creates out of the project's commits: list them in
    the repo's info/exclude (local only, unlike .gitignore). Files git already tracks are not affected. Best effort; a folder that is not a git repo is skipped."""
    try:
        r = subprocess.run(["git", "-C", p.path, "rev-parse", "--git-path", "info/exclude"],
                           capture_output=True, timeout=10, **TEXT)
        if r.returncode != 0:
            return
        path = os.path.join(p.path, r.stdout.strip())
        text = open(path, encoding="utf-8", errors="replace").read() if os.path.isfile(path) else ""
        missing = [f"/{d}/" for d in OBSERVER_DIRS if f"/{d}/" not in text.splitlines()]
        if missing:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(("" if not text or text.endswith("\n") else "\n") + "\n".join(missing) + "\n")
    except (OSError, subprocess.SubprocessError):
        pass


def inbox_settings(p, task_id):
    """--settings JSON that installs inbox.py, so card messages sent mid-work reach the running session."""
    hook = [{"hooks": [{"type": "command", "command": script_cmd(p, task_id, "inbox.py")}]}]
    return json.dumps({"hooks": {"PostToolUse": hook, "Stop": hook}})


def start_session(p, task_id, name, prompt, model):
    r = run_claude(["--bg", "-n", name, "--permission-mode", PERMISSION_MODE, *model_args(model),
                    "--settings", inbox_settings(p, task_id), prompt], cwd=p.path, timeout=60)
    out = ANSI.sub("", r.stdout + r.stderr)
    m = re.search(r"backgrounded\s*·\s*([0-9a-f]+)", out)
    if r.returncode != 0 or not m:
        if re.search(r"not trusted", out, re.I):
            raise UntrustedError(p.path)
        raise RuntimeError(out.strip() or f"claude exited {r.returncode}")
    return m.group(1)


REVIEW_LEVELS = ("low", "medium", "high")


def task_commits(p, since):
    """[(short sha, subject)] on HEAD committed since `since` (ISO time), newest first."""
    r = subprocess.run(["git", "-C", p.path, "log", f"--since={since}", "--format=%h %s", "HEAD"],
                       capture_output=True, timeout=10, **TEXT)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "git log failed")
    return [tuple(x.split(" ", 1)) if " " in x else (x, "") for x in r.stdout.splitlines() if x]


def review(p, task_id, level):
    """Start a fresh session that code-reviews the task's commits and posts the findings on its card. The card
    then follows the reviewer, so a reply ("fix 1 and 3") goes to the session that holds the findings."""
    if level not in REVIEW_LEVELS:
        raise ValueError(f"bad review level: {level!r}")
    state = load_tasks(p)
    t = next((x for x in state["tasks"] if x["id"] == task_id), None)
    if not t or not t.get("agent"):
        raise LookupError("this task was never sent to Claude")
    info = WATCH.snapshot(p.id).get(task_id)
    if info and info["phase"] == "working":
        raise PermissionError("Claude is still working — wait until it stops")
    log = reports.read(p.board, task_id)
    since = next((e["at"] for e in reversed(log) if e.get("status") == "launch"), t["agent"].get("started"))
    commits = task_commits(p, since)
    if not commits:
        raise PermissionError("No commits since the task was sent — nothing to review")
    # The task's own report, not an earlier reviewer's findings: stop at the first review.
    first_review = next((i for i, e in enumerate(log) if e.get("status") == "review"), len(log))
    report = next((e["message"] for e in reversed(log[:first_review]) if e.get("from") == "claude"
                   and e.get("status") in ("done", "answer")), "(no final report)")
    board_rel = os.path.relpath(p.board, p.path)
    rep = report_cmd(p, task_id)
    prompt = "\n".join([
        f"Code review for a task on the project task board ({board_rel}).", "",
        f"Task: {t['title']}", f"Task id: {task_id}", *(["", "Task notes:", t["notes"]] if t.get("notes") else []),
        "", f"Another Claude session did this task. It was sent at {since}. Its last report on the card:", report, "",
        "Commits on HEAD since then, newest first. Other sessions commit to the same branch, so some may belong "
        "to other tasks:", *[f"- {sha} {subj}" for sha, subj in commits], "",
        "Steps:",
        f'1. Run the commit-review skill (the Skill tool, skill "commit-review", args "{level}" plus the candidate '
        f"shas above). If that skill is not installed, Read {shell_path(os.path.join(HERE, 'skills', 'commit-review'))}"
        "/SKILL.md "
        "and follow it. It keeps only the commits that match the report above. Keep to its turn budget. Do not use "
        "/code-review, subagents or workflows. Review only in this turn: no file changes, no commits.",
        f'2. Post its report with `{rep} done "..."`, naming the commits you reviewed. If nothing survived, say so. '
        "End by asking which findings to fix.",
        "If a later reply asks for fixes: make them, verify them, commit (never push), and report done.", "",
        f"Follow CLAUDE.md if the project has one. Do not edit {os.path.join(board_rel, 'tasks.json')} "
        "(the board owns it).", "", *card_lines(rep)])
    aid = start_session(p, task_id, "review: " + t["title"][:58], prompt, t["agent"].get("model") or "")
    reports.append(p.board, task_id, "review", f"Code review started ({level}, session {aid})", "you", session=aid)
    WATCH.expect(p, task_id, aid)
    return aid


def task_prompt(p, task_id):
    path = reports.prompt_path(p.board, task_id)
    try:
        with open(path, encoding="utf-8") as f:
            return {"prompt": f.read(), "saved": True}
    except FileNotFoundError:
        pass
    state = load_tasks(p)
    t = next((x for x in state["tasks"] if x["id"] == task_id), None)
    if not t:
        raise LookupError("no such task")
    return {"prompt": build_prompt(p, state, t), "saved": False}


def git_info(p):
    def git(*args):
        try:
            r = subprocess.run(["git", "-C", p.path, *args], capture_output=True, timeout=5, **TEXT)
        except (OSError, subprocess.SubprocessError):
            return None
        return r.stdout.strip() if r.returncode == 0 else None

    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    if branch is None:
        return {"branch": None, "tag": None, "dirty": False}
    if branch == "HEAD":
        branch = git("rev-parse", "--short", "HEAD")
    return {"branch": branch, "tag": git("describe", "--tags", "--abbrev=0") or None,
            "dirty": bool(git("status", "--porcelain"))}


def phase_of(session, log, launched_at):
    """(phase, reason) from the CLI's session record and the card's message log."""
    log = [e for e in log if e.get("status") != "note"]  # a note sent mid-work changes nothing about the turn
    last = log[-1] if log else {}
    st = session and session.get("state")
    # "status" is the live turn (busy / idle; absent once the process exits). "state" is the CLI's summary and
    # can stay "working" on an idle session after a --resume, so it only counts when there is no live status.
    live = session and session.get("status")
    busy = live == "busy" and st != "blocked" or not live and st == "working"
    # "done" is the agent's last action, so it wins over "busy": a background shell that never exits (a headless
    # Chrome screenshot) keeps the session "busy" forever.
    if session is not None and last.get("from") == "claude" and last.get("status") in ("done", "answer"):
        return "finished", last["message"]
    # Just after a launch or reply the session is still starting (absent, or stopped from the reply's stop).
    if busy or (last.get("from") == "you" and time.time() - launched_at < 45):
        return "working", ""
    if session is None:
        return "gone", "Session was removed"
    # An explicit report beats the CLI's own guess (it marks some finished sessions "blocked").
    if last.get("status") == "question":
        return "needs_you", last["message"]
    if last.get("status") in ("done", "answer"):
        return "finished", last["message"]
    if st == "blocked":
        # The CLI marks a turn "blocked" when its own end-of-turn summary sounds like it waits on you
        # ("awaiting reload test"), so a finished turn that ended on a progress note lands here too.
        detail = job_detail(session.get("id"))
        if last.get("from") == "claude" and last.get("status") == "progress":
            return "needs_you", "Stopped after a progress note without reporting done — reply to check on it" + (f" (CLI: {detail})" if detail else "")
        if detail:
            return "needs_you", f"Stopped in the session: {detail} — reply, or attach if it asks for a permission"
        return "needs_you", "Waiting on a permission prompt or a question in the session — attach to answer"
    return "needs_you", "Stopped without reporting — attach or reply to check on it"


def job_detail(job_id):
    """The CLI's one-line summary of where a background session stopped ('' if unknown)."""
    try:
        path = os.path.join(os.path.expanduser("~/.claude/jobs"), job_id or "", "state.json")
        with open(path, encoding="utf-8") as f:
            return (json.load(f).get("detail") or "").strip()
    except (OSError, ValueError, AttributeError):
        return ""


def list_sessions(cwd):
    r = run_claude(["agents", "--json", "--all"], cwd=cwd, timeout=30)
    return json.loads(r.stdout or "[]")


PROJECTS_DIR = os.path.expanduser("~/.claude/projects")
TAIL_BYTES = 4 << 20
_cache_memo = {}  # transcript path -> ((mtime, size), info)


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def transcript_path(session_id):
    if not session_id:
        return None
    paths = [os.path.join(PROJECTS_DIR, d, session_id + ".jsonl") for d in os.listdir(PROJECTS_DIR)]
    paths = [x for x in paths if os.path.exists(x)]
    return max(paths, key=os.path.getmtime) if paths else None


def transcript_tail(path):
    """Main-thread transcript entries from the last TAIL_BYTES of the file."""
    with open(path, "rb") as f:
        f.seek(max(0, os.path.getsize(path) - TAIL_BYTES))
        lines = f.read().decode("utf-8", "replace").splitlines()
    entries = []
    for line in lines:
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("timestamp") and not e.get("isSidechain"):
            entries.append(e)
    return entries


def last_answer(session_id, reply_text):
    """The chat text that ends the session's turn answering `reply_text` (the text after its last tool
    result), or "". Used when an agent answers a card reply in chat and never calls report.py."""
    path = transcript_path(session_id)
    if not path:
        return ""
    texts, found = [], False
    for e in transcript_tail(path):
        content = (e.get("message") or {}).get("content")
        if e.get("type") == "user":
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
            if any(isinstance(b, dict) and b.get("type") == "text" and b.get("text", "").startswith(reply_text)
                   for b in blocks):
                texts, found = [], True
            elif any(isinstance(b, dict) and b.get("type") == "tool_result" for b in blocks):
                texts = []
            elif found and not e.get("isMeta") and any(isinstance(b, dict) and b.get("type") == "text" for b in blocks):
                break  # a later message typed into the session itself; its answer is not the card's
        elif found and e.get("type") == "assistant" and isinstance(content, list):
            texts += [b["text"].strip() for b in content if isinstance(b, dict) and b.get("type") == "text"
                      and b.get("text", "").strip()]
    return "\n\n".join(texts)


_turn_memo = {}  # transcript path -> ((mtime, size), ended at)


def turn_ended_at(session_id):
    """When the session's last turn ended (epoch s), or None while a turn runs. A session that ends its turn to wait
    on a background shell still lists as busy, and its inbox hook never fires again until that shell exits."""
    path = transcript_path(session_id)
    if not path:
        return None
    st = os.stat(path)
    memo = _turn_memo.get(path)
    if memo and memo[0] == (st.st_mtime, st.st_size):
        return memo[1]
    ended = None
    for e in reversed(transcript_tail(path)):
        kind = e.get("type")
        if kind == "system" and e.get("subtype") == "turn_duration":
            ended = _ts(e["timestamp"])
            break
        # A queued background-task notification starts the next turn.
        if kind in ("user", "assistant") or kind == "attachment" and (e.get("attachment") or {}).get("type") == "queued_command":
            break
    _turn_memo[path] = ((st.st_mtime, st.st_size), ended)
    return ended


def cache_info(session_id):
    """Prompt-cache state of a session's LAST API call, from its transcript:
    {"at": call start (epoch s), "ttl": seconds, "expires": epoch s, "tokens": context size}, or None.
    The API refreshes a cache entry's TTL on every read; the TTL tier (5m or 1h) shows in the usage's
    cache_creation split. The call start is taken as the entry just before the reply (conservative)."""
    path = transcript_path(session_id)
    if not path:
        return None
    st = os.stat(path)
    memo = _cache_memo.get(path)
    if memo and memo[0] == (st.st_mtime, st.st_size):
        return memo[1]
    entries = transcript_tail(path)
    info, ttl = None, None
    for i in range(len(entries) - 1, -1, -1):
        m = entries[i].get("message") or {}
        usage = entries[i].get("type") == "assistant" and m.get("usage")
        if not usage:
            continue
        split = usage.get("cache_creation") or {}
        if ttl is None and split.get("ephemeral_1h_input_tokens"):
            ttl = 3600
        elif ttl is None and split.get("ephemeral_5m_input_tokens"):
            ttl = 300
        if info is None:
            j = i
            while j > 0 and (entries[j - 1].get("message") or {}).get("id") == m.get("id"):
                j -= 1
            at = _ts(entries[j - 1]["timestamp"] if j > 0 else entries[j]["timestamp"])
            tokens = sum(usage.get(k) or 0 for k in
                         ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
            info = {"at": at, "tokens": tokens}
        if ttl is not None:
            break
    if info:
        info["ttl"] = ttl or 300  # tier not seen in the tail: assume the short one so "cold" is never late
        info["expires"] = info["at"] + info["ttl"]
    _cache_memo[path] = ((st.st_mtime, st.st_size), info)
    return info


SUB_TAIL = 256 << 10
_sub_memo = {}  # subagent transcript path -> ((mtime, size), row)


def _action(block):
    """'Edit · index.html' / 'Bash · Run the tests' from a tool_use block."""
    a = block.get("input") or {}
    arg = (os.path.basename(a["file_path"]) if a.get("file_path") else
           a.get("description") or a.get("pattern") or a.get("query") or a.get("url") or "")
    return (block.get("name") or "tool") + (" · " + str(arg).splitlines()[0][:80] if arg else "")


def subagents(session_id):
    """The session's Agent-tool subagents from <session>/subagents/agent-<id>.{meta.json,jsonl}:
    [{id, name, type, started, updated, finished, action}], oldest first. finished = its last entry is an
    end_turn reply; a subagent cut off mid-run stays unfinished (the board shows it as stopped)."""
    path = transcript_path(session_id)
    folder = path and os.path.join(path[:-len(".jsonl")], "subagents")
    if not folder or not os.path.isdir(folder):
        return []
    rows = []
    for name in os.listdir(folder):
        if not name.endswith(".jsonl"):
            continue
        f = os.path.join(folder, name)
        st = os.stat(f)
        memo = _sub_memo.get(f)
        if memo and memo[0] == (st.st_mtime, st.st_size):
            rows.append(memo[1])
            continue
        try:
            with open(f[:-len(".jsonl")] + ".meta.json", encoding="utf-8") as m:
                meta = json.load(m)
        except (OSError, ValueError):
            meta = {}
        with open(f, "rb") as fh:
            first = fh.readline()
            fh.seek(max(0, st.st_size - SUB_TAIL))
            tail = fh.read().decode("utf-8", "replace").splitlines()
        entries = []
        for line in tail:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict) and e.get("type") in ("user", "assistant"):
                entries.append(e)
        try:
            started = _ts(json.loads(first)["timestamp"])
        except (ValueError, KeyError, TypeError):
            started = st.st_mtime
        last = entries[-1] if entries else {}
        msg = last.get("message") or {}
        action = next((_action(b) for e in reversed(entries) for b in reversed((e.get("message") or {}).get("content") or [])
                       if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") != "SubagentHandback"), "")
        row = {"id": name[len("agent-"):-len(".jsonl")], "name": meta.get("description") or "Subagent",
               "type": meta.get("agentType") or "", "started": started,
               "updated": _ts(last["timestamp"]) if last.get("timestamp") else st.st_mtime,
               "finished": last.get("type") == "assistant" and msg.get("stop_reason") == "end_turn",
               "action": action}
        _sub_memo[f] = ((st.st_mtime, st.st_size), row)
        rows.append(row)
    return sorted(rows, key=lambda r: r["started"])


class Watcher:
    """Polls session state for every task with an agent, in every remembered project; the board reads the cache."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}       # (project id, task id) -> {id, sessionId, state, phase, reason, log}
        self.launched = {}    # agent id -> launch time, for sessions not listed yet
        self.primed = False   # first pass only records, so a restart doesn't re-alert old sessions
        self.delivering = set()  # (project id, task id) whose leftover notes are being handed over

    def _deliver(self, p, task_id, info, waiting=False):
        try:
            deliver_notes(p, task_id, info, waiting)
        finally:
            self.delivering.discard((p.id, task_id))

    def expect(self, p, task_id, agent_id):
        with self.lock:
            self.launched[agent_id] = time.time()
            self.cache[(p.id, task_id)] = {"id": agent_id, "sessionId": None, "state": "starting", "phase": "working",
                                           "reason": "", "log": reports.read(p.board, task_id)}

    def poll(self):
        work, done_cols = [], set()
        for p in projects():
            try:
                state = load_tasks(p)
            except (OSError, ValueError):
                continue
            work += [(p, t) for t in state["tasks"] if t.get("agent")]
            done_cols |= {c["id"] for c in state.get("columns", []) if c.get("done")}
        sessions = {s["id"]: s for s in list_sessions(HERE)} if work else {}
        fresh = {}
        for p, t in work:
            log = reports.read(p.board, t["id"])
            aid = next((e["session"] for e in reversed(log) if e.get("session")), t["agent"]["id"])
            s = sessions.get(aid)
            phase, reason = phase_of(s, log, self.launched.get(aid, 0))
            said = [e for e in log if e.get("status") != "note"]
            if phase == "needs_you" and s and s.get("state") != "blocked" and said and said[-1].get("from") == "you":
                # It stopped after a card reply without calling report.py: its answer is only in the chat, which
                # the user never sees. Copy that answer onto the card.
                try:
                    text = last_answer(s.get("sessionId"), said[-1]["message"])
                except OSError:
                    text = ""
                if text:
                    reports.append(p.board, t["id"], "answer", text, "claude")
                    log = reports.read(p.board, t["id"])
                    phase, reason = phase_of(s, log, self.launched.get(aid, 0))
            delivered = reports.delivered(p.board, t["id"])
            busy = bool(s) and s.get("status") == "busy"
            last_claude = next((e["at"] for e in reversed(log) if e.get("from") == "claude"), "")
            if phase == "finished" and busy and any(last_claude < e["at"] <= delivered
                                                    for e in reports.pending_notes(log, "")):
                phase, reason = "working", ""  # the Stop hook handed it a note after its done report
            pending = s and reports.pending_notes(log, delivered) and (p.id, t["id"]) not in self.delivering
            # Busy but its turn is over: it waits on a background shell (maybe hung), so only a resume reaches it.
            waiting = pending and busy and phase == "working" and time.time() - (
                turn_ended_at(s.get("sessionId")) or time.time()) > IDLE_GRACE
            if pending and (phase in ("finished", "needs_you") and not busy or waiting):
                self.delivering.add((p.id, t["id"]))
                threading.Thread(target=self._deliver, args=(p, t["id"], {"id": aid, "sessionId": s.get("sessionId")},
                                                             waiting), daemon=True).start()
            try:
                cache = cache_info(s and s.get("sessionId"))
            except OSError:
                cache = None
            try:
                subs = subagents(s and s.get("sessionId"))
            except OSError:
                subs = []
            fresh[(p.id, t["id"])] = {"id": aid, "sessionId": s and s.get("sessionId"), "state": s and s.get("state"),
                                      "phase": phase, "reason": reason, "log": log, "cache": cache, "subagents": subs,
                                      "closed": t.get("column") in done_cols, "delivered": delivered}
        with self.lock:
            old, self.cache = self.cache, fresh
            primed, self.primed = self.primed, True
        if not primed:
            return
        for p, t in work:
            key = (p.id, t["id"])
            was, now_ = (old.get(key) or {}).get("phase"), fresh[key]["phase"]
            if now_ == was or (was is None and fresh[key]["id"] not in self.launched):
                continue
            if now_ == "needs_you":
                notify("Claude needs you", f"{p.name}: {t['title']}", fresh[key]["reason"])
            elif now_ == "finished":
                notify("Claude finished", f"{p.name}: {t['title']}", fresh[key]["reason"])

    def run(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # noqa: BLE001 — keep watching through a bad poll
                print("watcher:", e)
            time.sleep(4)

    def snapshot(self, pid):
        with self.lock:
            return {tid: v for (p, tid), v in self.cache.items() if p == pid}

    def attention(self, pid):
        """Tasks in a project that wait on the user: a question/stop, or finished work (with the time of
        Claude's last message, so the board can tell whether it was opened since). Done cards don't count."""
        waiting, finished = [], {}
        with self.lock:
            for (p, tid), v in self.cache.items():
                if p != pid or v.get("closed"):
                    continue
                if v["phase"] == "needs_you":
                    waiting.append(tid)
                elif v["phase"] == "finished":
                    finished[tid] = next((e.get("at") for e in reversed(v["log"]) if e.get("from") != "you"), None)
        return {"waiting": waiting, "finished": finished}


WATCH = Watcher()

def script_cmd(p, task_id, script):
    # Agents and hooks run it in their shell (Git Bash on Windows, where `python3` is often missing or a Store stub).
    q = lambda x: shlex.quote(shell_path(x))
    return f"{q(sys.executable) if WINDOWS else 'python3'} {q(os.path.join(HERE, script))} --board {q(p.board)} {task_id}"


def report_cmd(p, task_id):
    return script_cmd(p, task_id, "report.py")


def reply(p, task_id, text, images=()):
    """Send a card message. While Claude works it is logged as a note for the session's inbox hook; else it
    resumes the session. Returns True when it was queued as a note."""
    info = WATCH.snapshot(p.id).get(task_id)
    if not info:
        raise LookupError("no Claude session for this task")
    if info["phase"] == "working":
        reports.append(p.board, task_id, "note", text, "you", images=list(images))
        return True
    if not info.get("sessionId"):
        raise LookupError("no Claude session for this task")
    # The user reads only the card, so the answer has to go through report.py, not the agent's chat.
    message = text + f"\n\n(Reply from the task board. Post your answer on the card: `{report_cmd(p, task_id)} done|progress|question \"...\"`. "
    message += reports.LANGUAGE_RULE + ")"
    if images:
        message += "\n\nImages attached to this reply (open each with the Read tool):\n" + "\n".join(
            image_lines(images, reports.log_images(p.board)))
    aid = resume(p, info, message)
    reports.append(p.board, task_id, "reply", text, "you", session=aid, images=list(images))
    WATCH.expect(p, task_id, aid)
    return False


def resume(p, info, message):
    """Wake an idle session with `message` as its next turn; returns the session's short id."""
    # A live idle session must be stopped first, and fully (its pid gone): --resume on a
    # running one starts a copy instead of waking it.
    run_claude(["stop", info["id"]], cwd=p.path, timeout=30)
    for _ in range(40):
        s = next((x for x in list_sessions(p.path) if x["id"] == info["id"]), None)
        if not s or "pid" not in s:
            break
        time.sleep(0.25)
    time.sleep(1)  # resuming the instant the pid goes has still produced a copy; the board follows either way
    # No flags here: a bg session keeps its saved options (model and inbox hook included), and any flag on --resume
    # forks a copy.
    r = run_claude(["--bg", "--resume", info["sessionId"], message], cwd=p.path, timeout=60)
    out = ANSI.sub("", r.stdout + r.stderr)
    m = re.search(r"backgrounded\s*·\s*([0-9a-f]+)", out)
    if r.returncode != 0 or not m:
        raise RuntimeError(out.strip() or f"claude exited {r.returncode}")
    return m.group(1)


def deliver_notes(p, task_id, info, waiting=False):
    """Fallback for notes the inbox hook never handed over (the turn ended first, or the session predates the hook):
    resume the idle session with them. `waiting`: its turn had ended while a background shell still ran."""
    notes, since = reports.take_notes(p.board, task_id)
    if not notes:
        return
    try:
        message = reports.notes_message(p.board, notes)
        if waiting:
            message += ("\n\n(Your turn had ended while you waited on a background command. Your session was "
                        "stopped to hand you this message, which stopped those commands too: rerun any you still "
                        "need, and give long runs a time limit so a hang cannot stall you.)")
        message += f"\n\n(Post your answer on the card: `{report_cmd(p, task_id)} done|progress|question \"...\"`.)"
        aid = resume(p, info, message)
    except Exception as e:  # noqa: BLE001 — put the notes back for the next poll
        reports.untake_notes(p.board, task_id, notes, since)
        print("deliver notes:", e)
        return
    n = len(notes)
    reports.append(p.board, task_id, "resume", f"Handed Claude {n} message{'s' if n > 1 else ''} it had not read yet",
                   "you", session=aid)
    WATCH.expect(p, task_id, aid)


# ---------------------------------------------------------------- http
FILE_ROUTE = re.compile(r"/files/([0-9a-f]{10})/(images|agent_reports/images)/([^/]+)")


# Fixed types: mimetypes reads the Windows registry, which can map .js or .css to anything.
TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
         ".json": "application/json", ".md": "text/plain; charset=utf-8", ".png": "image/png", ".jpg": "image/jpeg",
         ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml", ".ico": "image/x-icon"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=HERE, **kwargs)

    def guess_type(self, path):
        return TYPES.get(os.path.splitext(path)[1].lower()) or super().guess_type(path)

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj).encode())

    def _same_origin(self):
        # Any web page can POST to localhost; only the board itself may launch agents or write files.
        origin = self.headers.get("Origin")
        host = self.headers.get("Host", "")
        return (self.headers.get("Content-Type", "").startswith("application/json")
                and origin in (f"http://{host}", None))

    def _route(self):
        u = urlsplit(self.path)
        return u.path, parse_qs(u.query).get("p", [""])[0]

    def _errors(self, fn):
        try:
            fn()
        except LookupError as e:
            self._json(404, {"error": str(e)})
        except ValueError as e:
            self._json(400, {"error": str(e)})
        except PermissionError as e:
            self._json(409, {"error": str(e)})
        except UntrustedError as e:
            self._json(409, {"error": f"Claude does not trust {e} yet", "untrusted": True})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": str(e)})

    def do_GET(self):
        path, pid = self._route()
        m = FILE_ROUTE.fullmatch(path)
        if m:
            def serve():
                p = project(m.group(1))
                folder = reports.task_images(p.board) if m.group(2) == "images" else reports.log_images(p.board)
                f = reports.image_path(folder, m.group(3))
                with open(f, "rb") as fh:
                    ext = f.rsplit(".", 1)[1]
                    self._send(200, fh.read(), "image/" + ("jpeg" if ext == "jpg" else ext))
            self._errors(serve)
        elif path == "/api/projects":
            self._json(200, [{**p.info(), "attention": WATCH.attention(p.id)} for p in projects()])
        elif path == "/api/tasks":
            def tasks():
                p = project(pid)
                if os.path.exists(p.data):
                    with open(p.data, "rb") as f:
                        self._send(200, f.read())
                else:
                    self._send(200, b"null")
            self._errors(tasks)
        elif path == "/api/default-model":
            self._errors(lambda: self._json(200, default_model(project(pid))))
        elif path == "/api/agents":
            self._errors(lambda: self._json(200, WATCH.snapshot(project(pid).id)))
        elif path == "/api/git":
            self._errors(lambda: self._json(200, git_info(project(pid))))
        elif path == "/api/observations":
            self._errors(lambda: self._json(200, observations.list_observations(project(pid).path)))
        elif path == "/api/prompt":
            tid = parse_qs(urlsplit(self.path).query).get("t", [""])[0]
            self._errors(lambda: self._json(200, task_prompt(project(pid), tid)))
        else:
            super().do_GET()

    def do_POST(self):
        path, _ = self._route()
        routes = ("/api/agent", "/api/agent/reply", "/api/agent/review", "/api/image", "/api/projects", "/api/projects/pick",
                  "/api/projects/forget", "/api/projects/trust")
        if path not in routes:
            self._send(404)
            return
        if not self._same_origin():
            self._json(403, {"error": "cross-origin request refused"})
            return

        def handle():
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if path == "/api/projects":
                self._json(200, open_project(body["path"]).info())
            elif path == "/api/projects/pick":
                folder = pick_folder()
                self._json(200, open_project(folder).info() if folder else {"cancelled": True})
            elif path == "/api/projects/forget":
                forget_project(body["p"])
                self._json(200, {"ok": True})
            elif path == "/api/projects/trust":
                trust_folder(project(body["p"]))
                self._json(200, {"ok": True})
            elif path == "/api/agent":
                aid = launch(project(body["p"]), body["taskId"], body.get("model") or "")
                self._json(200, {"id": aid})
            elif path == "/api/agent/review":
                self._json(200, {"id": review(project(body["p"]), body["taskId"], body.get("level") or "medium")})
            elif path == "/api/image":
                p = project(body["p"])
                folder = reports.task_images(p.board) if body.get("kind") == "task" else reports.log_images(p.board)
                self._json(200, {"name": reports.store_image(base64.b64decode(body["data"]), folder)})
            else:
                p = project(body["p"])
                images = [n for n in body.get("images", [])
                          if os.path.exists(reports.image_path(reports.log_images(p.board), n))]
                queued = reply(p, body["taskId"],
                               body["text"].strip() or ("See the attached images." if images else "Go ahead."), images)
                self._json(200, {"ok": True, "queued": queued})
        self._errors(handle)

    def do_PUT(self):
        path, pid = self._route()
        if path != "/api/tasks":
            self._send(404)
            return
        if not self._same_origin():
            self._json(403, {"error": "cross-origin request refused"})
            return
        raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))

        def write():
            p = project(pid)
            try:
                data = json.loads(raw)
                ok = isinstance(data.get("columns"), list) and isinstance(data.get("tasks"), list)
            except (ValueError, AttributeError):
                ok = False
            if not ok:
                raise ValueError("need columns and tasks lists")
            reports.ensure_board(p.board)
            # Write-then-rename so a crash mid-write never leaves a truncated tasks.json.
            fd, tmp = tempfile.mkstemp(dir=p.board, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
                f.write("\n")
            replace_file(tmp, p.data)
            self._send(204)
        self._errors(write)

    def log_message(self, fmt, *args):
        if "/api/" not in self.path and "/files/" not in self.path:
            super().log_message(fmt, *args)


def main():
    global PERMISSION_MODE
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--project", help="open this folder's board (default with none remembered: the current git repo)")
    ap.add_argument("--permission-mode", default=PERMISSION_MODE,
                    help="permission mode for agents started with Send to Claude (default: auto)")
    args = ap.parse_args()
    for stream in (sys.stdout, sys.stderr):  # a redirected Windows console would crash on non-ANSI names
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    PERMISSION_MODE = args.permission_mode
    if args.project:
        open_project(args.project)
    elif not projects():
        open_project(reports.project_root())
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    threading.Thread(target=WATCH.run, daemon=True).start()
    print(f"Task board: http://127.0.0.1:{args.port}  (projects: {', '.join(p.name for p in projects())})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
