"""Read the task-observer skill's observation logs (read-only) for the board.

Two sources: <project>/skill-observations/ and <claude config dir>/skill-observations/. Each can hold the
legacy single file log.md (`### Observation <N>: <title>` entries with bold-label fields) and/or the new
observation-log/ folder (one NNNN-slug.md per observation with a small frontmatter block). Only open
entries are listed; a missing status counts as open.
"""
import datetime
import os
import re

HEADER = re.compile(r"^###\s+Observation\s+#?(\d+)\s*[:.\-–—]?\s*(.*)$")
FIELD = re.compile(r"^\s*[-*]?\s*\*\*([^*:]+):?\*\*:?\s*(.*)$")
LEGACY_FIELDS = {"date": "date", "skill": "skill", "type": "type", "phase/area": "area", "area": "area"}


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")


def installed():
    """True when the task-observer skill is installed for the user."""
    return os.path.isfile(os.path.join(config_dir(), "skills", "task-observer", "SKILL.md"))


def read_text(path):
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def parse_legacy(path):
    """Entries of a log.md, as dicts with the observation fields plus a lowercase status ('' when missing)."""
    out, cur = [], None
    for line in read_text(path).splitlines():
        m = HEADER.match(line)
        if m:
            cur = {"id": int(m.group(1)), "title": m.group(2).strip(), "lines": []}
            out.append(cur)
        elif cur is not None:
            if line.startswith("## "):  # a top-level section after the entries ends the last one
                cur = None
            else:
                cur["lines"].append(line)
    entries = []
    for e in out:
        status, body, fields = "", [], {}
        for line in e["lines"]:
            m = FIELD.match(line)
            label = m.group(1).strip().lower() if m else ""
            if label == "status" and not status:
                status = (m.group(2).split() or [""])[0].strip(".:,;").lower() or "open"
                continue
            if label in LEGACY_FIELDS and LEGACY_FIELDS[label] not in fields:
                fields[LEGACY_FIELDS[label]] = m.group(2).strip()
            body.append(line)
        skill = fields.get("skill", "")
        entries.append({"id": e["id"], "title": e["title"], "status": status, "skill": [skill] if skill else [],
                        "date": fields.get("date", ""), "type": fields.get("type", ""),
                        "area": fields.get("area", ""), "file": path, "body": "\n".join(body).strip()})
    return entries


def unquote(s):
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'":
        return s[1:-1]
    return s


def flow_list(s):
    items, cur, quote = [], "", None
    for ch in s.strip()[1:-1]:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch == ",":
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    return [unquote(x) for x in items if x.strip()]


def frontmatter(text):
    """(fields, body) of a `---` delimited frontmatter block; ({}, text) when there is none."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
    if end is None:
        return {}, text
    fields, key = {}, None
    for line in lines[1:end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t" or line.startswith("- "):
            if key is None:
                continue
            item = line.strip()
            if item.startswith("- "):
                if not isinstance(fields[key], list):
                    fields[key] = []
                fields[key].append(unquote(item[2:]))
            elif isinstance(fields[key], str):  # folded / continued scalar
                fields[key] = (fields[key] + " " + item).strip()
            continue
        k, sep, v = line.partition(":")
        if not sep:
            continue
        key, v = k.strip().lower(), v.strip()
        if v.startswith("[") and v.endswith("]"):
            fields[key] = flow_list(v)
        elif v in ("|", ">", "|-", ">-", ""):
            fields[key] = ""
        else:
            fields[key] = unquote(v)
    return fields, "\n".join(lines[end + 1:]).strip()


def parse_file(path):
    """One new-format observation file, or None when it has no frontmatter or no usable id."""
    fields, body = frontmatter(read_text(path))
    if not fields:
        return None
    name = os.path.splitext(os.path.basename(path))[0]
    m = re.match(r"(\d+)", name)
    try:
        oid = int(str(fields.get("id", "")).strip().lstrip("#"))
    except ValueError:
        oid = int(m.group(1)) if m else None
    if oid is None:
        return None
    title = fields.get("title") if isinstance(fields.get("title"), str) else ""
    if not title:
        h = next((ln[2:].strip() for ln in body.splitlines() if ln.startswith("# ")), "")
        title = h or re.sub(r"^\d+[-_]?", "", name).replace("-", " ").strip() or name
    skill = fields.get("skill", [])
    skill = [s for s in skill if s] if isinstance(skill, list) else ([skill] if skill else [])
    status = fields.get("status", "")
    status = status.strip().lower() if isinstance(status, str) else ""

    def text(k):
        v = fields.get(k, "")
        return ", ".join(v) if isinstance(v, list) else v

    return {"id": oid, "title": title, "status": status, "skill": skill, "date": text("date"),
            "type": text("type"), "area": text("area"), "file": path, "body": body}


def read_source(folder):
    """All entries in one skill-observations folder, new format winning over log.md on the same id."""
    by_id = {}
    log = os.path.join(folder, "log.md")
    if os.path.isfile(log):
        try:
            for e in parse_legacy(log):
                by_id.setdefault(e["id"], e)
        except (OSError, ValueError):
            pass
    new = os.path.join(folder, "observation-log")
    if os.path.isdir(new):
        try:
            names = sorted(os.listdir(new))
        except OSError:
            names = []
        for n in names:
            f = os.path.join(new, n)
            if not n.lower().endswith(".md") or not os.path.isfile(f):
                continue
            try:
                e = parse_file(f)
            except (OSError, ValueError):
                continue
            if e:
                by_id[e["id"]] = e
    return list(by_id.values())


def same_dir(a, b):
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


REVIEW_EVERY_DAYS = 7


def review_state(folder, today=None):
    """Last full-review date in folder/last-review-date.txt; due when missing, `never`, unreadable or 7+ days old."""
    today = today or datetime.date.today()
    try:
        last = read_text(os.path.join(folder, "last-review-date.txt")).strip()
    except OSError:
        last = ""
    try:
        days = (today - datetime.date.fromisoformat(last)).days
    except ValueError:
        days = None
    return {"lastReview": last or None, "reviewDays": days,
            "reviewDue": days is None or days >= REVIEW_EVERY_DAYS}


def list_observations(project_path):
    """Open observations of the project and the user, for GET /api/observations."""
    proj = os.path.abspath(os.path.join(project_path, "skill-observations"))
    user = os.path.abspath(os.path.join(config_dir(), "skill-observations"))
    sources = [("project", proj)]
    if not (os.path.isdir(proj) and os.path.isdir(user) and same_dir(proj, user)):
        sources.append(("user", user))
    sources = [(s, d) for s, d in sources if os.path.isdir(d)]
    obs = []
    for scope, folder in sources:
        entries = [e for e in read_source(folder) if e["status"] in ("", "open")]
        for e in sorted(entries, key=lambda x: -x["id"]):
            obs.append({"key": f"{scope}:{e['id']}", **e, "status": "open", "scope": scope})
    return {"installed": installed(), "sources": [{"scope": s, "dir": d, **review_state(d)} for s, d in sources],
            "observations": obs, "open": len(obs)}
