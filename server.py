#!/usr/bin/env python3
"""Task board: a local kanban with subtasks, for any project folder.

Run from anywhere:  python3 server.py [--port 8765] [--project PATH]
Then open http://127.0.0.1:8765 in a browser.

The header's project menu switches boards: "Open folder…" picks any folder
(a native dialog, or a pasted path) and remembers it in
~/.config/task_board/projects.json. Each project keeps its own board in
<project>/.task_board/ (see reports.py; a repo that already has
tools/task_board/tasks.json keeps using that). The board is not committed: a new one ignores
itself (.gitignore `*`), and a send lists the folder in the repo's info/exclude.
With no projects remembered yet, the git repo containing the current directory
(or --project) is opened. Opening index.html directly (file://) also works, but
then tasks live only in that browser's localStorage.
GET /api/tasks?p=<pid> answers the board (or null) and PUT saves the whole board. Both, and GET /api/agents, carry
X-Board-Rev (sha1 of tasks.json's bytes, "0" when missing). A PUT with If-Match that no longer equals it is refused
with 409 `changed` and the current `rev`, so a stale window cannot overwrite a newer board; no If-Match always writes.

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
it, hand the independent parts to subagents on the chosen model, and name in each subagent's prompt the skills it
must invoke first (the lead picks them; a subagent sees the skills list but picks none alone). A card reply that
resumes such a session repeats the rule (delegate_reminder), since the brief is many turns back by then. A task ticked "Answer only"
(task field `answerOnly`) gets a brief to investigate and report back on the card without changing
files or committing.

A task ticked "Use a worktree" (task field `worktree`, on by default: a missing key counts as on; never for "Answer
only") gets its own git worktree at launch: launch makes
<repo>/.claude/worktrees/<title-slug>-<task id> on a branch of the same name (make_worktree; the folder is kept out of
`git status` through info/exclude), stores {path, dir, branch, base} as task.agent.worktree, and the prompt tells the
session, which still starts in the project folder, to switch into it and do all work and commits there. A folder with no
repo or no commit answers 409 `norepo`; POST /api/git/init makes one with an "Initial commit". While the worktree
exists, the task's commits are looked up on its branch, and a card in Review offers "Run app from worktree" and "Merge
worktree to main" (POST /api/agent/worktree): canned replies that have the session run the app from the worktree, or
merge the branch into the base branch, then remove the worktree and branch (anything going wrong: abort, ask on the card).
"Delete worktree" (any column; POST /api/agent/worktree/delete, delete_worktree) removes the worktree and branch in the
server, no session; it answers 409 `unsaved` {changes, commits} when work would be lost, and the page resends with
`force` once the user confirms. A reply after the worktree was deleted or merged makes a fresh one (renew_worktree).

The agent posts progress / question / done onto its card with report.py. A
watcher thread polls `claude agents --json --all` every few seconds and, for
every remembered project, turns session state + the latest report into a phase:
working, needs_you (a question, a permission prompt, or it stopped without
reporting) or finished. Entering needs_you or finished raises a macOS
notification (none on other systems). A reply typed on the card stops the idle session and resumes it
with the reply as its next message (`claude --bg --resume` on a live session
would start a copy instead). If the session then stops without calling report.py, the
watcher copies its chat answer from the transcript onto the card (status "answer").

A message typed on the card while Claude works is logged as a "note" instead. The watcher types it into the live
session through `claude attach` (attach.py; POSIX only), where Claude Code queues it like anything typed at its
terminal, then presses its "send now" key so the note goes in at once (a running command moves to the background,
a reply being written stops there) instead of after a long command. It never types over a permission prompt. As a backup every session the board starts gets inbox.py as a PostToolUse + Stop hook
(--settings; a flagless --resume keeps it), which hands the note over after the current tool call, or blocks the
stop if the turn is ending. Notes left over (the turn ended first, or the session predates the hook) are delivered
by the watcher resuming the idle session.

"Review code" (a finished card in Review) starts a fresh `claude --bg` session on the task's model that picks
the task's commits from `git log --since=<launch>` (other sessions share the branch, so it matches them to the
card's report), runs the commit-review skill (skills/commit-review/SKILL.md; one cheap pass, not /code-review) on them at the
chosen budget (low / medium / high), and posts the findings on the card. The card then follows the reviewer, so a reply asking for fixes goes to it.

A card moved into a done column is closed by the watcher once its session is idle (archive): a one-shot `claude -p` (RECAP_MODEL) writes a recap
(goal, decisions and why, how, results) from the card log and the sessions' transcripts to
agent_reports/<task>.recap.md and posts it on the card, then `claude rm` removes every session of the task (that
frees its job folder and scratch files; the transcript stays). A reply on such a card starts a fresh session with
the task prompt, the recap and the reply (reopen); when it stops in the done column it is closed the same way.
GET /api/changelog is the board app's own changelog, whats-new.md in plain words, plus the entries the Update button would bring.

The server does not reload its own code: after server.py or reports.py change, restart it (Ctrl-C, then
onemoretask). Until then new /api routes answer a bare 404, which the page reports as "runs older code".
GET /api/update compares the app's own git clone with GitHub (fetch, cached 10 min, ?force=1 re-checks); POST
/api/update runs `git pull --ff-only` there and restarts the server with the same arguments.

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

Codex backend (codex.py): picking an OpenAI model (codex.is_model: gpt-…, o1/o3/o4, codex…) sends the task to OpenAI's
Codex CLI instead: `codex exec --json` runs as a subprocess the board owns, in the project folder, and its event log
lives in <board>/agent_reports/codex/. Its sessions (cli "codex") come from codex.sessions(board) in the same shape as
`claude agents --json`, so the watcher, phases, replies, review and close treat both alike. What differs: the prompt
gets a note that it runs without Claude Code's Skill / Agent tools (codex_note); there is no attach and no permission
prompt; a card note waits until the turn ends and is then delivered by resuming it (codex.resume); tokens come
from its event log; closing removes it with codex.remove. The recap is still written by `claude -p`. "Review code"
takes a model (GET /api/codex-models lists the OpenAI ones), so a Codex reviewer can check a Claude task and back.
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

import attach
import codex
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
SHA_WORD = re.compile(r"\b[0-9a-f]{7,40}\b")
PERMISSION_MODE = "auto"
IDLE_GRACE = 20  # seconds a busy session's turn must have been over before a card note resumes it
TYPE_RETRY = 15  # seconds before typing a note into a session is tried again (a prompt was on screen)
STALL_RETRY = 60  # seconds after auto mode's safety check stopped a session before the board resumes it
STALL_TRIES = 3   # such resumes in a row (no report from Claude in between) before it waits for the user
ALERT_WAIT = 75  # seconds an alert waits for the board to show the change (a hidden tab polls once a minute)
PAGE_GONE = 75  # seconds without a board page reading a project's cards: nothing will move them, alert at once


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
BOARD_LOCKS = {}
BOARD_LOCKS_LOCK = threading.Lock()


def board_lock(p):
    with BOARD_LOCKS_LOCK:
        return BOARD_LOCKS.setdefault(p.id, threading.Lock())


def board_rev(p, raw=None):
    """sha1 of tasks.json's bytes (or of `raw`, bytes already read from it), "0" when there is no file."""
    if raw is None:
        try:
            with open(p.data, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return "0"
    return hashlib.sha1(raw).hexdigest()


def load_tasks(p):
    with open(p.data, encoding="utf-8") as f:
        return json.load(f)


def image_lines(names, folder, indent=""):
    return [f"{indent}- {shell_path(reports.image_path(folder, n))}" for n in names]


def build_prompt(p, state, t, wt=None):
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
    answer_only = bool(t.get("answerOnly"))
    if answer_only:
        lines += ["", *answer_only_lines(report)]
    if t.get("delegate"):
        lines += ["", *delegate_lines(t.get("subagentModel") or "", report, answer_only)]
    wt = None if answer_only else wt
    if wt:
        lines += ["", *worktree_lines(wt, p.path)]
    lines.append("")
    if observations.installed():
        lines.append("Invoke the task-observer skill at the start and log its observations to "
                     f"{os.path.join(os.path.abspath(p.path), 'skill-observations')}. Skills it creates or "
                     "updates stay local: never commit " + ", ".join(d + "/" for d in OBSERVER_DIRS) +
                     " (the board keeps them out of git), and never commit a change it makes to a skill file.")
    work = ("Change no files and make no commits." if answer_only else
            f"Commit your work in the worktree, on branch {wt['branch']}; never push unless the task or the user tells "
            "you to." if wt else
            "Work in the project folder itself: do not create a git worktree or a branch for this task (the card has "
            "\"Use a worktree\" off), and if a harness rule puts you in one anyway, say so on the card. Commit your "
            "work; never push unless the task or the user tells you to.")
    lines += [f"Follow CLAUDE.md if the project has one. {work} Do not edit "
              f"{os.path.join(board_rel, 'tasks.json')} (the board owns it).", "", *card_lines(report, answer_only)]
    return "\n".join(lines)


def answer_only_lines(report):
    return ["Answer only (this task is marked \"do not act on it, just answer me\"):",
            "- Do NOT make the changes the task describes: no file edits, no commits, no installs, nothing that "
            "changes the project or the machine. Reading files, searching and running read-only commands to find "
            "the answer is fine, and so is the task-observer log if this prompt asks for one. This holds for any subagents too.",
            "- Work out what was asked and answer it: what you found, what you would change and where (file:line), "
            "the options and your recommendation.",
            f'- Put the full answer in the `{report} done "..."` message; the user reads it on the card and decides '
            "what to do next. If they then reply asking you to go ahead, that reply lifts this rule."]


def worktree_lines(wt, main):
    where = shell_path(wt["path"]) + (f" (the project's files are in {shell_path(wt['dir'])})"
                                      if wt["dir"] != wt["path"] else "")
    return ["Worktree (this task is marked \"use worktrees\": the user ticked it on the board):",
            f"- A git worktree is already made for this task: {where}, on branch `{wt['branch']}`, made from "
            f"`{wt['base']}`.",
            "- Switch into it first (the EnterWorktree tool with `path`, or `cd`) and do ALL edits, builds, tests and "
            f"commits there, never in the main checkout {shell_path(main)}.",
            "- The board files stay in the main checkout: keep using the report command and the absolute paths given in "
            "this prompt (board, images, skill-observations). Never use or edit the worktree's copy of the board folder.",
            "- Files git does not track (local config, .env, installed dependencies, build output) are not in the "
            "worktree: copy or reinstall what the work needs.",
            f"- Do not merge into `{wt['base']}` and do not remove the worktree yourself. The user tries it with \"Run "
            "app from worktree\" and merges it with \"Merge worktree to main\" on the card; those arrive as messages.",
            f"- In the done report, name the branch `{wt['branch']}` along with the commit shas."]


def card_lines(report, answer_only=False):
    done = "the full answer" if answer_only else "what you did, commit shas, what is left"
    return ["Keep the card current with the report script (it shows on the board and alerts the user):",
              "- Format every message for reading on the card: short paragraphs and `- ` bullet lists separated by "
              "blank lines (real newlines inside the quoted argument), never one run-on block. A progress line may be "
              "a single sentence. The card renders **bold**, `code` and `## ` headings.",
              f'- `{report} progress "<one line>"` at each milestone.',
              f'- `{report} question "<the question, with options>"` when you need a decision, then END YOUR TURN; '
              "the answer arrives as your next message. Do not guess on decisions the user should make.",
              f'- `{report} done "<{done}>"` as your last action when the task is finished.',
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


def delegate_lines(model, report, answer_only=False):
    if model and not MODEL.fullmatch(model):
        model = ""
    brief = ("- Give each subagent a self-contained prompt: the question, the files, the constraints (incl. "
             "CLAUDE.md rules) and what to return. Subagents only look into it and return findings: no file changes, "
             "no commits, no posts to the card. You write the answer." if answer_only else
             "- Give each subagent a self-contained prompt: the goal, the files, the constraints (incl. CLAUDE.md "
             "rules), how to verify, and what to return. Subagents do not commit or post to the card; you do.")
    use = (f'- Run every subagent on the "{model}" model: pass `model: "{model}"` on each Agent call.' if model
           else "- Subagents use this session's model (leave the Agent `model` unset).")
    return ["Subagents (this task is marked \"divide in subtasks / use subagents\"):",
            "- Do NOT use plan mode (no EnterPlanMode / ExitPlanMode): work out the split yourself and carry on "
            "without waiting for approval.",
            f"- Before {'starting' if answer_only else 'changing anything'}, analyse the brief carefully and work out "
            "how to split it: the parts, what each needs to know, and which depend on others.",
            "- Delegate each part that can stand alone to a subagent (the Agent tool). Launch independent parts in one "
            "message so they run in parallel; run dependent parts after what they need. Keep tiny or tightly coupled "
            "parts yourself.",
            brief,
            # Measured: an Agent-tool subagent sees the same skills list as the lead and can call Skill by name, but
            # it picks none on its own. The `skills:` frontmatter field exists only for custom agent files
            # (.claude/agents/*.md): it preloads a skill's text, so writing one per part would leave files behind.
            "- Skills: a subagent sees the same skills list as you but picks none by itself. For each part, choose "
            "the skills that cover its kind of work (the project's own skills, the domain skill for what it touches, "
            "and EVERY working-rules skill a hook, this prompt or CLAUDE.md tells you to load at the start: all of "
            "them, not one, e.g. both cmm-rules and ctx-rules when your setup asks for them) and open its prompt with "
            "them: `First invoke Skill('<name>') with the Skill tool` per skill, plus one line on why that skill "
            "matters for this part. Leave task-observer out: you log the observations.",
            use,
            f'- Post the plan (the parts, who does each) with `{report} progress "..."` before launching subagents, '
            "then check and integrate their results yourself before reporting done.",
            "- When a subagent writes a test, ask that it makes a fresh temp folder on every run (mkdtemp), so you can "
            "rerun it; rerun it once yourself before you trust its pass count.",
            "- This holds for the whole task, not just this first turn: when a reply from the card (an answer to your "
            "question, a follow-up change) asks for more work, split and delegate that work the same way."]


def delegate_reminder(t):
    """Appended to a card reply that resumes a delegating session: the brief is many turns back by then."""
    if not t or not t.get("delegate"):
        return ""
    model = t.get("subagentModel") or ""
    if model and not MODEL.fullmatch(model):
        model = ""
    look = "the looking-into this reply needs" if t.get("answerOnly") else "the work this reply asks for"
    on = f' on the "{model}" model (`model: "{model}"` on each Agent call)' if model else ""
    return (f"\n\nThis task is still marked \"divide in subtasks / use subagents\": hand {look} to subagents{on}, "
            "as the first brief says: a self-contained prompt per part that opens with `First invoke Skill('<name>') with "
            "the Skill tool` for each skill you chose for that part, working-rules skills included (a subagent picks "
            "none by itself). Split it and "
            "launch them before you edit anything yourself, not only for the check at the end. Keep only tiny or "
            "tightly coupled parts yourself.")


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


def codex_models():
    """GET /api/codex-models: the OpenAI models the pickers offer for Codex."""
    return {"models": codex.models()}


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
    """Start the task's session; {"id", "worktree", "cli"} (the worktree record, or None; "claude" or "codex").

    The task's own `worktree` field decides the worktree (missing = on); "Answer only" never gets one."""
    state = load_tasks(p)
    t = next((x for x in state["tasks"] if x["id"] == task_id), None)
    if not t:
        raise LookupError("no such task (save first?)")
    if t.get("agent"):
        raise PermissionError("already sent to Claude — the task is locked; reply on the card instead")
    name = "task: " + t["title"][:60]
    exclude_local(p)
    made = []
    use_wt = t.get("worktree", True) and not t.get("answerOnly")  # an answer changes no files
    wt = make_worktree(p, t, made) if use_wt else None
    try:
        prompt = build_prompt(p, state, t, wt)
        path = reports.prompt_path(p.board, task_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(prompt)
        aid = start_session(p, task_id, name, prompt, model)
    except Exception:
        # No session: take back what this send made. No --force and -d, so anything holding work stays.
        if "worktree" in made:
            git_try(p.path, "worktree", "remove", wt["path"])
        if "branch" in made and not os.path.isdir(wt["path"]):
            git_try(p.path, "branch", "-d", wt["branch"])
        raise
    where = f", worktree {wt['branch']}" if wt else ""
    reports.append(p.board, task_id, "launch", f"Sent to {who(model)} (session {aid}, model {model or 'default'}{where})",
                   "you", session=aid)
    WATCH.expect(p, task_id, aid)
    return {"id": aid, "worktree": wt, "cli": "codex" if codex.is_model(model) else "claude"}


OBSERVER_DIRS = ("skill-observations", "skill-updates", ".claude/skills")   # log, staged skills, project skills
WORKTREES = ".claude/worktrees"  # under the repo root; one folder per task sent with "Use a worktree"


SECRET_FILES = (".env", ".env.*", "!.env.example", "!.env.sample", "!.env.template")  # kept out of init_repo's commit


def exclude_dirs(p, dirs):
    """List folders in the repo's info/exclude (local only, unlike .gitignore), so they stay out of commits and
    `git status`. Files git already tracks are not affected. Best effort; a folder that is not a git repo is skipped."""
    exclude_patterns(p, [f"/{d}/" for d in dirs])


def exclude_patterns(p, patterns):
    try:
        r = subprocess.run(["git", "-C", p.path, "rev-parse", "--git-path", "info/exclude"],
                           capture_output=True, timeout=10, **TEXT)
        if r.returncode != 0:
            return
        path = os.path.join(p.path, r.stdout.strip())
        text = open(path, encoding="utf-8", errors="replace").read() if os.path.isfile(path) else ""
        missing = [x for x in patterns if x not in text.splitlines()]
        if missing:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(("" if not text or text.endswith("\n") else "\n") + "\n".join(missing) + "\n")
    except (OSError, subprocess.SubprocessError):
        pass


def exclude_local(p):
    """Every local-only exclude the board wants: the worktrees folder, the board folder (tasks are not committed; a
    repo that already tracks its board keeps it), plus the observer's when it is installed."""
    top = git_try(p.path, "rev-parse", "--show-toplevel")
    board = os.path.relpath(os.path.realpath(p.board), os.path.realpath(top)).replace(os.sep, "/") if top else ".."
    exclude_dirs(p, (WORKTREES, *(() if board.startswith("..") else (board,)),
                     *(OBSERVER_DIRS if observations.installed() else ())))


class NoRepoError(Exception):
    """"Use worktrees" in a folder with no git repo, or a repo with no commit yet."""


def git_try(cwd, *args, timeout=10):
    """stdout of `git -C cwd args`, or None when it fails."""
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           **TEXT)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def git_run(cwd, *args, timeout=60):
    """stdout of `git -C cwd args`; RuntimeError with git's message when it fails."""
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, **TEXT)
    except OSError:
        raise RuntimeError("git is not installed") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"git {args[0]} timed out") from None
    if r.returncode:
        raise RuntimeError(f"git {args[0]}: {_git_err(r)}")
    return r.stdout.strip()


def init_repo(p):
    """Make the project a git repo with a first commit, so its tasks can use worktrees. Returns git_info.
    The commit takes every file but the board (tasks), its local folders and .env files (secrets)."""
    info = git_info(p)
    if not info["repo"]:
        git_run(p.path, "init", "-q")
    if not info["commits"]:
        exclude_local(p)
        exclude_patterns(p, SECRET_FILES)
        git_run(p.path, "add", "-A")
        who = [] if git_try(p.path, "config", "user.email") and git_try(p.path, "config", "user.name") else [
            "-c", "user.name=Task board", "-c", "user.email=taskboard@localhost"]
        git_run(p.path, *who, "commit", "-q", "--allow-empty", "-m", "Initial commit")
    return git_info(p)


def worktree_name(t):
    slug = "-".join(re.findall(r"[a-z0-9]+", t["title"].lower()))[:30].strip("-")
    tid = re.sub(r"[^A-Za-z0-9_-]", "", str(t["id"]))
    return f"{slug}-{tid}" if slug else f"task-{tid}"


def make_worktree(p, t, made=None, name=None):
    """Create the task's worktree, or reuse it (a retry after a failed launch): <repo>/.claude/worktrees/<name> on
    branch <name>, from HEAD. Returns {path, dir (the project folder inside it), branch, base}. `made` (a list)
    gets "worktree" and "branch" for what this call created. `name` defaults to one made from the task's title."""
    made = [] if made is None else made
    if git_try(p.path, "rev-parse", "--is-inside-work-tree") != "true":
        raise NoRepoError(f"{p.name} has no git repo yet, so its tasks cannot use worktrees")
    if git_try(p.path, "rev-parse", "--verify", "--quiet", "HEAD^{commit}") is None:
        raise NoRepoError(f"The git repo of {p.name} has no commit yet, so its tasks cannot use worktrees")
    top = os.path.normpath(git_run(p.path, "rev-parse", "--show-toplevel"))
    prefix = git_run(p.path, "rev-parse", "--show-prefix")
    name = name or worktree_name(t)
    path = os.path.join(top, *WORKTREES.split("/"), name)
    exclude_local(p)
    base = git_try(p.path, "symbolic-ref", "--short", "-q", "HEAD") or git_run(p.path, "rev-parse", "--short", "HEAD")
    git_run(p.path, "worktree", "prune")  # a registered worktree whose folder was deleted blocks `worktree add`
    listed = {os.path.realpath(x[len("worktree "):]) for x in
              git_run(p.path, "worktree", "list", "--porcelain").splitlines() if x.startswith("worktree ")}
    if not (os.path.isdir(path) and os.path.realpath(path) in listed):
        if git_try(p.path, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}") is not None:
            git_run(p.path, "worktree", "add", "-q", path, name)
        else:
            git_run(p.path, "worktree", "add", "-q", "-b", name, path, "HEAD")
            made.append("branch")
        made.append("worktree")
    return {"path": path, "dir": os.path.normpath(os.path.join(path, prefix)) if prefix else path, "branch": name,
            "base": base}


def live_worktree(t):
    """The task's worktree record while its folder exists, else None."""
    wt = (t.get("agent") or {}).get("worktree")
    return wt if isinstance(wt, dict) and wt.get("path") and os.path.isdir(wt["path"]) else None


def renew_worktree(p, t):
    """A message to a "use worktrees" task whose worktree was deleted or merged away: make a fresh one, same folder
    and branch name (the card's record stays valid), from the main checkout's HEAD. Returns it, else None."""
    wt = (t.get("agent") or {}).get("worktree")
    if not isinstance(wt, dict) or not wt.get("branch") or t.get("answerOnly") or live_worktree(t):
        return None
    branch = wt["branch"]
    # A branch left behind with nothing main lacks would check out stale; drop it so the new one starts at HEAD.
    # One holding unmerged commits is reused, so that work stays. Prune first: a stale registration blocks -D.
    git_try(p.path, "worktree", "prune")
    if (git_try(p.path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is not None
            and git_try(p.path, "merge-base", "--is-ancestor", f"refs/heads/{branch}", "HEAD") is not None):
        git_try(p.path, "branch", "-D", branch)
    return make_worktree(p, t, name=branch)


def renewed_lines(wt, main):
    return ["The task's earlier worktree was removed (the user deleted it, or it was merged), so the board made a "
            "fresh one for this message. Anything you said earlier about the old worktree's folder or branch is out "
            "of date.", *worktree_lines(wt, main)]


def commit_ref(p, t):
    """Where the task's commits are: its worktree branch until that is merged and deleted, else HEAD."""
    wt = (t.get("agent") or {}).get("worktree")
    branch = isinstance(wt, dict) and wt.get("branch")
    if branch and git_try(p.path, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is not None:
        return f"refs/heads/{branch}"
    return "HEAD"


def inbox_settings(p, task_id):
    """--settings JSON that installs inbox.py, so card messages sent mid-work reach the running session."""
    hook = [{"hooks": [{"type": "command", "command": script_cmd(p, task_id, "inbox.py")}]}]
    # bgIsolation none: Claude Code's own background-session isolation would otherwise put the session in a
    # `worktree-<name>` worktree the board knows nothing about (no Run app / Merge buttons, Changes read the main
    # folder). The board makes the worktree itself when the card asks for one.
    return json.dumps({"hooks": {"PostToolUse": hook, "Stop": hook}, "worktree": {"bgIsolation": "none"}})


def is_codex(s):
    """A session record from codex.sessions; everything else is a Claude one."""
    return bool(s) and s.get("cli") == "codex"


def who(model):
    """The agent a model runs on, as the card names it."""
    return "Codex" if codex.is_model(model or "") else "Claude"


def codex_note(p):
    """Appended to a Codex session's prompt: the brief is written for Claude Code."""
    return ("Note: you run in OpenAI's Codex CLI, not Claude Code, so there is no Skill tool and no Agent tool. Where "
            "this brief names a skill to invoke, use it if it is in your own skills list; otherwise read its SKILL.md "
            f"(user skills are in {shell_path(os.path.expanduser('~/.claude/skills'))}/<name>/SKILL.md, project skills in "
            f"{shell_path(os.path.join(p.path, '.claude', 'skills'))}/<name>/SKILL.md) and follow it. Where it says to "
            "hand parts to subagents, do the parts yourself, in order, unless your runtime offers subagents. \"The Read "
            "tool\" means opening the file. You must still post on the card with the report.py command exactly as the "
            "brief says.")


def start_session(p, task_id, name, prompt, model):
    if codex.is_model(model):
        # Claude's folder trust check (UntrustedError) does not apply to Codex.
        model_args(model)  # refuses a bad model name
        return codex.start(p.board, task_id, name, prompt + "\n\n" + codex_note(p), model, p.path)
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


def task_commits(p, since, ref="HEAD"):
    """[(short sha, subject)] on `ref` committed since `since` (ISO time), newest first."""
    r = subprocess.run(["git", "-C", p.path, "log", f"--since={since}", "--format=%h %s", ref, "--"],
                       capture_output=True, timeout=10, **TEXT)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "git log failed")
    return [tuple(x.split(" ", 1)) if " " in x else (x, "") for x in r.stdout.splitlines() if x]


def launched_at(log, t):
    """When the task was sent: its last launch message, else the agent's start time."""
    return next((e["at"] for e in reversed(log) if e.get("status") == "launch"), t["agent"].get("started"))


def own_commits(p, log, since, ref="HEAD"):
    """Commits on `ref` since `since` that a Claude message on the card names by sha: the task's own work, as
    opposed to other sessions' commits on the shared branch. None when git fails."""
    words = {w for e in log if e.get("from") == "claude" for w in SHA_WORD.findall(e.get("message") or "")}
    if not words:
        return []
    try:
        commits = task_commits(p, since, ref)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
    return [c for c in commits if any(w.startswith(c[0]) or c[0].startswith(w) for w in words)]


def review(p, task_id, level, model=None):
    """Start a fresh session that code-reviews the task's commits and posts the findings on its card. The card
    then follows the reviewer, so a reply ("fix 1 and 3") goes to the session that holds the findings.
    `model` picks the reviewer's model (a Claude alias or an OpenAI one for Codex); empty = the task's own model."""
    if level not in REVIEW_LEVELS:
        raise ValueError(f"bad review level: {level!r}")
    if model and not MODEL.fullmatch(model):
        raise ValueError(f"bad model name: {model!r}")
    state = load_tasks(p)
    t = next((x for x in state["tasks"] if x["id"] == task_id), None)
    if not t or not t.get("agent"):
        raise LookupError("this task was never sent to Claude")
    use = model or t["agent"].get("model") or ""
    info = WATCH.snapshot(p.id).get(task_id)
    if info and info["phase"] == "working":
        raise PermissionError("Claude is still working — wait until it stops")
    log = reports.read(p.board, task_id)
    since = launched_at(log, t)
    ref = commit_ref(p, t)
    commits = task_commits(p, since, ref)
    if not own_commits(p, log, since, ref):
        raise PermissionError("This task made no commits — nothing to review")
    branch = ref[len("refs/heads/"):] if ref != "HEAD" else None
    wt = branch and live_worktree(t)
    # The task's own report, not an earlier reviewer's findings: stop at the first review.
    first_review = next((i for i, e in enumerate(log) if e.get("status") == "review"), len(log))
    report = next((e["message"] for e in reversed(log[:first_review]) if e.get("from") == "claude"
                   and e.get("status") in ("done", "answer")), "(no final report)")
    board_rel = os.path.relpath(p.board, p.path)
    rep = report_cmd(p, task_id)
    skill = shell_path(os.path.join(HERE, 'skills', 'commit-review', 'SKILL.md'))
    step1 = (f'1. Use the `commit-review` skill with args "{level} {" ".join(sha for sha, _ in commits)}" if it is in '
             f"your skills list; otherwise read {skill} and follow it exactly with those args (where it says Skill "
             "tool or Read tool, do the equivalent yourself)." if codex.is_model(use) else
             f'1. Run the commit-review skill (the Skill tool, skill "commit-review", args "{level}" plus the candidate '
             f"shas above). If that skill is not installed, Read {skill} and follow it.")
    prompt = "\n".join([
        f"Code review for a task on the project task board ({board_rel}).", "",
        f"Task: {t['title']}", f"Task id: {task_id}", *(["", "Task notes:", t["notes"]] if t.get("notes") else []),
        "", f"Another {who(t['agent'].get('model'))} session did this task. It was sent at {since}. Its last report on the card:", report, "",
        (f"Commits on the task's branch `{branch}` since then, newest first:" if branch else
         "Commits on HEAD since then, newest first. Other sessions commit to the same branch, so some may belong "
         "to other tasks:"), *[f"- {sha} {subj}" for sha, subj in commits], "",
        *([f"The work is on branch `{branch}` in the git worktree {shell_path(wt['path'])}: review it there (cd into "
           "it first). Later fixes go there too, committed on that branch.", ""] if wt else []),
        "Steps:",
        step1 + " It keeps only the commits that match the report above. Keep to its turn budget. Do not use "
        "/code-review, subagents or workflows. Review only in this turn: no file changes, no commits.",
        f'2. Post its report with `{rep} done "..."`, naming the commits you reviewed. If nothing survived, say so. '
        "End by asking which findings to fix.",
        "If a later reply asks for fixes: make them, verify them, commit (never push unless the user tells you to), and report done.", "",
        f"Follow CLAUDE.md if the project has one. Do not edit {os.path.join(board_rel, 'tasks.json')} "
        "(the board owns it).", "", *card_lines(rep)])
    aid = start_session(p, task_id, "review: " + t["title"][:58], prompt, use)
    reports.append(p.board, task_id, "review", f"Code review started ({level}, {model or 'task model'}, session {aid})",
                   "you", session=aid)
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
    return {"prompt": build_prompt(p, state, t, live_worktree(t)), "saved": False}


def git_info(p):
    """{branch, tag, dirty, repo (inside a git work tree), commits (HEAD resolves to a commit)}."""
    git = lambda *args: git_try(p.path, *args, timeout=5)
    repo = git("rev-parse", "--is-inside-work-tree") == "true"
    commits = repo and git("rev-parse", "--verify", "--quiet", "HEAD^{commit}") is not None
    branch = git("rev-parse", "--abbrev-ref", "HEAD") if commits else None
    if branch is None:
        return {"branch": None, "tag": None, "dirty": False, "repo": repo, "commits": commits}
    if branch == "HEAD":
        branch = git("rev-parse", "--short", "HEAD")
    return {"branch": branch, "tag": git("describe", "--tags", "--abbrev=0") or None,
            "dirty": bool(git("status", "--porcelain")), "repo": repo, "commits": commits}


class GitUnavailable(Exception):
    """Git cannot answer for a task right now (no git, not a repo, timeout, failed command)."""


def _git_raw(cwd, *args, ok=(0,), timeout=10):
    """Unstripped stdout of `git -C cwd args` (porcelain lines start with a space); GitUnavailable on failure.
    `ok` lists the accepted exit codes (`diff --no-index` exits 1 when the files differ)."""
    try:
        r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                           env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}, **TEXT)
    except OSError:
        raise GitUnavailable("git is not installed") from None
    except subprocess.SubprocessError:
        raise GitUnavailable(f"git {args[0]} timed out") from None
    if r.returncode not in ok:
        raise GitUnavailable(f"git {args[0]}: {_git_err(r)}")
    return r.stdout


def _branch_exists(cwd, name):
    return bool(name) and git_try(cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{name}", timeout=5) is not None


def _find_task(p, task_id):
    t = next((x for x in load_tasks(p)["tasks"] if x["id"] == task_id), None)
    if not t:
        raise LookupError("no such task")
    return t


def _task_git_place(p, t):
    """Where a task's git state lives: {cwd (folder for uncommitted changes, a work tree top, or None), ref (the
    task's commits), target_ref, target, branch, wt, live, branch_alive, shared}. GitUnavailable when git can't say."""
    if git_try(p.path, "rev-parse", "--is-inside-work-tree", timeout=5) != "true":
        raise GitUnavailable("this project folder is not a git repo")
    if git_try(p.path, "rev-parse", "--verify", "--quiet", "HEAD^{commit}", timeout=5) is None:
        raise GitUnavailable("the git repo has no commits yet")
    current = git_try(p.path, "symbolic-ref", "--short", "-q", "HEAD", timeout=5) or git_try(
        p.path, "rev-parse", "--short", "HEAD", timeout=5)
    wt = (t.get("agent") or {}).get("worktree")
    if isinstance(wt, dict) and wt.get("branch"):
        branch, base = wt["branch"], wt.get("base")
        live = bool(live_worktree(t))
        if _branch_exists(p.path, base):
            target, target_ref = base, f"refs/heads/{base}"
        elif base and git_try(p.path, "rev-parse", "--verify", "--quiet", f"{base}^{{commit}}", timeout=5):
            target, target_ref = base, base  # the worktree was made from a detached HEAD
        else:
            target, target_ref = current, "HEAD"  # the base branch is gone: compare with the main checkout
        alive = _branch_exists(p.path, branch)
        return {"cwd": wt["path"] if live else None, "ref": f"refs/heads/{branch}" if alive else "HEAD",
                "target_ref": target_ref, "target": target, "branch": branch, "wt": wt, "live": live,
                "branch_alive": alive, "shared": False}
    top = git_try(p.path, "rev-parse", "--show-toplevel", timeout=5)
    if not top:
        raise GitUnavailable("git could not find the repo's top folder")
    return {"cwd": os.path.normpath(top), "ref": "HEAD", "target_ref": "HEAD", "target": current, "branch": current,
            "wt": None, "live": False, "branch_alive": False, "shared": True}


TASK_GIT_MAX_FILES = 200


def _changed_files(cwd):
    """(uncommitted changes in the work tree at `cwd`, truncated). Paths are relative to that work tree's top."""
    raw = _git_raw(cwd, "status", "--porcelain=v1", "-z", "--no-renames")
    entries = [e for e in raw.split("\0") if e]
    truncated = len(entries) > TASK_GIT_MAX_FILES
    counts = {}
    for line in _git_raw(cwd, "diff", "--numstat", "--no-renames", "HEAD", "--").splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3:
            counts[parts[2]] = (None if parts[0] == "-" else int(parts[0]), None if parts[1] == "-" else int(parts[1]))
    out = []
    for e in entries[:TASK_GIT_MAX_FILES]:
        xy, path = e[:2], e[3:]
        status = "??" if xy == "??" else (xy.replace(" ", "")[:1] or "M")
        added, deleted = counts.get(path, (None, None))
        out.append({"status": status, "path": path, "added": added, "deleted": deleted})
    return out, truncated


def task_git(p, task_id):
    """The real git state of a task's work, so the card can tell whether anything is left to merge. Git trouble
    gives available=False with an error, never an exception; LookupError only for an unknown task."""
    t = _find_task(p, task_id)
    out = {"available": False, "error": None, "repo": False, "shared": False, "target": None, "branch": None,
           "worktree": None, "changed": [], "ahead": [], "aheadKind": "own", "behind": 0, "merged": False,
           "answerOnly": bool(t.get("answerOnly")), "sent": bool(t.get("agent"))}
    if not t.get("agent"):
        out["error"] = "this task was never sent to Claude"
        return out
    try:
        place = _task_git_place(p, t)
        out["repo"] = True
        out.update(shared=place["shared"], target=place["target"], branch=place["branch"])
        if place["wt"] is not None:
            wt = place["wt"]
            out["worktree"] = {"path": wt.get("path"), "branch": wt.get("branch"), "base": wt.get("base"),
                               "exists": place["live"]}
            out["aheadKind"] = "branch"
        if place["cwd"]:
            out["changed"], truncated = _changed_files(place["cwd"])
            if truncated:
                out["truncated"] = True
        if place["shared"]:
            log = reports.read(p.board, task_id)
            own = own_commits(p, log, launched_at(log, t), "HEAD")
            if own is None:
                raise GitUnavailable("git log failed")
            out["ahead"] = [{"sha": sha, "subject": subj} for sha, subj in own]
        elif place["branch_alive"]:
            rng = f"{place['target_ref']}..{place['ref']}"
            out["ahead"] = [{"sha": x.split(" ", 1)[0], "subject": x.split(" ", 1)[1] if " " in x else ""}
                            for x in _git_raw(p.path, "log", "--format=%h %s", rng, "--").splitlines() if x]
            out["behind"] = int(_git_raw(p.path, "rev-list", "--count", f"{place['ref']}..{place['target_ref']}",
                                         "--").strip() or 0)
            out["merged"] = git_try(p.path, "merge-base", "--is-ancestor", place["ref"], place["target_ref"],
                                    timeout=5) is not None
        else:
            out["merged"] = True  # the branch was merged and deleted
        out["available"] = True
    except (GitUnavailable, OSError, ValueError) as e:
        out.update(available=False, error=str(e) or "git failed", changed=[], ahead=[], behind=0, merged=False)
        out.pop("truncated", None)
    return out


TASK_DIFF_MAX = 200_000


def _cap(text):
    data = text.encode("utf-8")
    if len(data) <= TASK_DIFF_MAX:
        return text, False
    return data[:TASK_DIFF_MAX].decode("utf-8", "ignore"), True


def task_diff(p, task_id, path=None, sha=None):
    """{diff, truncated, binary}: one uncommitted file's diff in the task's work tree (`path`), or one of the task's
    commits (`sha`, only a commit reachable from the task's branch / HEAD). Git trouble gives an "error" field."""
    t = _find_task(p, task_id)
    out = {"diff": "", "truncated": False, "binary": False}
    if bool(path) == bool(sha):
        raise ValueError("pass exactly one of path or sha")
    if path is not None:
        norm = path.replace("\\", "/")
        if norm.startswith("/") or re.match(r"^[A-Za-z]:", norm) or ".." in norm.split("/") or norm.startswith(":"):
            raise ValueError("bad path")
    elif not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        raise ValueError("bad commit id")
    if not t.get("agent"):
        out["error"] = "this task was never sent to Claude"
        return out
    try:
        place = _task_git_place(p, t)
        if path is not None:
            cwd = place["cwd"]
            if not cwd:
                out["error"] = "the task's worktree folder is gone"
                return out
            text = _git_raw(cwd, "diff", "--no-color", "--no-renames", "HEAD", "--", norm)
            if not text and _git_raw(cwd, "ls-files", "--others", "--exclude-standard", "--", norm).strip() \
                    and os.path.isfile(os.path.join(cwd, *norm.split("/"))):
                text = _git_raw(cwd, "diff", "--no-color", "--no-index", "--", os.devnull, norm, ok=(0, 1))
        else:
            full = git_try(p.path, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}", timeout=5)
            if not full or git_try(p.path, "merge-base", "--is-ancestor", full, place["ref"], timeout=5) is None:
                raise ValueError("that commit is not part of this task's work")
            text = _git_raw(p.path, "show", "--no-color", "--format=fuller", "--stat", "-p", full, "--")
        if re.search(r"^Binary files .* differ$", text, re.M) and not re.search(r"^@@ ", text, re.M):
            out["binary"] = True
            if path is not None:
                text = ""
        out["diff"], out["truncated"] = _cap(text)
    except (GitUnavailable, OSError) as e:
        out["error"] = str(e) or "git failed"
    return out


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
    # Chrome screenshot) keeps the session "busy" forever. A permission prompt after the report still needs you.
    ask = permission_ask(session)
    if session is not None and not ask and last.get("from") == "claude" and last.get("status") in ("done", "answer"):
        return "finished", last["message"]
    # Just after a launch or reply the session is still starting (absent, or stopped from the reply's stop).
    if busy or (last.get("from") == "you" and time.time() - launched_at < 45):
        return "working", ""
    if last.get("status") == "recap":
        return "finished", "Moved to done: sessions closed, recap saved. A reply starts a new session from the recap."
    if session is None:
        return "gone", "Session was removed"
    # A live permission prompt is what blocks it now, even when an older, answered question ends the log.
    if ask:
        return "needs_you", ask
    # An explicit report beats the CLI's own guess (it marks some finished sessions "blocked").
    if last.get("status") == "question":
        return "needs_you", last["message"]
    if last.get("status") in ("done", "answer"):
        return "finished", last["message"]
    if st == "blocked":
        # The CLI marks a turn "blocked" when its own end-of-turn summary sounds like it waits on you
        # ("awaiting reload test"), so a finished turn that ended on a progress note lands here too.
        detail = session.get("detail") or ("" if is_codex(session) else job_detail(session.get("id")))
        if last.get("from") == "claude" and last.get("status") == "progress":
            return "needs_you", "Stopped after a progress note without reporting done — reply to check on it" + (f" (CLI: {detail})" if detail else "")
        if is_codex(session):  # no attach and no permission prompts in Codex
            return "needs_you", f"Stopped in the session{': ' + detail if detail else ''} — reply to check on it"
        if detail:
            return "needs_you", f"Stopped in the session: {detail} — reply, or attach if it asks for a permission"
        return "needs_you", "Waiting on a permission prompt or a question in the session — attach to answer"
    if is_codex(session):
        return "needs_you", "Stopped without reporting — reply to check on it"
    return "needs_you", "Stopped without reporting — attach or reply to check on it"


def job_detail(job_id, field="detail"):
    """The CLI's one-line summary of where a background session stopped ('' if unknown)."""
    try:
        path = os.path.join(os.path.expanduser("~/.claude/jobs"), job_id or "", "state.json")
        with open(path, encoding="utf-8") as f:
            return (json.load(f).get(field) or "").strip()
    except (OSError, ValueError, AttributeError):
        return ""


def permission_ask(session):
    """What a session sitting on a permission prompt asks to do ('' if it is not on one). `claude agents` gives
    waitingFor "permission prompt"; the job's state.json has the request ("approve Bash: rm -f …")."""
    if not session or "permission" not in (session.get("waitingFor") or ""):
        return ""
    need = re.sub(r"^approve\s+", "", job_detail(session.get("id"), "needs"), flags=re.I)
    return "Claude asks permission: " + (need or "a tool call (attach to see it)")


def list_sessions(cwd):
    r = run_claude(["agents", "--json", "--all"], cwd=cwd, timeout=30)
    # Interactive terminal sessions are listed too, without an id: the board only drives --bg ones.
    return [s for s in json.loads(r.stdout or "[]") if isinstance(s, dict) and s.get("id")]


def all_sessions(p):
    """Claude's sessions (a global list) plus the project's Codex ones, to look a session up by id."""
    return list_sessions(p.path) + codex.sessions(p.board)


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


AUTO_MODE_STALL = "Auto mode is unavailable"  # the CLI's note when auto mode's safety check gave no verdict
_stall_memo = {}  # transcript path -> ((mtime, size), stalled at)


def auto_mode_stall(session_id):
    """When the CLI ended the session's last turn because auto mode's safety check kept giving no verdict (epoch s),
    else None. The CLI then waits for a message, which nobody sends to a background session."""
    path = transcript_path(session_id)
    if not path:
        return None
    st = os.stat(path)
    memo = _stall_memo.get(path)
    if memo and memo[0] == (st.st_mtime, st.st_size):
        return memo[1]
    at = None
    for e in reversed(transcript_tail(path)):
        kind = e.get("type")
        if kind == "system" and str(e.get("content") or "").startswith(AUTO_MODE_STALL):
            at = _ts(e["timestamp"])
            break
        if kind in ("user", "assistant") or kind == "attachment" and (e.get("attachment") or {}).get("type") == "queued_command":
            break
    _stall_memo[path] = ((st.st_mtime, st.st_size), at)
    return at


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
    [{id, name, type, started, updated, finished, action, skills, tokens}], oldest first. finished = its last entry is an
    end_turn reply or a successful SubagentHandback result; a subagent cut off mid-run stays unfinished (the board shows it as stopped)."""
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
        # Newer subagents end on a SubagentHandback call and its result, never an end_turn reply.
        prev = (entries[-2].get("message") or {}).get("content") if len(entries) > 1 else None
        handback = {b.get("id") for b in (prev if isinstance(prev, list) else [])
                    if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "SubagentHandback"}
        handed = last.get("type") == "user" and isinstance(msg.get("content"), list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result" and b.get("tool_use_id") in handback
            and not b.get("is_error") for b in msg["content"])
        action = next((_action(b) for e in reversed(entries) for b in reversed((e.get("message") or {}).get("content") or [])
                       if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") != "SubagentHandback"), "")
        calls, failed = _skill_calls(f)
        used = []
        for tid, (skill, _) in sorted(calls.items(), key=lambda c: c[1][1]):
            if tid not in failed and skill not in used:
                used.append(skill)
        row = {"id": name[len("agent-"):-len(".jsonl")], "name": meta.get("description") or "Subagent",
               "type": meta.get("agentType") or "", "started": started,
               "updated": _ts(last["timestamp"]) if last.get("timestamp") else st.st_mtime,
               "finished": handed or last.get("type") == "assistant" and msg.get("stop_reason") == "end_turn",
               "action": action, "skills": used, "tokens": sum(map(sum, _usage(f).values()))}
        _sub_memo[f] = ((st.st_mtime, st.st_size), row)
        rows.append(row)
    return sorted(rows, key=lambda r: r["started"])


_skill_memo = {}  # transcript path -> [bytes read, {tool_use id: (skill, at)}, {failed tool_use ids}]


def _skill_calls(path):
    """Skill-tool calls in one transcript as {tool_use id: (skill, at)} and the ids whose call failed. Reads only
    the bytes added since the last call: transcripts only grow, and skills are often loaded at the very start."""
    size = os.path.getsize(path)
    memo = _skill_memo.get(path)
    if not memo or size < memo[0]:
        memo = _skill_memo[path] = [0, {}, set()]
    if size > memo[0]:
        with open(path, "rb") as f:
            f.seek(memo[0])
            chunk = f.read(size - memo[0])
        end = chunk.rfind(b"\n") + 1  # a half-written last line is read next time
        for line in chunk[:end].splitlines():
            if b'"Skill"' not in line and b'"is_error":true' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            content = isinstance(e, dict) and (e.get("message") or {}).get("content")
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("name") == "Skill" and (b.get("input") or {}).get("skill"):
                    memo[1][b.get("id")] = (str(b["input"]["skill"]), e.get("timestamp") or "")
                elif b.get("type") == "tool_result" and b.get("is_error"):
                    memo[2].add(b.get("tool_use_id"))
        memo[0] += end
    return memo[1], memo[2]


def skills(session_ids):
    """Skills Claude loaded with the Skill tool across the given sessions and their subagents:
    [{name, count, first}], in the order first used. A call that errored (e.g. an unknown skill) does not count.
    A call copied into a second transcript (a forked resume carries the history over) counts once, by tool_use id."""
    found, seen = {}, set()
    for sid in session_ids:
        path = transcript_path(sid)
        if not path:
            continue
        folder = os.path.join(path[:-len(".jsonl")], "subagents")
        subs = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".jsonl")] if os.path.isdir(folder) else []
        for f in [path] + subs:
            calls, failed = _skill_calls(f)
            for tid, (name, at) in calls.items():
                if tid in failed or tid in seen:
                    continue
                seen.add(tid)
                row = found.setdefault(name, {"name": name, "count": 0, "first": at})
                row["count"] += 1
                if at and (not row["first"] or at < row["first"]):
                    row["first"] = at
    return sorted(found.values(), key=lambda r: r["first"])


USAGE_KEYS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
_usage_memo = {}  # transcript path -> [bytes read, {message id: (input, cache write, cache read, output)}]


def _usage(path):
    """Token usage of each API reply in one transcript as {message id: (input, cache write, cache read, output)}.
    A reply split over several entries repeats its usage while its output count grows: the largest counts win.
    Reads only the bytes added since the last call."""
    size = os.path.getsize(path)
    memo = _usage_memo.get(path)
    if not memo or size < memo[0]:
        memo = _usage_memo[path] = [0, {}]
    if size > memo[0]:
        with open(path, "rb") as f:
            f.seek(memo[0])
            chunk = f.read(size - memo[0])
        end = chunk.rfind(b"\n") + 1
        for line in chunk[:end].splitlines():
            if b'"usage"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            m = isinstance(e, dict) and e.get("type") == "assistant" and e.get("message")
            u = isinstance(m, dict) and m.get("usage")
            if not isinstance(u, dict) or not m.get("id"):
                continue
            new = tuple(int(u.get(k) or 0) for k in USAGE_KEYS)
            old = memo[1].get(m["id"])
            memo[1][m["id"]] = tuple(map(max, old, new)) if old else new
        memo[0] += end
    return memo[1]


def task_transcripts(p, task_id, session_ids):
    """Main transcripts of a card: its listed sessions (a Codex one's event log), plus those a recap names (removed
    sessions keep theirs)."""
    paths = []
    for sid in session_ids:
        c = codex.session(p.board, sid)
        paths.append(c["log"] if c else transcript_path(sid))
    _, _, listing = reports.read_recap(p.board, task_id).partition(TRANSCRIPTS)
    paths += [os.path.normpath(x) for x in re.findall(r"^- (.+\.jsonl)$", listing, re.M)]
    return [x for x in dict.fromkeys(paths) if x and os.path.exists(x)]


def tokens(paths):
    """Tokens used by the given main transcripts and their subagents:
    {"main": [input, cache write, cache read, output], "subagents": [...], "count": subagent count}.
    A reply copied into a second transcript (a forked resume carries the history over) counts once, by message id."""
    out, seen = {"main": [0] * 4, "subagents": [0] * 4, "count": 0}, set()
    for path in paths:
        if codex.is_log(path):  # a Codex event log: no subagents
            out["main"] = [a + b for a, b in zip(out["main"], codex.usage(path))]
            continue
        folder = os.path.join(path[:-len(".jsonl")], "subagents")
        subs = [os.path.join(folder, n) for n in os.listdir(folder) if n.endswith(".jsonl")] if os.path.isdir(folder) else []
        out["count"] += len(subs)
        for f, side in [(path, "main")] + [(x, "subagents") for x in subs]:
            for mid, u in _usage(f).items():
                if mid not in seen:
                    seen.add(mid)
                    out[side] = [a + b for a, b in zip(out[side], u)]
    return out


class Watcher:
    """Polls session state for every task with an agent, in every remembered project; the board reads the cache."""

    def __init__(self):
        self.lock = threading.Lock()
        self.cache = {}       # (project id, task id) -> {id, sessionId, state, phase, reason, log}
        self.launched = {}    # agent id -> launch time, for sessions not listed yet
        self.primed = False   # first pass only records, so a restart doesn't re-alert old sessions
        self.loaded = False   # a first pass has ended, even with an error: the page stops polling fast
        self.delivering = set()  # (project id, task id) whose leftover notes are being handed over
        self.typed_fail = {}  # (project id, task id) -> time typing a note into its session last failed
        self.unstall_fail = {}  # (project id, task id) -> the auto-mode stall (epoch s) a resume failed on
        self.committed = {}   # (project id, task id) -> (log length, whether a card message names its own commit)
        # A card moved to done is marked by reports.close_path (a file, so a server restart keeps it) until it is closed.
        self.closing = set()   # (project id, task id) whose recap is being written
        self.close_failed = set()  # (project id, task id) whose recap failed: no retry until moved to done again
        self.alerts = {}  # (project id, task id) -> alert waiting for the board to show the change
        self.seen = {}    # project id -> last time a board page read its cards

    def close_when_idle(self, p, task_id):
        path = reports.close_path(p.board, task_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        with self.lock:
            self.close_failed.discard((p.id, task_id))

    def note_if_closing(self, p, task_id, text, images):
        """A reply while the recap is written would resume a session about to be removed: keep it as a note, which
        _close hands to the new session."""
        with self.lock:
            if (p.id, task_id) not in self.closing:
                return False
            reports.append(p.board, task_id, "note", text, "you", images=list(images))
            return True

    def _close(self, p, task_id):
        key, notes = (p.id, task_id), []
        try:
            with CLOSE_SLOTS:
                archive(p, task_id)
            try:
                os.remove(reports.close_path(p.board, task_id))
            except FileNotFoundError:
                pass
            with self.lock:
                notes, since = reports.take_notes(p.board, task_id)
                self.closing.discard(key)
        except Exception as e:  # noqa: BLE001 — sessions stay; moving the card to done again retries
            print("close sessions:", e)
            self.close_failed.add(key)
            reports.append(p.board, task_id, "progress", f"Could not write the recap, so the sessions were kept: {e}",
                           "claude")
        finally:
            self.closing.discard(key)
        if notes:
            try:
                reopen(p, task_id, reports.notes_message(p.board, notes), handed=len(notes))
            except Exception as e:  # noqa: BLE001 — put the notes back; a card reply reopens it
                reports.untake_notes(p.board, task_id, notes, since)
                print("reopen after close:", e)

    def _deliver(self, p, task_id, info, waiting=False):
        try:
            deliver_notes(p, task_id, info, waiting)
        finally:
            self.delivering.discard((p.id, task_id))

    def _type(self, p, task_id, aid):
        try:
            if not type_notes(p, task_id, aid):
                self.typed_fail[(p.id, task_id)] = time.time()
        finally:
            self.delivering.discard((p.id, task_id))

    def _unstall(self, p, task_id, info, stalled):
        try:
            unstall(p, task_id, info)
        except Exception as e:  # noqa: BLE001 — the card asks the user to reply instead
            self.unstall_fail[(p.id, task_id)] = stalled
            print("auto mode retry:", e)
        finally:
            self.delivering.discard((p.id, task_id))

    def expect(self, p, task_id, agent_id):
        with self.lock:
            self.launched[agent_id] = time.time()
            self.cache[(p.id, task_id)] = {"id": agent_id, "sessionId": None, "state": "starting", "phase": "working",
                                           "reason": "", "log": reports.read(p.board, task_id)}

    def poll(self):
        work, done_cols, unread = [], set(), set()
        for p in projects():
            try:
                state = load_tasks(p)
            except (OSError, ValueError):
                unread.add(p.id)  # keep its cards as they were rather than blank them for a pass
                continue
            work += [(p, t) for t in state["tasks"] if t.get("agent")]
            done_cols |= {c["id"] for c in state.get("columns", []) if c.get("done")}
        sessions = {s["id"]: s for s in list_sessions(HERE)} if work else {}
        for p in {p.id: p for p, _ in work}.values():
            try:
                sessions.update((s["id"], s) for s in codex.sessions(p.board))
            except OSError as e:
                print("codex sessions:", e)
        fresh = {}
        with self.lock:
            old = dict(self.cache)
        # Open cards first: each card is published as soon as it is read, so after a restart the board fills in
        # card by card instead of all at once when the pass ends.
        work.sort(key=lambda pt: pt[1].get("column") in done_cols)
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
                    text = (codex.last_answer(s["log"], said[-1]["message"]) if is_codex(s) else
                            last_answer(s.get("sessionId"), said[-1]["message"]))
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
            # While the recap is written, notes wait for the new session (_close): a resume would be removed.
            pending = (s and reports.pending_notes(log, delivered) and (p.id, t["id"]) not in self.delivering
                       and (p.id, t["id"]) not in self.closing)
            # A Codex session has no attach: its notes wait until the process exits, then deliver_notes resumes it.
            if (pending and busy and phase == "working" and attach.available() and not is_codex(s) and s.get("state") != "blocked"
                    and time.time() - self.typed_fail.get((p.id, t["id"]), 0) > TYPE_RETRY):
                # Type it into the live session like a message at its terminal: read at its next step, or at once
                # if it sits idle waiting on a background shell. The inbox hook stays as the backup.
                self.delivering.add((p.id, t["id"]))
                threading.Thread(target=self._type, args=(p, t["id"], aid), daemon=True).start()
                pending = False
            # Busy but its turn is over: it waits on a background shell (maybe hung), so only a resume reaches it.
            waiting = (pending and busy and phase == "working" and not attach.available() and not is_codex(s) and time.time() - (
                turn_ended_at(s.get("sessionId")) or time.time()) > IDLE_GRACE)
            if pending and (phase in ("finished", "needs_you") and not busy or waiting):
                self.delivering.add((p.id, t["id"]))
                threading.Thread(target=self._deliver, args=(p, t["id"], {"id": aid, "sessionId": s.get("sessionId")},
                                                             waiting), daemon=True).start()
            key, closed = (p.id, t["id"]), t.get("column") in done_cols
            # The CLI ends the turn when auto mode's safety check keeps failing, then waits for a message: resume it.
            try:
                stalled = (phase == "needs_you" and not busy and not closed and not reason.startswith("Claude asks permission")
                           and not (said and said[-1].get("status") == "question") and not is_codex(s)
                           and auto_mode_stall(s.get("sessionId")))
            except OSError:
                stalled = None
            unstalling = bool(stalled) and stall_retries(said) < STALL_TRIES and self.unstall_fail.get(key) != stalled
            if stalled:
                reason = "Claude Code's safety check kept failing, so Claude stopped. " + (
                    "The board tries again in a minute." if unstalling else "Reply to try again.")
            if (unstalling and not pending and key not in self.delivering and key not in self.closing
                    and time.time() - stalled > STALL_RETRY):
                self.delivering.add(key)
                threading.Thread(target=self._unstall, args=(p, t["id"], {"id": aid, "sessionId": s.get("sessionId")},
                                                             stalled), daemon=True).start()
            # Prompt cache, subagents and skills are read from Claude transcripts: none for a Codex session.
            try:
                cache = None if is_codex(s) else cache_info(s and s.get("sessionId"))
            except OSError:
                cache = None
            try:
                subs = [] if is_codex(s) else subagents(s and s.get("sessionId"))
            except OSError:
                subs = []
            ids = dict.fromkeys([t["agent"]["id"]] + [e["session"] for e in log if e.get("session")])
            try:
                used = skills(sessions[i].get("sessionId") for i in ids if i in sessions and not is_codex(sessions[i]))
            except OSError:
                used = []
            try:
                spent = tokens(task_transcripts(p, t["id"], [sessions[i].get("sessionId") for i in ids if i in sessions]))
            except OSError:
                spent = None
            # A sha lands on the card only after its commit, so the answer can change only when the log grows.
            done = self.committed.get((p.id, t["id"]))
            if phase != "working" and (not done or done[0] != len(log)):
                found = own_commits(p, log, launched_at(log, t), commit_ref(p, t))
                done = (len(log), bool(found)) if found is not None else None
                if done:
                    self.committed[(p.id, t["id"])] = done
            recapped = any(e.get("status") == "recap" for e in log)
            marker = reports.close_path(p.board, t["id"])
            if not closed:
                try:
                    os.remove(marker)
                except FileNotFoundError:
                    pass
            with self.lock:
                # Closed again after a reopen: that new session goes too once it stops, unless it waits on a question.
                close = closed and s and not busy and phase != "working" and key not in self.closing and (
                    os.path.exists(marker) and key not in self.close_failed
                    or recapped and key not in self.close_failed and phase != "needs_you"
                    and said[-1].get("status") != "recap")
                if close:
                    self.closing.add(key)
                # Moved to done, recap not posted yet: the board holds replies until it is (they would only queue).
                recapping = closed and key not in self.close_failed and (key in self.closing or os.path.exists(marker))
            if close:
                threading.Thread(target=self._close, args=(p, t["id"]), daemon=True).start()
            fresh[(p.id, t["id"])] = {"id": aid, "sessionId": s and s.get("sessionId"), "state": s and s.get("state"),
                                      "archived": not s and bool(said) and said[-1].get("status") == "recap",
                                      "commits": bool(done and done[1]), "phase": phase, "reason": reason,
                                      "permission": phase == "needs_you" and reason.startswith("Claude asks permission") and reason or "", "log": log, "cache": cache, "subagents": subs, "skills": used, "tokens": spent,
                                      "closed": t.get("column") in done_cols, "recapping": recapping, "delivered": delivered,
                                      "worktree": bool(live_worktree(t)), "unstalling": unstalling,
                                      "cli": "codex" if is_codex(s) or not s and t["agent"].get("cli") == "codex"
                                      else "claude"}
            with self.lock:
                self.cache[key] = fresh[key]
        with self.lock:
            fresh.update({k: v for k, v in self.cache.items() if k[0] in unread})
            self.cache = fresh
            primed, self.primed = self.primed, True
        if not primed:
            return
        for p, t in work:
            key = (p.id, t["id"])
            was, now_ = (old.get(key) or {}).get("phase"), fresh[key]["phase"]
            with self.lock:
                if key in self.alerts and self.alerts[key]["phase"] != now_:
                    del self.alerts[key]  # it went back to work before the card showed the change
            # A permission prompt can come while the card already reads "needs you" (an older question).
            asks = fresh[key]["permission"] and fresh[key]["permission"] != (old.get(key) or {}).get("permission")
            # A stall the board resumes itself needs no alert; one it gave up on does, even with no phase change.
            gave_up = (old.get(key) or {}).get("unstalling") and not fresh[key]["unstalling"] and now_ == "needs_you"
            if ((now_ == was and not asks and not gave_up) or (was is None and fresh[key]["id"] not in self.launched)
                    or fresh[key]["unstalling"] and not asks):
                continue
            if now_ in ("needs_you", "finished"):
                with self.lock:
                    self.alerts[key] = {"p": p, "id": t["id"], "phase": now_, "at": time.time(),
                                        "column": t.get("column"), "updated": t.get("updated"),
                                        "title": "Claude asks permission" if asks else "Claude needs you" if now_ == "needs_you" else "Claude finished",
                                        "sub": f"{p.name}: {t['title']}", "reason": fresh[key]["reason"]}
        self.flush_alerts()

    def flush_alerts(self):
        """Alert only once the board shows the change: the page moves a finished card to Review and saves it, and
        shows "needs you" after its next /api/agents read. With no page open nothing will change, so alert at once."""
        with self.lock:
            pending = list(self.alerts.items())
        now, boards, fire = time.time(), {}, []
        for key, a in pending:
            p = a["p"]
            page = self.seen.get(p.id, 0)
            ready = now - page > PAGE_GONE or now - a["at"] > ALERT_WAIT
            if not ready and a["phase"] == "needs_you":
                ready = page > a["at"]
            elif not ready:
                if p.id not in boards:
                    try:
                        boards[p.id] = load_tasks(p)
                    except (OSError, ValueError):
                        boards[p.id] = None
                state = boards[p.id]
                t = state and next((x for x in state["tasks"] if x.get("id") == a["id"]), None)
                cols = {c["id"]: c for c in (state or {}).get("columns", [])}
                review = next((c["id"] for c in cols.values() if not c.get("done") and re.search("review", c.get("name", ""), re.I)), None)
                col = t and cols.get(t.get("column"))
                # The page puts finished work on top of Review even when it was there, so a move shows as a new
                # "updated"; a card in a done column or a board with no Review column has nothing to wait for.
                ready = not t or not review or bool(col and col.get("done")) or (
                    t.get("column") == review and (a["column"] != review or t.get("updated") != a["updated"]))
            if ready:
                fire.append((key, a))
        for key, a in fire:
            with self.lock:
                if self.alerts.get(key) is not a:
                    continue
                del self.alerts[key]
            notify(a["title"], a["sub"], a["reason"])

    def run(self):
        last = 0
        while True:
            try:
                if time.time() - last >= 4:
                    last = time.time()
                    self.poll()
                elif self.alerts:
                    self.flush_alerts()
            except Exception as e:  # noqa: BLE001 — keep watching through a bad poll
                print("watcher:", e)
            finally:
                self.loaded = last > 0  # a first pass that failed still ends the page's fast polling
            time.sleep(0.5)

    def snapshot(self, pid):
        with self.lock:
            self.seen[pid] = time.time()
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


def reply(p, task_id, text, images=(), extra=""):
    """Send a card message. While Claude works it is logged as a note for the session's inbox hook; else it
    resumes the session. Returns True when it was queued as a note. `extra` goes to the session after `text`
    but not onto the card's log (the note and reopen paths keep it with the text)."""
    info = WATCH.snapshot(p.id).get(task_id)
    if not info:
        raise LookupError("no Claude session for this task")
    if info["phase"] == "working":
        reports.append(p.board, task_id, "note", text + extra, "you", images=list(images))
        return True
    if WATCH.note_if_closing(p, task_id, text + extra, images):
        return True
    if not info.get("archived"):
        # The snapshot can predate a close that just finished: resuming its removed session would fail.
        said = [e for e in reports.read(p.board, task_id) if e.get("status") != "note"]
        if said and said[-1].get("status") == "recap" and not any(
                s["id"] == info["id"] for s in all_sessions(p)):
            info = dict(info, archived=True)
    if info.get("archived"):
        reopen(p, task_id, text + extra, images)
        return False
    if not info.get("sessionId"):
        raise LookupError("no Claude session for this task")
    message = text + extra + board_tail(p, task_id)
    if images:
        message += "\n\nImages attached to this reply (open each with the Read tool):\n" + "\n".join(
            image_lines(images, reports.log_images(p.board)))
    aid = resume(p, info, message)
    reports.append(p.board, task_id, "reply", text, "you", session=aid, images=list(images))
    WATCH.expect(p, task_id, aid)
    return False


def board_tail(p, task_id):
    """What follows a card message that resumes a session: a renewed worktree, the delegate brief, the card duty."""
    t = next((x for x in load_tasks(p)["tasks"] if x["id"] == task_id), None)
    extra = ""
    wt = t and renew_worktree(p, t)
    if wt:
        extra += "\n\n" + "\n".join(renewed_lines(wt, p.path))
    extra += delegate_reminder(t)
    # The user reads only the card, so the answer has to go through report.py, not the agent's chat.
    extra += f"\n\n(Reply from the task board. Post your answer on the card: `{report_cmd(p, task_id)} done|progress|question \"...\"`. "
    return extra + reports.LANGUAGE_RULE + ")"


STALL_NOTE = "Claude Code's safety check failed for a moment and stopped Claude, so the board told it to carry on"
STALL_MESSAGE = ("Your last turn was ended by Claude Code, not by you: auto mode's safety check returned no verdict "
                 "several times in a row. That was a passing server problem, not a refusal. Carry on where you left "
                 "off, starting with the step that was cut off.")


def stall_retries(said):
    """The board's auto-mode resumes at the end of the card log, with no report from Claude after them."""
    n = 0
    for e in reversed(said):
        if e.get("status") != "resume" or e.get("message") != STALL_NOTE:
            break
        n += 1
    return n


def unstall(p, task_id, info):
    """Resume a session whose turn the CLI ended because auto mode's safety check kept failing."""
    aid = resume(p, info, STALL_MESSAGE + board_tail(p, task_id))
    reports.append(p.board, task_id, "resume", STALL_NOTE, "you", session=aid)
    WATCH.expect(p, task_id, aid)


def resume(p, info, message):
    """Wake an idle session with `message` as its next turn; returns the session's short id."""
    if codex.is_session(p.board, info["id"]):
        return codex.resume(p.board, info["id"], message)
    # A live idle session must be stopped first, and fully (its pid gone): --resume on a
    # running one starts a copy instead of waking it.
    run_claude(["stop", info["id"]], cwd=p.path, timeout=30)
    for _ in range(40):
        s = next((x for x in all_sessions(p) if x["id"] == info["id"]), None)
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


WORKTREE_ACTIONS = {"run": "Run app from worktree", "merge": "Merge worktree to main"}  # the page matches these labels
OPEN_URL_CMD = {"darwin": "open", "win32": "start \"\""}.get(sys.platform, "xdg-open")


def worktree_steps(p, task_id, wt, action):
    """What the session is told to do for a worktree button; the card shows only the button's label."""
    rep, main, path, branch, base = report_cmd(p, task_id), shell_path(p.path), shell_path(wt["path"]), wt["branch"], wt["base"]
    if action == "run":
        return [f"Run the app from the worktree {shell_path(wt['dir'])}, not from the main checkout {main}, so the "
                "user can try this task's changes.",
                "- Work out how from the project: its run skill if it has one, the README, package scripts, a Makefile.",
                "- A web app: start (or restart) its dev server from the worktree. If the main copy already holds the "
                "usual port, use a spare one. Wait until the URL answers, then open it in a new tab of the user's "
                f"browser with `{OPEN_URL_CMD} <url>`, so they land on this task's copy.",
                "- A game or desktop app: start it from the worktree.",
                "- A command-line tool or a library: run its most useful demo from the worktree and show what it prints.",
                "- Files git does not track (.env, local config, installed dependencies) may be missing in the "
                "worktree: copy or install what it needs to run.",
                "- Leave it running for the user: start it in the background so your turn can end.",
                f'- Then post on the card with `{rep} done "..."`: how to reach it (URL, window, command) and how to '
                "stop it.",
                "- If it cannot be run, say exactly why on the card (the error, what is missing)."]
    if git_try(p.path, "rev-parse", "--verify", "--quiet", f"refs/heads/{base}") is None:
        # Sent from a detached HEAD (base is a sha), or the base branch is gone: there is no branch to merge into.
        return [f"The user wants this task's branch `{branch}` (worktree {path}) merged, but it was made from "
                f"`{base}`, which is not a branch of this repo.",
                "- Merge nothing yet. In the worktree, commit anything still uncommitted.",
                f'- Then ask with `{rep} question "..."` which branch to merge `{branch}` into: list the local '
                f"branches and say which one the main checkout {main} is on now.",
                "- After the answer: merge there. If anything goes wrong, abort the merge so that checkout is exactly "
                "as it was and ask again. Never push. On success remove the worktree (`git worktree remove`, no "
                f"--force) and the branch (`git branch -d {branch}`, never -D), then report the merge commit sha "
                f'with `{rep} done "..."`.']
    return [f"Merge this task's branch `{branch}` into `{base}`:",
            f"1. In the worktree {path}, commit anything still uncommitted.",
            f"2. In the main checkout {main}: it must be on `{base}`. If it is on another branch, stop and ask with "
            f'`{rep} question "..."`.',
            f"3. There, merge `{branch}` into `{base}`.",
            "4. If ANYTHING goes wrong (conflicts, local changes in the main checkout in the way, a failing hook, a "
            f"`{base}` that moved in a way you are unsure about), do not force it. Abort the merge so the main checkout "
            f'is exactly as it was before, then tell the user with `{rep} question "..."`: what went wrong, which '
            "files, and the options.",
            "5. Never push.",
            f"6. On success: stop anything still running from the worktree, then `git worktree remove {path}` and "
            f"`git branch -d {branch}` (never -D, and never --force without asking). Report the merge commit sha "
            f'with `{rep} done "..."`.']


def worktree_action(p, task_id, action):
    """The Review card's worktree buttons: a canned reply to the task's session to run the app from its worktree
    ("run") or merge its branch and remove the worktree ("merge")."""
    if action not in WORKTREE_ACTIONS:
        raise ValueError(f"bad worktree action: {action!r}")
    t = next((x for x in load_tasks(p)["tasks"] if x["id"] == task_id), None)
    if not t or not (t.get("agent") or {}).get("worktree"):
        raise LookupError("this task was not sent to Claude with a worktree")
    wt = live_worktree(t)
    if not wt:
        raise PermissionError("The worktree is gone — it was merged or removed")
    info = WATCH.snapshot(p.id).get(task_id)
    if info and info["phase"] == "working":
        raise PermissionError("Claude is still working — wait until it stops")
    if action == "merge" and board_runs_from(wt["path"]):
        raise PermissionError(STILL_RUNNING_HERE)
    reply(p, task_id, WORKTREE_ACTIONS[action], extra="\n\n" + "\n".join(worktree_steps(p, task_id, wt, action)))


STILL_RUNNING_HERE = "The board itself runs from this worktree — switch it back to main (the box at the top) first"


class UnsavedWorkError(Exception):
    """Deleting the worktree would lose work: args[0] = {"changes": [status lines], "commits": n}."""


def delete_worktree(p, task_id, force=False):
    """The card's "Delete worktree" button: remove the task's worktree folder and its branch, without Claude.
    Refuses (UnsavedWorkError) when that would lose uncommitted changes or commits found on no other branch,
    unless `force` (the user saw the list and confirmed)."""
    t = next((x for x in load_tasks(p)["tasks"] if x["id"] == task_id), None)
    if not t or not (t.get("agent") or {}).get("worktree"):
        raise LookupError("this task was not sent to Claude with a worktree")
    wt = live_worktree(t)
    if not wt:
        raise PermissionError("The worktree is gone — it was merged or removed")
    info = WATCH.snapshot(p.id).get(task_id)
    if info and info["phase"] == "working":
        raise PermissionError("Claude is still working — wait until it stops")
    if board_runs_from(wt["path"]):
        raise PermissionError(STILL_RUNNING_HERE)
    branch = wt["branch"]
    ref = f"refs/heads/{branch}"
    has_branch = git_try(p.path, "rev-parse", "--verify", "--quiet", ref) is not None
    # --ignored: `worktree remove` deletes ignored files too (a .env, local data), so they count as work to lose.
    changes = [x for x in git_run(wt["path"], "status", "--porcelain", "--ignored").splitlines() if x.strip()]
    # The worktree's own HEAD too: it may be detached or mid-rebase, holding commits the branch does not have.
    # --exclude takes the name without refs/heads/ when it filters --branches.
    commits = int(git_run(wt["path"], "rev-list", "--count", *([ref] if has_branch else []), "HEAD",
                          "--not", f"--exclude={branch}", "--branches"))
    if (changes or commits) and not force:
        raise UnsavedWorkError({"changes": changes, "commits": commits})
    git_run(p.path, "worktree", "remove", *(["--force"] if changes else []), wt["path"])
    if has_branch:
        git_run(p.path, "branch", "-D", branch)  # its commits are on another branch, or the user confirmed losing them


def answer_permission(p, task_id, choice, shown):
    """Allow / always / deny the session's permission prompt from the card. `shown` is the request the card showed:
    the key is pressed only while the session still asks exactly that."""
    if choice not in ("allow", "always", "deny"):
        raise ValueError("unknown choice")
    info = WATCH.snapshot(p.id).get(task_id)
    session = info and next((s for s in all_sessions(p) if s["id"] == info["id"]), None)
    if is_codex(session):
        raise LookupError("Codex sessions have no permission prompts")
    ask = permission_ask(session)
    if not ask or ask != shown:
        raise LookupError("the session is no longer asking that; the card will update")
    # "Bash: <command>" -> "<command>": the prompt shows the command under a "Bash command" title.
    need = re.sub(r"^\w+:\s*", "", ask.removeprefix("Claude asks permission: "))
    answered, why = attach.answer_prompt(info["id"], choice, need, CLAUDE_CMD)
    if not answered:
        raise LookupError(why)


def type_notes(p, task_id, aid):
    """Type the notes not handed over yet into the live session (`claude attach`). False if it could not, e.g. a
    permission prompt was on screen; the notes then stay for the inbox hook or a later try."""
    notes, since = reports.take_notes(p.board, task_id)
    if not notes:
        return True
    message = reports.notes_message(p.board, notes)
    message += f"\n\n(Post your answer on the card: `{report_cmd(p, task_id)} progress|done|question \"...\"`.)"
    try:
        typed, why = attach.type_into(aid, message, CLAUDE_CMD)
    except Exception as e:  # noqa: BLE001
        typed, why = False, str(e)
    if not typed:
        reports.untake_notes(p.board, task_id, notes, since)
        print("type notes:", why)
    return typed


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
    agent = "Codex" if codex.is_session(p.board, aid) else "Claude"
    reports.append(p.board, task_id, "resume", f"Handed {agent} {n} message{'s' if n > 1 else ''} it had not read yet",
                   "you", session=aid)
    WATCH.expect(p, task_id, aid)


# ---------------------------------------------------------------- done: recap, then close the sessions
RECAP_MODEL = "sonnet"
CLOSE_SLOTS = threading.Semaphore(3)  # recaps written at once: many cards moved to done together queue up
TRANSCRIPTS = "## Transcripts"  # the recap's last section, added by the board, not the model
DIGEST_HEAD, DIGEST_TAIL, DIGEST_BLOCK = 30_000, 150_000, 4_000  # characters


def session_digest(session_id):
    """The conversation of a session without tool output: the prompt and user messages, Claude's text, and one
    line per tool call. Long runs keep their start and end."""
    path = transcript_path(session_id)
    if not path:
        return ""
    out = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict) or e.get("isSidechain") or e.get("isMeta"):
                continue
            content = (e.get("message") or {}).get("content")
            blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content or []
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and b.get("text", "").strip():
                    text = b["text"].strip()
                    if len(text) > DIGEST_BLOCK:
                        text = text[:DIGEST_BLOCK] + " […]"
                    who = "USER" if e.get("type") == "user" else "CLAUDE"
                    out.append(f"{who}: {text}")
                elif b.get("type") == "tool_use" and e.get("type") == "assistant":
                    out.append("  · " + _action(b))
    text = "\n\n".join(out)
    if len(text) > DIGEST_HEAD + DIGEST_TAIL:
        text = text[:DIGEST_HEAD] + "\n\n[… middle of the session left out …]\n\n" + text[-DIGEST_TAIL:]
    return text


RECAP_ASK = """Above is the record of a task done by Claude Code sessions: the task, earlier recap (if any), the \
messages posted on its task-board card, and the sessions' conversations (tool output left out). The sessions are \
being closed. Write the recap a NEW session will get if the task is reopened, so it can carry on without them.

Markdown, at most about 700 words, these sections:
## Goal — what was asked, in a sentence or two.
## Decisions — each decision made and why (options turned down too, and the user's own choices).
## How — what was changed and where: files, functions, commit shas, how it was tested.
## Results — what works and how that was checked, what was not verified, what is left open or was deferred.

Use only what the record shows; say "not recorded" rather than guess. Write it in the language of the task notes. \
Output only the recap."""


def write_recap(p, t, log, prior, sessions):
    """Ask Claude for the recap of the task's work; returns the markdown."""
    card = "\n\n".join(f"[{e.get('at', '')[:16]}] {'USER' if e.get('from') == 'you' else 'CLAUDE'} "
                       f"({e.get('status')}): {e.get('message', '')}" for e in log)
    parts = [f"# Task: {t['title']}", t.get("notes") or "(no notes)"]
    if prior:
        parts += ["# Earlier recap (the work before these sessions)", prior]
    parts += ["# Card messages", card]
    for i, s in enumerate(sessions, 1):
        digest = codex.digest(s["log"]) if is_codex(s) else session_digest(s.get("sessionId"))
        parts += [f"# Session {i} ({s.get('name') or s['id']})", digest or "(no transcript)"]
    r = run_claude(["-p", "--model", RECAP_MODEL, "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                    "--disable-slash-commands", "--tools", "", "--output-format", "json", "--max-turns", "1",
                    RECAP_ASK], input="\n\n".join(parts), cwd=tempfile.gettempdir(), timeout=600)
    try:
        out = json.loads(r.stdout)
    except ValueError:
        out = {}
    text = (out.get("result") or "").strip()
    if r.returncode != 0 or out.get("is_error") or not text:
        raise RuntimeError((text or r.stderr or r.stdout).strip()[:300] or f"claude exited {r.returncode}")
    return text


def archive(p, task_id):
    """A card moved to done: save a recap of its sessions' work, then remove those sessions (`claude rm` ends the
    process and deletes the job folder with its scratch files; the transcript stays). A Codex session is digested
    from its event log and removed with codex.remove (the log stays); the recap itself is still written by
    `claude -p`. A reply to the card starts a new session from the recap (reopen)."""
    t = next((x for x in load_tasks(p)["tasks"] if x["id"] == task_id), None)
    if not t or not t.get("agent"):
        return
    log = reports.read(p.board, task_id)
    since = max((i + 1 for i, e in enumerate(log) if e.get("status") == "recap"), default=0)
    ids = ([t["agent"]["id"]] if since == 0 else []) + [e["session"] for e in log[since:] if e.get("session")]
    listed = {s["id"]: s for s in all_sessions(p)}
    sessions = [listed[i] for i in dict.fromkeys(ids) if i in listed]
    if not sessions:
        return
    prior, _, listing = reports.read_recap(p.board, task_id).partition(TRANSCRIPTS)
    recap = write_recap(p, t, [e for e in log if e.get("status") != "recap"], prior.strip(), sessions)
    paths = [x for x in (s["log"] if is_codex(s) else transcript_path(s.get("sessionId")) for s in sessions) if x]
    old = re.findall(r"^- (.+\.jsonl)$", listing, re.M)
    recap += "\n\n" + TRANSCRIPTS + "\n\nFull conversations, to search for a detail the recap lacks:\n\n" + "\n".join(
        f"- {shell_path(x)}" for x in dict.fromkeys(old + paths))
    path = reports.recap_path(p.board, task_id)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(recap + "\n")
    replace_file(tmp, path)
    kept = []
    for s in sessions:
        if is_codex(s):
            try:
                codex.remove(p.board, s["id"])
            except (OSError, RuntimeError) as e:
                print("codex remove:", e)
                kept.append(s["id"])
            continue
        r = run_claude(["rm", s["id"]], cwd=p.path, timeout=60)
        if r.returncode != 0:
            kept.append(s["id"])
    note = f"\n\n(Could not remove session{'s' if len(kept) > 1 else ''} {', '.join(kept)}.)" if kept else ""
    reports.append(p.board, task_id, "recap", recap + note, "claude")


def reopen(p, task_id, text, images=(), handed=0):
    """Reply to a card whose sessions were closed: a new session gets the task, the recap and the reply."""
    state = load_tasks(p)
    t = next((x for x in state["tasks"] if x["id"] == task_id), None)
    recap = reports.read_recap(p.board, task_id)
    if not t or not recap:
        raise LookupError("no Claude session for this task")
    fresh = renew_worktree(p, t)
    prompt = "\n".join([build_prompt(p, state, t, fresh or live_worktree(t)), "",
                        *((renewed_lines(fresh, p.path)[0], "") if fresh else ()),
                        "This task was worked on before. Its sessions were closed when the card moved to done, and "
                        "this recap was written from them:", "", recap, "",
                        "The user reopened the card with this message. It is what to do now; where it differs from the "
                        "notes above, it wins:", "", text])
    if images:
        prompt += "\n\nImages attached to this message (open each with the Read tool):\n" + "\n".join(
            image_lines(images, reports.log_images(p.board)))
    model = t["agent"].get("model") or ""
    aid = start_session(p, task_id, "task: " + t["title"][:60], prompt, model)
    if handed:  # notes sent while the recap was written: already on the card
        reports.append(p.board, task_id, "resume", f"Handed {who(model)} {handed} message{'s' if handed > 1 else ''} it "
                       "had not read yet", "you", session=aid)
    else:
        reports.append(p.board, task_id, "reply", text, "you", session=aid, images=list(images))
    WATCH.expect(p, task_id, aid)
    return aid


# ---------------------------------------------------------------- self-update
APP = os.path.dirname(os.path.realpath(__file__))
SERVER_ARGS = sys.argv[1:]  # main() sets it again before parsing; the restart reuses them
UPDATE_TTL = 600


def app_git(*args, timeout=10):
    # No prompts: a fetch that wants a password or passphrase fails instead of hanging the check.
    return subprocess.run(["git", "-C", APP, *args], capture_output=True, timeout=timeout, stdin=subprocess.DEVNULL,
                          env={**os.environ, "GIT_TERMINAL_PROMPT": "0"}, **TEXT)


def _git_out(*args):
    r = app_git(*args)
    return r.stdout.strip() if r.returncode == 0 else None


def _git_err(r):
    lines = [x.strip() for x in (r.stderr or r.stdout or "").splitlines() if x.strip()]
    line = next((x for x in lines if x.startswith(("fatal:", "error:"))), lines[0] if lines else "")
    return line.split(":", 1)[1].strip()[:300] if line.startswith(("fatal:", "error:")) else (
        line[:300] or f"git exited with {r.returncode}")


def update_status():
    st = {"installed": None, "latest": None, "behind": 0, "ahead": 0, "dirty": False, "canUpdate": False,
          "reason": None, "error": None, "checked": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    try:
        top = _git_out("rev-parse", "--show-toplevel")
        # A copy sitting inside some other repo must not pull that repo.
        if not top or not os.path.samefile(top, APP):
            st["reason"] = "Not installed from git — reinstall to get updates."
            return st
        st["installed"] = _git_out("rev-parse", "--short", "HEAD")
        up = _git_out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if not up:
            # No upstream: only main (or a detached HEAD) may follow origin/main; a local branch must not be moved.
            head = _git_out("rev-parse", "--abbrev-ref", "HEAD")
            if head not in ("main", "HEAD"):
                st["reason"] = f"Branch {head} has no upstream — switch to main to get updates."
                return st
            up = "origin/main"
        remote, _, branch = up.partition("/")
        st["upstream"] = up
        try:
            r = app_git("fetch", "-q", remote, branch, timeout=20)
            if r.returncode:
                st["error"] = _git_err(r)
        except subprocess.TimeoutExpired:
            st["error"] = "Could not reach GitHub (timed out)."
        st["latest"] = _git_out("rev-parse", "--short", up)
        if st["latest"]:
            counts = (_git_out("rev-list", "--left-right", "--count", f"HEAD...{up}") or "0 0").split()
            st["ahead"], st["behind"] = int(counts[0]), int(counts[1])
        elif not st["error"]:
            st["error"] = f"No {up} branch to compare with."
        st["dirty"] = bool(_git_out("status", "--porcelain", "--untracked-files=no"))
    except OSError:
        st["reason"] = "Git is not installed — install git to get updates."
        return st
    except subprocess.SubprocessError as e:
        st["error"] = str(e)[:300]
    st["canUpdate"] = st["behind"] > 0 and st["ahead"] == 0 and not st["dirty"] and not st["error"]
    if st["behind"] > 0 and not st["canUpdate"]:
        st["reason"] = ("Local changes in the app folder — update by hand with git pull." if st["dirty"] else
                        "This copy has commits that are not on GitHub." if st["ahead"] else
                        "Could not check GitHub — try again later.")
    return st


WHATS_NEW = "whats-new.md"


def _whats_new(text):
    """[{date, text}] from whats-new.md: `## YYYY-MM-DD` headings over `- ` lines, newest first."""
    entries, day = [], ""
    for line in (text or "").splitlines():
        if line.startswith("## "):
            day = line[3:].strip()
        elif line.startswith("- ") and line[2:].strip():
            entries.append({"date": day, "text": line[2:].strip()})
    return entries


def app_changelog():
    """The app's whats-new.md as installed, plus `pending`: entries in the fetched upstream copy not installed yet."""
    try:
        with open(os.path.join(APP, WHATS_NEW), encoding="utf-8") as f:
            entries = _whats_new(f.read())
    except FileNotFoundError:
        entries = []
    up = _git_out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}") or "origin/main"
    have = {(e["date"], e["text"]) for e in entries}
    pending = [e for e in _whats_new(_git_out("show", f"{up}:{WHATS_NEW}")) if (e["date"], e["text"]) not in have]
    return {"installed": _git_out("rev-parse", "--short", "HEAD"), "pending": pending, "entries": entries}


class Updater:
    """Caches the update check; one lock so parallel GETs don't run parallel fetches (or a pull)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.last, self.at = None, 0

    def status(self, force=False):
        with self.lock:
            if force or not self.last or time.time() - self.at > UPDATE_TTL:
                self.last, self.at = update_status(), time.time()
            return {k: v for k, v in self.last.items() if k != "upstream"}

    def pull(self):
        """git pull --ff-only; returns (from, to) short shas. PermissionError = not updatable (409)."""
        with self.lock:
            st = self.last = update_status()
            self.at = time.time()
            if st["installed"] and st["behind"] == 0 and not st["error"] and not st["reason"]:
                return st["installed"], st["installed"]
            if not st["canUpdate"]:
                raise PermissionError(st["reason"] or st["error"] or "Nothing to update.")
            remote, _, branch = st["upstream"].partition("/")
            r = app_git("pull", "--ff-only", "-q", remote, branch, timeout=120)
            self.last = None
            if r.returncode:
                raise RuntimeError((r.stderr or r.stdout).strip()[:1000] or f"git pull exited with {r.returncode}")
            return st["installed"], _git_out("rev-parse", "--short", "HEAD")


UPDATER = Updater()


def app_copies():
    """The checkouts of the app's repo (the main one first, then its worktrees) and which one this server runs
    from. Empty when the app is not a git checkout."""
    if _git_out("rev-parse", "--show-toplevel") is None:
        return {"current": APP, "copies": []}
    copies = []
    blocks = (_git_out("worktree", "list", "--porcelain") or "").split("\n\n")
    for i, block in enumerate(blocks):
        f = dict((line + " ").split(" ", 1) for line in block.splitlines() if line)
        path = f.get("worktree", "").strip()
        if not path or "bare" in f or not os.path.isfile(os.path.join(path, "server.py")):
            continue
        branch = f.get("branch", "").strip().removeprefix("refs/heads/")
        try:
            with open(os.path.join(path, "server.py"), encoding="utf-8") as fh:
                # A worktree made before this switch existed would leave the board with no way back (main is home).
                can_switch = i == 0 or "def app_copies(" in fh.read()
        except OSError:
            can_switch = False
        copies.append({"path": path, "branch": branch or "(no branch) " + f.get("HEAD", "")[:7].strip(),
                       "main": i == 0, "canSwitch": can_switch})  # git lists the main checkout first
    # A task's worktree branch is "<slug>-<task id>": the name drops the id only when it is a task on the app's board.
    home = next((x for x in projects() if copies and copies[0]["main"] and x.path == os.path.realpath(copies[0]["path"])),
                None)
    try:
        ids = {t["id"] for t in load_tasks(home)["tasks"]} if home else set()
    except (OSError, ValueError, KeyError):
        ids = set()
    for c in copies:
        slug, _, tail = c["branch"].rpartition("-")
        c["name"] = "main" if c["main"] else slug if slug and tail in ids else c["branch"]
    current = next((c["path"] for c in copies if os.path.samefile(c["path"], APP)), APP)
    return {"current": current, "copies": copies}


def board_runs_from(path):
    """True when this server runs from `path` (a worktree it must not remove under itself)."""
    try:
        return os.path.samefile(path, APP)
    except OSError:
        return False


RESTART = threading.Event()
RESTART_FROM = [APP]  # the app folder whose server.py the restart runs


def switch_copy(path):
    """Run the board from another checkout of the app (main or a worktree): checked against app_copies()."""
    info = app_copies()
    target = next((c for c in info["copies"] if c["path"] == path), None)
    if not target:
        raise LookupError("not a copy of the app: " + str(path))
    if target["path"] == info["current"]:
        raise PermissionError("The board already runs from that copy")
    if not target["canSwitch"]:
        raise PermissionError("That copy is older than this switch: the board could not switch back from it")
    target = target["path"]
    RESTART_FROM[0] = target


def restart(server):
    """Stop serving; main() then re-runs server.py. Closing the socket from this thread instead would make
    serve_forever raise in the main thread (select on a closed socket), and on Windows that can end the
    process before the new one is spawned."""
    time.sleep(0.5)  # let the response reach the page first
    RESTART.set()
    server.shutdown()


def reexec():
    """Re-run server.py with the same arguments so pulled code takes effect (or another copy's, after a switch)."""
    cmd = [sys.executable, os.path.join(RESTART_FROM[0], "server.py"), *SERVER_ARGS]
    for stream in (sys.stdout, sys.stderr):
        stream.flush()
    if WINDOWS:  # execv there starts a new process and returns the console to the shell
        subprocess.Popen(cmd)
        os._exit(0)
    os.execv(sys.executable, cmd)


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

    def _send(self, code, body=b"", ctype="application/json", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code, obj, headers=None):
        self._send(code, json.dumps(obj).encode(), headers=headers)

    def _host_ok(self):
        # DNS rebinding: a page on evil.example rebound to 127.0.0.1 sends Host: evil.example, with a matching Origin.
        port = self.server.server_address[1]
        if self.headers.get("Host", "").lower() in (f"127.0.0.1:{port}", f"localhost:{port}"):
            return True
        self._json(403, {"error": "unknown Host refused"})
        return False

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
        except NoRepoError as e:
            self._json(409, {"error": str(e), "norepo": True})
        except UnsavedWorkError as e:
            self._json(409, {"error": "the worktree holds work that would be lost", "unsaved": e.args[0]})
        except Exception as e:  # noqa: BLE001
            self._json(500, {"error": str(e)})

    def do_HEAD(self):
        self._send(404)

    def do_GET(self):
        if not self._host_ok():
            return
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
                try:
                    with open(p.data, "rb") as f:
                        raw = f.read()
                except FileNotFoundError:
                    self._send(200, b"null", headers={"X-Board-Rev": "0"})
                else:
                    self._send(200, raw, headers={"X-Board-Rev": board_rev(p, raw)})
            self._errors(tasks)
        elif path == "/api/default-model":
            self._errors(lambda: self._json(200, default_model(project(pid))))
        elif path == "/api/codex-models":
            self._errors(lambda: self._json(200, codex_models()))
        elif path == "/api/agents":
            def agents():
                p = project(pid)
                # The page polls faster while the first pass after a start still fills in cards.
                headers = {"X-Board-Rev": board_rev(p)}
                if not WATCH.loaded:
                    headers["X-Board-Loading"] = "1"
                self._json(200, WATCH.snapshot(p.id), headers=headers)
            self._errors(agents)
        elif path == "/api/git":
            self._errors(lambda: self._json(200, git_info(project(pid))))
        elif path == "/api/observations":
            self._errors(lambda: self._json(200, observations.list_observations(project(pid).path)))
        elif path == "/api/changelog":
            self._errors(lambda: self._json(200, app_changelog()))
        elif path == "/api/prompt":
            tid = parse_qs(urlsplit(self.path).query).get("t", [""])[0]
            self._errors(lambda: self._json(200, task_prompt(project(pid), tid)))
        elif path in ("/api/task-git", "/api/task-diff"):
            q = parse_qs(urlsplit(self.path).query)
            tid = q.get("t", [""])[0]
            if path == "/api/task-git":
                self._errors(lambda: self._json(200, task_git(project(pid), tid)))
            else:
                self._errors(lambda: self._json(200, task_diff(project(pid), tid, q.get("path", [None])[0],
                                                               q.get("sha", [None])[0])))
        elif path == "/api/app-copy":
            self._errors(lambda: self._json(200, app_copies()))
        elif path == "/api/update":
            force = parse_qs(urlsplit(self.path).query).get("force", [""])[0] not in ("", "0")
            self._errors(lambda: self._json(200, UPDATER.status(force)))
        elif path in ("/", "/index.html"):
            # Only the page itself: the app folder also holds .git/ and the board data.
            with open(os.path.join(HERE, "index.html"), "rb") as f:
                self._send(200, f.read(), TYPES[".html"])
        else:
            self._send(404)

    def do_POST(self):
        path, _ = self._route()
        routes = ("/api/agent", "/api/agent/reply", "/api/agent/review", "/api/agent/permission", "/api/agent/worktree",
                  "/api/agent/worktree/delete", "/api/git/init",
                  "/api/image", "/api/projects", "/api/projects/pick", "/api/projects/forget", "/api/projects/trust",
                  "/api/update", "/api/app-copy")
        if not self._host_ok():
            return
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
                self._json(200, launch(project(body["p"]), body["taskId"], body.get("model") or ""))
            elif path == "/api/agent/permission":
                answer_permission(project(body["p"]), body["taskId"], body.get("choice"), body.get("ask") or "")
                self._json(200, {"ok": True})
            elif path == "/api/agent/worktree":
                worktree_action(project(body["p"]), body["taskId"], body.get("action"))
                self._json(200, {"ok": True})
            elif path == "/api/agent/worktree/delete":
                delete_worktree(project(body["p"]), body["taskId"], bool(body.get("force")))
                self._json(200, {"ok": True})
            elif path == "/api/git/init":
                self._json(200, init_repo(project(body["p"])))
            elif path == "/api/agent/review":
                self._json(200, {"id": review(project(body["p"]), body["taskId"], body.get("level") or "medium",
                                              body.get("model") or "")})
            elif path == "/api/update":
                old, new = UPDATER.pull()
                self._json(200, {"ok": True, "from": old, "to": new, "restarting": old != new})
                if old != new:
                    self.wfile.flush()
                    threading.Thread(target=restart, args=(self.server,), daemon=True).start()
            elif path == "/api/app-copy":
                switch_copy(body.get("path"))
                self._json(200, {"ok": True, "restarting": True})
                self.wfile.flush()
                threading.Thread(target=restart, args=(self.server,), daemon=True).start()
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
        if not self._host_ok():
            return
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
            match = self.headers.get("If-Match")
            with board_lock(p):
                try:
                    with open(p.data, "rb") as f:
                        cur = f.read()
                except FileNotFoundError:
                    cur = None
                rev = board_rev(p, cur) if cur is not None else "0"
                if match is not None and match.strip('"') != rev:
                    self._json(409, {"error": "The board changed in another window", "changed": True, "rev": rev})
                    return
                # Only the board page that sent a card sets its "agent", and nothing clears it. A tab opened before
                # the send (or a save racing the send) would drop it, and the card would vanish from the watcher.
                try:
                    before = json.loads(cur.decode("utf-8"))
                    sent = {t["id"]: t["agent"] for t in before["tasks"] if t.get("agent")}
                except (AttributeError, ValueError, KeyError, TypeError):
                    before, sent = None, {}
                for t in data["tasks"]:
                    if isinstance(t, dict) and not t.get("agent") and t.get("id") in sent:
                        t["agent"] = sent[t["id"]]
                out = (json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
                # Write-then-rename so a crash mid-write never leaves a truncated tasks.json.
                fd, tmp = tempfile.mkstemp(dir=p.board, suffix=".tmp")
                with os.fdopen(fd, "wb") as f:
                    f.write(out)
                replace_file(tmp, p.data)
            if isinstance(before, dict):
                # A card with a session that lands in a done column gets a recap, then its sessions are removed.
                done = {c.get("id") for c in data["columns"] if isinstance(c, dict) and c.get("done")}
                was = {t.get("id"): t.get("column") for t in before.get("tasks", []) if isinstance(t, dict)}
                for t in data["tasks"]:
                    if isinstance(t, dict) and t.get("agent") and t.get("column") in done and was.get(t.get("id")) not in done:
                        WATCH.close_when_idle(p, t["id"])
            self._send(204, headers={"X-Board-Rev": board_rev(p, out)})
        self._errors(write)

    def log_message(self, fmt, *args):
        if "/api/" not in self.path and "/files/" not in self.path:
            super().log_message(fmt, *args)


def main():
    global PERMISSION_MODE, SERVER_ARGS
    SERVER_ARGS = sys.argv[1:]
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
    if RESTART.is_set():
        server.server_close()
        reexec()


if __name__ == "__main__":
    main()
