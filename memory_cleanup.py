#!/usr/bin/env python3
"""Retire Muninn notes that point at files which are really gone, without asking each time.

Run: python3 memory_cleanup.py            one check; acts when a note meets every condition
     python3 memory_cleanup.py --dry-run  prints what it would do and changes nothing

server.py runs this about once a day. A note is marked no longer true only when ALL of these hold:
  - it names at least one file or folder of a known project, the project's history shows that name was deleted (not
    renamed), and no name in the note still exists in the project, a card copy or a local branch;
  - two checks at least a day apart found this;
  - no session used it for 90 days;
  - it has no key-decision tag (those wait for a person, on the board).
Only the name was renamed? The note gets the new name instead. "No longer true" keeps the note in history and the board
can bring it back; nothing here deletes permanently.

Traps measured on Muninn v0.11.0:
  - last_access and access_count do not change on recall, so use is counted from the session transcripts instead.
    Transcripts live 30 days, so the counts are kept in the state file and nothing is removed until 90 days are covered.
  - GET /api/engrams still lists a note after muninn_forget(not_true_since), so re-read a note before acting on it.
  - muninn_restore refuses such a note; muninn_evolve is what brings it back (under a new id).
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

REST = "http://127.0.0.1:8475"
MCP = "http://127.0.0.1:8750/mcp"
CONFIG = os.path.expanduser("~/.config/task_board")
REGISTRY = os.path.join(CONFIG, "projects.json")
STATE = os.path.join(CONFIG, "memory-cleanup.json")
ACTIONS = os.path.join(CONFIG, "memory-cleanup-actions.json")  # the board's clicks: only act() writes this file
TRANSCRIPTS = os.path.expanduser("~/.claude/projects")
DAY = 86400
UNUSED_DAYS = 90
MAX_PER_RUN = 25  # a bug or an odd state may flag many notes at once; the rest wait for the next run
KEY_TAG = "key-decision"
SHOWN_DAYS = 7

EXTENSIONS = ("py js ts tsx jsx mjs sh gd tscn tres glb gltf php html css md json yml yaml toml txt go rs c h cpp "
              "cs java kt rb sql vue blade png svg").split()
NAME = re.compile(r"(?<![\w/.~:@-])(?:res://|~/|/)?[\w@+.-]+(?:/[\w@+.-]+)*/?")
ULID = re.compile(rb"\b01[0-9A-HJKMNP-TV-Z]{24}\b")
# A sentence holding one of these already knows the name is old, so the note may well still be right.
KNOWS_ITS_OLD = re.compile(r"\b(removed?|deleted?|dropped|retired|gone|no longer|replaced|renamed|moved|old|former|"
                           r"legacy|deprecated|was)\b", re.I)


def names_in(text):
    """Things in a note's text that look like a file or folder. Words that only look like one are sorted out later:
    a relative name counts only when a project's history knows it."""
    out = set()
    for m in NAME.finditer(text):
        s = m.group().rstrip(".-")
        if not s or s.startswith("//") or "://" in s[:8] and not s.startswith("res://"):
            continue
        tail = text[m.end():m.end() + 3]
        if tail.startswith("://"):
            continue
        s = s[len("res://"):] if s.startswith("res://") else s
        has_slash = "/" in s.strip("/")
        ext = s.rsplit(".", 1)[-1].lower() if "." in s.rsplit("/", 1)[-1] else ""
        if has_slash or ext in EXTENSIONS:
            if re.search(r"[A-Za-z]{2}", s):
                out.add(s.rstrip("/"))
    return out


def subpaths(path):
    """Every run of whole segments of a/b/c: a, b, c, a/b, b/c, a/b/c. A note may name a path by its tail or a folder."""
    parts = path.split("/")
    return {"/".join(parts[i:j]) for i in range(len(parts)) for j in range(i + 1, len(parts) + 1)}


def git(root, *args):
    r = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True, errors="replace", timeout=600)
    if r.returncode:
        raise RuntimeError(f"git {args[0]} failed in {root}: {r.stderr.strip()[:200]}")
    return r.stdout


class Project:
    """What one git project holds now (checkouts and local branches) and what its history once held."""

    def __init__(self, root):
        self.root = root
        self.name = os.path.basename(root.rstrip("/")).lower()
        self.checkouts = [line[9:] for line in git(root, "worktree", "list", "--porcelain").splitlines()
                          if line.startswith("worktree ")]
        self.here = set()
        for tree in sorted(set(git(root, "for-each-ref", "--format=%(tree)", "refs/heads").split())):
            for path in git(root, "ls-tree", "-r", "--name-only", tree).splitlines():
                self.here |= subpaths(path)
        self.ever = set()
        self.renames = {}  # old path -> new path, the newest rename of it
        for line in git(root, "log", "--all", "-M", "--name-status", "--format=").splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            for path in parts[1:]:
                self.ever |= subpaths(path)
            if parts[0].startswith("R") and len(parts) == 3:
                self.renames.setdefault(parts[1], parts[2])
        if not self.ever:
            raise RuntimeError(f"no history in {root}")

    def exists(self, name):
        name = name.lstrip("/")
        return name in self.here or any(os.path.exists(os.path.join(c, name)) for c in self.checkouts)

    def new_name(self, name):
        """Where the renames lead a file or folder, when they all lead to one place that exists now."""
        ends = set()
        for old, new in self.renames.items():
            if old == name or old.endswith("/" + name):
                ends.add(self._follow(new))
            elif name + "/" in old + "/" and (old.startswith(name + "/") or "/" + name + "/" in old):
                head = old[:old.index(name) + len(name)]
                tail = old[len(head):]
                final = self._follow(new)
                ends.add(final[:-len(tail)] if final.endswith(tail) else None)
        ends.discard(None)
        return ends.pop() if len(ends) == 1 and self.exists(next(iter(ends))) else None

    def _follow(self, path):
        seen = set()
        while path in self.renames and path not in seen:
            seen.add(path)
            path = self.renames[path]
        return path

    def classify(self, name):
        """(here | renamed | gone | unknown, new name or None) for a name relative to this project."""
        name = name.lstrip("/")
        if self.exists(name):
            return "here", None
        if name not in self.ever:
            return "unknown", None
        new = self.new_name(name)
        return ("renamed", new) if new else ("gone", None)


def load_projects(registry=REGISTRY):
    """Every project on the board that can be read now. One that can't (a moved folder, an unplugged disk) is left out:
    its notes then have no project and are left alone."""
    try:
        with open(registry, encoding="utf-8") as f:
            entries = json.load(f).get("projects", [])
    except (OSError, ValueError):
        return []
    out = []
    for e in entries:
        path = e.get("path") if isinstance(e, dict) else None
        if not path or os.sep + ".claude" + os.sep in path or not os.path.isdir(path):
            continue
        try:
            out.append(Project(path))
        except (OSError, RuntimeError, subprocess.SubprocessError) as err:
            print(f"Note: skipped a project ({err}).", file=sys.stderr)
    return out


def project_of(note, projects):
    """The one project a note is about: by its tags, else by its title and opening words. None when unclear."""
    tags = {str(t).lower() for t in note.get("tags") or []}
    by_tag = [p for p in projects if p.name in tags]
    if len(by_tag) == 1:
        return by_tag[0]
    if by_tag:
        return None
    head = f"{note.get('concept', '')} {(note.get('content') or '')[:120]}".lower()
    by_text = [p for p in projects if re.search(rf"(?<![\w-]){re.escape(p.name)}(?![\w-])", head)]
    return by_text[0] if len(by_text) == 1 else None


def resolve(name, note_project, projects):
    """(project, name relative to it) for one name, or (None, name) when it lies outside every project."""
    full = os.path.expanduser(name)
    if os.path.isabs(full):
        for p in projects:
            if full == p.root or full.startswith(p.root.rstrip("/") + "/"):
                return p, full[len(p.root.rstrip("/")) + 1:]
        return None, full
    return note_project, name


def judge(note, projects):
    """What the note's names say. kind: keep | partial | aware | renamed | gone."""
    text = note.get("content") or ""
    project = project_of(note, projects)
    gone, renamed, here = [], {}, 0
    for name in sorted(names_in(text)):
        p, rel = resolve(name, project, projects)
        if p is None:
            here += os.path.isabs(rel) and os.path.exists(rel)  # a live path outside any project still counts
            continue
        if not rel:
            here += 1
            continue
        state, new = p.classify(rel)
        here += state == "here"
        if state == "gone":
            gone.append(name)
        elif state == "renamed":
            renamed[name] = new
    if not gone and not renamed:
        return {"kind": "keep", "names": []}
    names = gone + sorted(renamed)
    for sentence in re.split(r"(?<=[.;!?])\s|\n", text):
        if any(n in sentence for n in names):
            for n in names:  # a file called gone.py says nothing about itself
                sentence = sentence.replace(n, " ")
            if KNOWS_ITS_OLD.search(sentence):
                return {"kind": "aware", "names": names}
    if renamed and not gone:
        if any(new in text for new in renamed.values()):
            return {"kind": "aware", "names": names}
        return {"kind": "renamed", "names": names, "renames": renamed}
    if here or renamed:
        return {"kind": "partial", "names": names}
    return {"kind": "gone", "names": names}


# ------------------------------------------------------------------------------------------ Muninn
def _open(req, tries=5):
    for i in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == tries - 1:
                raise
            time.sleep(1 + i)


def list_notes(rest=REST):
    """Every note. Raises unless the pages add up to the store's own total, so a short read can never look like a store
    that lost notes."""
    notes, offset, total = {}, 0, None
    while True:
        d = _open(f"{rest}/api/engrams?vault=default&limit=200&offset={offset}")
        total = d["total"]
        page = d["engrams"]
        for n in page:
            notes[n["id"]] = n
        offset += len(page)
        if not page or offset >= total:
            break
    if len(notes) != total:
        raise RuntimeError(f"read {len(notes)} notes of {total}")
    return list(notes.values())


def call(tool, args, mcp=MCP):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool, "arguments": args}})
    req = urllib.request.Request(mcp, body.encode(), {"Content-Type": "application/json",
                                                      "Accept": "application/json, text/event-stream"})
    d = _open(req)
    if d.get("error"):
        raise RuntimeError(d["error"].get("message", "tool error"))
    return json.loads(d["result"]["content"][0]["text"])


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_open(note_id, mcp=MCP):
    """The note as it stands now, or None when it was already closed, deleted or superseded."""
    try:
        cur = call("muninn_read", {"id": note_id, "read_only": True}, mcp)
    except RuntimeError:
        return None
    return cur if cur.get("state") == "active" and not cur.get("valid_until") else None


# ------------------------------------------------------------------------------------------ state
def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def scan_use(state, known_ids, transcripts=TRANSCRIPTS, now=None):
    """Record when each note last showed up in a session transcript. Only transcripts changed since the last scan are
    read. The covered window restarts when transcripts may have been cleaned up before they were read."""
    now = now or time.time()
    since = state.get("scanned_to", 0)
    files = [(os.path.getmtime(p), p) for p in glob.glob(os.path.join(transcripts, "**", "*.jsonl"), recursive=True)]
    oldest = min((m for m, _ in files), default=None)
    if not since or (oldest and since < oldest):
        state["covered_from"] = oldest or now
    used = state.setdefault("used", {})
    known = {i.encode() for i in known_ids}
    for mtime, path in files:
        if mtime <= since:
            continue
        try:
            with open(path, "rb") as f:
                while True:
                    chunk = f.read(1 << 24)
                    if not chunk:
                        break
                    chunk += f.readline()
                    for found in set(ULID.findall(chunk)) & known:
                        used[found.decode()] = max(used.get(found.decode(), 0), mtime)
        except OSError:
            pass
    state["scanned_to"] = now


def run(now=None, dry=False, rest=REST, mcp=MCP, registry=REGISTRY, state_path=STATE, actions_path=ACTIONS,
        transcripts=TRANSCRIPTS):
    """One check. Returns the state it saved (or would have saved, on a dry run)."""
    now = now or time.time()
    state = read_json(state_path)
    actions = read_json(actions_path)
    notes = list_notes(rest)
    projects = load_projects(registry)
    if not projects:
        raise RuntimeError("no project could be read")
    scan_use(state, {n["id"] for n in notes}, transcripts, now)
    kept = set(actions.get("kept", {})) | {v.get("new_id") for v in actions.get("restored", {}).values()}
    closed = {e["id"] for e in state.get("removed", [])} | set(actions.get("restored", {}))
    closed |= {e["id"] for e in actions.get("removed", [])}

    flagged, seen, review, waiting, tally, acted = {}, state.get("seen", {}), {}, [], {}, 0
    covered = now - state.get("covered_from", now) >= UNUSED_DAYS * DAY
    for note in notes:
        nid = note["id"]
        if nid in kept or nid in closed:
            continue
        verdict = judge(note, projects)
        kind = verdict["kind"]
        tally[kind] = tally.get(kind, 0) + 1
        if kind not in ("renamed", "gone"):
            continue
        first = seen.get(nid, {}).get("first", now) if seen.get(nid, {}).get("kind") == kind else now
        flagged[nid] = {"first": first, "kind": kind}
        if now - first < DAY:
            continue
        entry = {"id": nid, "concept": note.get("concept") or "", "text": (note.get("content") or "")[:160],
                 "names": verdict["names"], "when": now}
        if kind == "renamed":
            if acted < MAX_PER_RUN and not dry:
                cur = is_open(nid, mcp)
                if cur:
                    text = cur["content"]
                    for old, new in verdict["renames"].items():
                        text = text.replace(old, new)
                    call("muninn_evolve", {"id": nid, "new_content": text, "reason": "a file it names was renamed"}, mcp)
                    state.setdefault("renamed", []).append({**entry, "renames": verdict["renames"]})
                    flagged.pop(nid)
                    acted += 1
            continue
        if KEY_TAG in (note.get("tags") or []):
            if nid in state.get("review", {}) or is_open(nid, mcp):  # the list still shows notes already closed
                review[nid] = {**entry, "when": state.get("review", {}).get(nid, {}).get("when", now)}
            continue
        last_used = max(note.get("created_at", now), state["used"].get(nid, 0))
        if not covered or now - last_used < UNUSED_DAYS * DAY:
            waiting.append(nid)
            continue
        if acted >= MAX_PER_RUN:
            waiting.append(nid)
            continue
        if dry:
            state.setdefault("would_remove", []).append(entry)
            continue
        if is_open(nid, mcp):
            call("muninn_forget", {"id": nid, "not_true_since": iso(now)}, mcp)
            state.setdefault("removed", []).append(entry)
            acted += 1
        flagged.pop(nid)

    state.update(seen=flagged, review=review, waiting=len(waiting), tally=tally, notes=len(notes), last_run=now,
                 projects=sorted(p.name for p in projects), error=None)
    if not dry:
        write_json(state_path, state)
    return state


def note_failure(err, now=None, state_path=STATE):
    state = read_json(state_path)
    state.update(error=str(err)[:300], last_try=now or time.time())
    write_json(state_path, state)


def due(now=None, state_path=STATE):
    """A check runs a full day after the last one that finished or failed, so two checks are always a day apart."""
    state = read_json(state_path)
    return (now or time.time()) - max(state.get("last_run") or 0, state.get("last_try") or 0) >= DAY


# ------------------------------------------------------------------------------------------ board
def view(now=None, state_path=STATE, actions_path=ACTIONS):
    """What the board shows: this week's changes, the notes waiting for a person, and when removal can start."""
    now = now or time.time()
    state, actions = read_json(state_path), read_json(actions_path)
    restored, kept = actions.get("restored", {}), actions.get("kept", {})
    recent = lambda e: now - e.get("when", 0) < SHOWN_DAYS * DAY
    every = [e for e in state.get("removed", []) + actions.get("removed", []) if e["id"] not in restored]
    removed = [e for e in every if recent(e)]
    gone_ids = {e["id"] for e in actions.get("removed", [])}
    review = [e for i, e in state.get("review", {}).items() if i not in kept and i not in gone_ids]
    covered_from = state.get("covered_from")
    return {"last_run": state.get("last_run"), "error": state.get("error"), "notes": state.get("notes", 0),
            "waiting": state.get("waiting", 0), "removed": sorted(removed, key=lambda e: -e["when"]),
            "earlier": sorted((e for e in every if not recent(e)), key=lambda e: -e["when"]),
            "renamed": [e for e in state.get("renamed", []) if recent(e)],
            "review": sorted(review, key=lambda e: e.get("concept", "")),
            "removal_from": covered_from + UNUSED_DAYS * DAY if covered_from else None}


def act(note_id, action, now=None, mcp=MCP, state_path=STATE, actions_path=ACTIONS):
    """A click on the board. keep: leave the note alone for good. remove: mark it no longer true. restore: bring back."""
    now = now or time.time()
    actions = read_json(actions_path)
    state = read_json(state_path)
    if action == "keep":
        actions.setdefault("kept", {})[note_id] = now
    elif action == "remove":
        entry = state.get("review", {}).get(note_id)
        if not entry:
            raise ValueError("that note is not waiting for you")
        call("muninn_forget", {"id": note_id, "not_true_since": iso(now)}, mcp)
        actions.setdefault("removed", []).append({**entry, "when": now})
    elif action == "restore":
        entry = next((e for e in state.get("removed", []) + actions.get("removed", []) if e["id"] == note_id), None)
        if not entry:
            raise ValueError("that note was not removed here")
        cur = call("muninn_read", {"id": note_id, "read_only": True}, mcp)
        new = call("muninn_evolve", {"id": note_id, "new_content": cur["content"].rstrip() + "\n(brought back from the board)",
                                     "reason": "brought back from the board"}, mcp)
        actions.setdefault("restored", {})[note_id] = {"new_id": new["id"], "when": now}
        actions["removed"] = [e for e in actions.get("removed", []) if e["id"] != note_id]
    else:
        raise ValueError("unknown action")
    write_json(actions_path, actions)
    return view(now, state_path, actions_path)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true", help="print what would happen; change nothing")
    args = ap.parse_args(argv)
    try:
        s = run(dry=args.dry_run)
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as e:
        # Muninn may simply not run here: nothing to do, try again next time.
        if not args.dry_run:
            note_failure(e)
        print(f"Note: memory cleanup did not run ({e}).")
        return 0
    print(json.dumps({k: s.get(k) for k in ("notes", "tally", "waiting", "projects")}))
    for e in s.get("would_remove", []):
        print("would remove:", e["concept"], e["names"])
    for e in s.get("review", {}).values():
        print("needs a person:", e["concept"], e["names"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
