#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# cwd-guard.sh — PreToolUse:Bash hook (CWD-drift prevention gate)
# BLOCKING: exits 2 when a command would persistently `cd` the shell away from the project root.
#
# Why this exists (monorepo / submodule safety):
#   The Bash tool keeps ONE persistent shell whose cwd survives between calls.
#   A standalone/persistent `cd <subdir>` parks the shell in a subdirectory for
#   every later call. In a monorepo with git submodules that is corrosive: once
#   the shell sits inside a submodule, `git rev-parse --show-toplevel` resolves
#   to the SUBMODULE root, so every CMM/Context-Mode hook computes a DIFFERENT
#   PROJECT_HASH, the `cmm-session-ready-<hash>` and
#   `context-mode-ready-<hash>` sentinels no longer match, and the session
#   gate + enforcer wrongly report "not indexed / not initialized". Hooks cannot
#   read the persistent shell's drifted cwd (the hook payload's cwd is the
#   session root), so drift cannot be DETECTED after the fact — only PREVENTED.
#
#   This hook blocks any command whose top-level (non-subshell) effect is to
#   change the shell's directory to anything other than the project root.
#   Allowed instead:
#     - absolute paths:            cat /abs/path/file
#     - per-repo git:              git -C apps/rest-api status
#     - subshells (cd is local):   ( cd apps/rest-api && make test )
#     - re-anchoring to root:      cd /abs/project/root   |   cd "$CLAUDE_PROJECT_DIR"
#
#   Operator bypass: include the literal `# cwd-exempt` anywhere in the command.
#
# Installed by setup/install_hooks.py. Matcher: PreToolUse:Bash

# --- Project root detection (lib/project-root.sh, cached) ---
# Same root the sentinel hash is computed from.
. "${BASH_SOURCE[0]%/*}/lib/project-root.sh"
[ -z "$PROJECT_ROOT" ] && exit 0   # fail-open: cannot resolve root

INPUT=$(cat)

# Fail open if Python is unavailable — never wedge every Bash call.
omt_has_py || exit 0

DECISION=$(CWD_GUARD_ROOT="$PROJECT_ROOT" omt_py - "$INPUT" <<'PY' 2>/dev/null
import sys, json, os, re, shlex

root = os.environ.get("CWD_GUARD_ROOT", "")
try:
    data = json.loads(sys.argv[1])
except Exception:
    print("ALLOW"); sys.exit(0)

ti = data.get("tool_input", {}) or {}
cmd = ti.get("command", "") or data.get("command", "")
if not cmd:
    print("ALLOW"); sys.exit(0)

# Operator bypass.
if "# cwd-exempt" in cmd:
    print("ALLOW"); sys.exit(0)

# Remove subshell / command-substitution groups: a `cd` inside `( ... )` or
# `$( ... )` runs in a child shell and does NOT persist. Strip innermost
# parens repeatedly until stable so nested groups are handled too.
stripped = cmd
prev = None
while prev != stripped:
    prev = stripped
    stripped = re.sub(r"\([^()]*\)", " ", stripped)

# Split into top-level segments on ; && || | and newlines outside quotes (a commit message saying
# "build && cd docs" changes no directory). A backgrounding `&` stays at the end of its segment.
def split_segments(s):
    segs, cur, q, i = [], "", "", 0
    while i < len(s):
        c = s[i]
        nxt = s[i + 1] if i + 1 < len(s) else ""
        if c == "\\" and q != "'":
            cur += s[i:i + 2]; i += 2; continue
        if q:
            q = "" if c == q else q
        elif c in "'\"":
            q = c
        elif c in ";|\n" or (c == "&" and nxt == "&"):
            segs.append(cur); cur = ""; i += 2 if nxt == c else 1; continue
        elif c == "&" and nxt != ">" and not cur.endswith(">"):
            segs.append(cur + "&"); cur = ""; i += 1; continue
        cur += c; i += 1
    return segs + [cur]

segments = split_segments(stripped)

ALLOWED_TARGET_TOKENS = {
    "$CLAUDE_PROJECT_DIR", '"$CLAUDE_PROJECT_DIR"', "${CLAUDE_PROJECT_DIR}",
    '"${CLAUDE_PROJECT_DIR}"', ".", "./",
}

def norm(p):
    # Windows: Git Bash gives /c/Users/..., git gives C:/Users/...; fold both.
    if os.name == "nt" and re.match(r"^/[A-Za-z](/|$)", p):
        p = p[1] + ":" + (p[2:] or "/")
    return os.path.normcase(os.path.realpath(p))

def target_is_root(tok):
    if tok in ALLOWED_TARGET_TOKENS:
        return True
    t = tok.strip().strip('"').strip("'")
    if not t:
        return False
    if t.startswith("/") or re.match(r"^[A-Za-z]:[\\/]", t):
        try:
            return norm(t) == norm(root)
        except Exception:
            return False
    return False

# Directory-changing builtins whose top-level use persists in the shell.
# `cd`/`pushd` take a target (allowed only when it is the root); `popd` moves to
# an unknown prior stack dir, so any top-level `popd` is treated as drift.
DRIFT_WITH_TARGET = {"cd", "pushd"}
ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
# `if cd x; then cd y; fi`, `{ cd x; }`, `builtin cd x` all change the shell's directory.
LEADING_WORDS = {"if", "then", "else", "elif", "do", "while", "until", "{", "!", "builtin", "command"}

def cd_target(rest):
    # Skip leading option flags (e.g. `cd -P`, `cd -L`, `cd --`) to find the path.
    for r in rest:
        if r.startswith("-"):
            continue
        return r
    return ""

for seg in segments:
    s = seg.strip()
    if not s:
        continue
    # A segment backgrounded with a trailing '&' runs in a child shell; its cwd
    # change does NOT persist in the parent. (Top-level '&&' was already split out
    # above, so a remaining trailing '&' is backgrounding.)
    if s.endswith("&"):
        s = s[:-1].strip()
        if not s:
            continue
        # Re-check: a backgrounded cd does not drift the persistent shell.
        try:
            bg_toks = shlex.split(s, comments=False, posix=True)
        except Exception:
            continue
        idx = 0
        while idx < len(bg_toks) and ENV_ASSIGN.match(bg_toks[idx]):
            idx += 1
        if idx < len(bg_toks) and bg_toks[idx] in ("cd", "pushd", "popd"):
            continue  # backgrounded dir change — non-persistent, allow
        continue
    try:
        toks = shlex.split(s, comments=False, posix=True)
    except Exception:
        # Unparseable: conservative regex fallback (also covers pushd/popd).
        m = re.match(r"^\s*(cd|pushd|popd)(\s+(\S+))?\s*$", s)
        if m:
            verb = m.group(1); tgt = m.group(3) or ""
            if verb == "popd" or not tgt or not target_is_root(tgt):
                print("BLOCK\t" + s); sys.exit(0)
        continue
    if not toks:
        continue
    # Skip leading NAME=VALUE env-assignment prefixes (e.g. `FOO=1 cd sub`).
    idx = 0
    while idx < len(toks) and (ENV_ASSIGN.match(toks[idx]) or toks[idx] in LEADING_WORDS):
        idx += 1
    if idx >= len(toks):
        continue
    verb = toks[idx]
    if verb == "popd":
        print("BLOCK\t" + s); sys.exit(0)
    if verb in DRIFT_WITH_TARGET:
        tgt = cd_target(toks[idx + 1:])
        if not tgt or not target_is_root(tgt):
            print("BLOCK\t" + s); sys.exit(0)

print("ALLOW")
PY
)

case "$DECISION" in
  BLOCK*)
    BAD_SEG="${DECISION#BLOCK	}"
    cat >&2 <<EOF
BLOCKED: persistent 'cd' detected — it would park the shell's directory and drift every later call.

  offending: ${BAD_SEG}

The Bash tool's working directory persists between calls, and a drifted cwd makes
the CMM / Context Mode sentinels stop matching (they are keyed on the project
root), so the session gate wrongly reports "not indexed". Keep commands
cwd-independent instead:
  - absolute paths:        cat ${PROJECT_ROOT}/some/file
  - per-repo git:          git -C apps/rest-api status
  - local cd in subshell:  ( cd apps/rest-api && make test )
  - re-anchor to root:     cd "${PROJECT_ROOT}"

Operator bypass (rare, intentional drift): append '# cwd-exempt' to the command.
EOF
    exit 2
    ;;
esac

exit 0
