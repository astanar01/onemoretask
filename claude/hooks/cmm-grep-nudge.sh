#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# cmm-grep-nudge.sh — PreToolUse:Grep+Bash hook (hard-blocking CMM navigation gate)
# BLOCKING: exits 2 for code-targeted Grep or Bash navigation commands when CMM is
# available, redirecting to graph tools. Honors # cmm-exempt override in Bash commands.
# Fails open (exit 0) without Python.
#
# Installed by setup/install_hooks.py. Matchers: PreToolUse Grep and Bash

# --- Input Parsing ---
INPUT=$(cat)
omt_has_py || exit 0
TOOL_NAME=$(echo "$INPUT" | omt_py -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('tool_name','') or '')
" 2>/dev/null)

# --- Bash navigation block ---
# When invoked for a Bash tool call: check for code-navigation commands against source paths.
# Only enforced when CMM sentinel is present (fail-open when CMM not indexed).
if [ "$TOOL_NAME" = "Bash" ]; then
  BASH_CMD=$(echo "$INPUT" | omt_py -c "
import sys,json
d=json.load(sys.stdin)
print(d.get('tool_input',{}).get('command','') or '')
" 2>/dev/null)

  # Every check ties a path to the nav verb that RECEIVES it, per pipeline segment: `python3 t.py | tail`
  # and `git diff x.py | grep foo` pass. Prints "src <file>" when a nav verb's own arguments name a file
  # CMM parses into functions (.html left out: CMM keeps inline JS as one blob), else "dir" when they name
  # a source dir as a whole path component (`claudeapps/` is not `apps/`). Heredoc bodies and redirect
  # targets are written content, not navigation; a bare `<` input redirect still counts.
  GATE=$(BASH_CMD="$BASH_CMD" omt_py - <<'PY' 2>/dev/null
import os, re, shlex
cmd = os.environ.get("BASH_CMD", "").split("<<")[0]
cmd = re.sub(r"\d*(>&|&>>?|>>?)\|?\s*[^\s|;&()]*", " ", cmd)
ext = re.compile(r"\.(py|gd|js|jsx|mjs|ts|tsx|go|php|cs|java|kt|c|cc|cpp|h|hpp|cu)$")
src_dir = re.compile(r"(^|/)(src|app|apps|lib|pkg|internal|hooks|agents|scripts)/")
nav = {"grep", "egrep", "rg", "cat", "head", "tail", "awk", "less", "nl"}
dir_nav = {"grep", "rg", "find", "cat", "head", "tail", "wc"}
dir_hit = False
for seg in re.split(r"\|\||&&|[|;&\n()]", cmd):
    try:
        w = shlex.split(seg, comments=True)
    except ValueError:
        w = seg.split()
    while w and (re.match(r"^\w+=", w[0]) or w[0] in ("sudo", "command", "env", "xargs")):
        w = w[1:]
    if not w:
        continue
    verb = os.path.basename(w[0])
    if verb in nav or (verb == "sed" and "-n" in w):
        hit = next((t for t in w[1:] if ext.search(t)), "")
        if hit:
            print("src " + os.path.basename(hit))
            break
    if verb in dir_nav and any(src_dir.search(t) for t in w[1:]):
        dir_hit = True
else:
    if dir_hit:
        print("dir")
PY
)
  SRC_READ=
  case $GATE in "src "*) SRC_READ=${GATE#src } ;; esac

  # # cmm-exempt bypasses the block, except on a source read (agents had made it the default for those).
  if [ -z "$SRC_READ" ] && echo "$BASH_CMD" | grep -q '# cmm-exempt'; then
    exit 0
  fi

  if [ -n "$GATE" ]; then

    # CMM availability check for Bash block (fail-open if CMM not configured)
    BASH_CWD=$(echo "$INPUT" | omt_py -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
    # Normalize path to resolve symlinks (macOS /var/folders -> /private/var/folders)
    BASH_REPO_ROOT=$(cd "${BASH_CWD:-.}" 2>/dev/null && pwd -P) || BASH_REPO_ROOT="${BASH_CWD:-.}"
    BASH_CMM_FOUND=false
    if [ -f "$BASH_REPO_ROOT/.mcp.json" ] && grep -q 'codebase-memory-mcp' "$BASH_REPO_ROOT/.mcp.json" 2>/dev/null; then
      BASH_CMM_FOUND=true
    fi
    if [ "$BASH_CMM_FOUND" = false ]; then
      CLAUDE_SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
      if [ -f "$CLAUDE_SETTINGS" ] && grep -q 'codebase-memory-mcp' "$CLAUDE_SETTINGS" 2>/dev/null; then
        BASH_CMM_FOUND=true
      fi
    fi

    # Only block when CMM sentinel is present (index is ready)
    BASH_PROJECT_HASH=$(omt_md5 "$BASH_REPO_ROOT")
    BASH_SENTINEL="$OMT_TMP/cmm-session-ready-${BASH_PROJECT_HASH}"
    if [ "$BASH_CMM_FOUND" = true ] && [ -f "$BASH_SENTINEL" ] && ! grep -q '^stale$' "$BASH_SENTINEL" 2>/dev/null; then
      cat >&2 <<EOF
BLOCKED: Use CMM tools instead of Bash navigation for code search.
  - Symbol search:  mcp__codebase-memory-mcp__search_graph(name_pattern="...")
  - Text search:    mcp__codebase-memory-mcp__search_code(query="...")
  - Fetch source:   mcp__codebase-memory-mcp__get_code_snippet(qualified_name="...")
  - Functions in one file: mcp__codebase-memory-mcp__search_graph(file_pattern="*name.gd")
  - Whole file:     Read, or ctx_execute_file to filter it
CMM indexes .py/.gd/.js/.ts/... functions (GDScript included). # cmm-exempt does not work on these
reads; it is only for non-source files, git state and filtering a program's output (skill \`cmm-rules\`).
EOF
      bash "$(dirname "${BASH_SOURCE[0]}")/track-hook-blocks.sh" "bash-nav" 2>/dev/null || true
      exit 2
    fi
  fi
  exit 0
fi

PARSED=$(echo "$INPUT" | omt_py -c "
import sys,json
d=json.load(sys.stdin)
ti=d.get('tool_input',{})
glob_field=ti.get('glob','') or ''
type_field=ti.get('type','') or ''
path_field=ti.get('path','') or ''
pattern_field=ti.get('pattern','') or ''
print(glob_field)
print(type_field)
print(path_field)
print(pattern_field)
" 2>/dev/null)

GLOB_FIELD=$(echo "$PARSED" | sed -n '1p')
TYPE_FIELD=$(echo "$PARSED" | sed -n '2p')
PATH_FIELD=$(echo "$PARSED" | sed -n '3p')
PATTERN_FIELD=$(echo "$PARSED" | sed -n '4p')

# --- Exception: Planning/Config Paths (check path early) ---
case "$PATH_FIELD" in
  */.planning/*|*/.claude/*|*/node_modules/*|*/.git/*) exit 0 ;;
esac

# --- CMM Availability Check (safety valve — don't block if CMM isn't configured) ---
# Determine repo root from path field or cwd
if [ -n "$PATH_FIELD" ] && [ -d "$PATH_FIELD" ]; then
  REPO_ROOT=$(git -C "$PATH_FIELD" rev-parse --show-toplevel 2>/dev/null)
elif [ -n "$PATH_FIELD" ] && [ -f "$PATH_FIELD" ]; then
  REPO_ROOT=$(git -C "$(dirname "$PATH_FIELD")" rev-parse --show-toplevel 2>/dev/null)
fi
if [ -z "$REPO_ROOT" ]; then
  CWD=$(echo "$INPUT" | omt_py -c "import sys,json; print(json.load(sys.stdin).get('cwd',''))" 2>/dev/null)
  REPO_ROOT="${CWD:-.}"
fi

CMM_FOUND=false
# Check project .mcp.json
if [ -f "$REPO_ROOT/.mcp.json" ] && grep -q 'codebase-memory-mcp' "$REPO_ROOT/.mcp.json" 2>/dev/null; then
  CMM_FOUND=true
fi
# Check global settings.json as fallback
if [ "$CMM_FOUND" = false ]; then
  CLAUDE_SETTINGS="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/settings.json"
  if [ -f "$CLAUDE_SETTINGS" ] && grep -q 'codebase-memory-mcp' "$CLAUDE_SETTINGS" 2>/dev/null; then
    CMM_FOUND=true
  fi
fi
# If CMM not found anywhere, allow Grep (fail open)
[ "$CMM_FOUND" = false ] && exit 0

# --- Extra extensions cache (from .codebase-memory.json) ---
_EXT_CACHE=""
if [ -n "$REPO_ROOT" ]; then
  _EXT_CACHE="$OMT_TMP/cmm-user-ext-$(omt_md5 "$REPO_ROOT")"
  if [ ! -f "$_EXT_CACHE" ]; then
    omt_py -c "
import json, os
for p in ['${REPO_ROOT}/.codebase-memory.json']:
    if os.path.isfile(p):
        try:
            for ext in json.load(open(p)).get('extra_extensions',{}):
                print(ext)
        except: pass
" 2>/dev/null > "$_EXT_CACHE" || rm -f "$_EXT_CACHE"
  fi
fi

# Helper: check if a filename matches code extensions (inline list + extra_extensions)
_is_code_file() {
  local f="$1"
  case "$f" in
    *.py|*.go|*.js|*.jsx|*.ts|*.tsx|*.rs|*.java|*.cpp|*.cc|*.cxx|*.c|*.h|*.hpp|\
    *.cs|*.php|*.lua|*.scala|*.kt|*.rb|*.sh|*.bash|*.zsh|*.zig|*.ex|*.exs|*.hs|\
    *.swift|*.dart|*.pl|*.pm|*.groovy|*.erl|*.r|*.R|*.clj|*.fs|*.jl|*.el|*.cu|\
    *.sql|*.vue|*.svelte|*.graphql|*.proto|*.ml|*.m|*.mm|*.nix|*.elm|*.lean|\
    *.f90|*.f95|*.f03|*.f08|*.cuh|*.v|*.sv|*.glsl|*.frag|*.vert|*.gd)
      return 0 ;;
  esac
  if [ -n "$_EXT_CACHE" ] && [ -s "$_EXT_CACHE" ]; then
    local bn; bn=$(basename "$f")
    while IFS= read -r _ext; do
      case "$bn" in *"$_ext") return 0 ;; esac
    done < "$_EXT_CACHE"
  fi
  return 1
}

SHOULD_BLOCK=false
BLOCK_TARGET=""

# --- Check glob field for code extensions ---
if [ -n "$GLOB_FIELD" ]; then
  _GLOB_MATCHED=false
  # Handle brace-expansion globs like *.{ts,tsx}
  if echo "$GLOB_FIELD" | grep -q '{.*}'; then
    _BRACE_CONTENT=$(echo "$GLOB_FIELD" | sed 's/.*{\(.*\)}.*/\1/')
    IFS=',' read -ra _EXTS <<< "$_BRACE_CONTENT"
    for _EXT in "${_EXTS[@]}"; do
      if _is_code_file "file.$_EXT"; then _GLOB_MATCHED=true; break; fi
    done
  fi
  # Standard glob: extract extension suffix
  if [ "$_GLOB_MATCHED" = false ]; then
    _GLOB_SUFFIX="${GLOB_FIELD##*\*}"
    if [ -n "$_GLOB_SUFFIX" ] && [ "$_GLOB_SUFFIX" != "$GLOB_FIELD" ]; then
      _is_code_file "file${_GLOB_SUFFIX}" && _GLOB_MATCHED=true
    fi
  fi
  if [ "$_GLOB_MATCHED" = true ]; then
    SHOULD_BLOCK=true
    BLOCK_TARGET="glob='$GLOB_FIELD'"
  fi
fi

# --- Check type field for code types ---
if [ "$SHOULD_BLOCK" = false ] && [ -n "$TYPE_FIELD" ]; then
  case "$TYPE_FIELD" in
    py|python|go|js|jsx|ts|tsx|rust|java|cpp|c|cs|csharp|php|lua|scala|kotlin|\
    rb|ruby|sh|bash|zsh|zig|elixir|haskell|swift|dart|perl|groovy|erlang|r|\
    clojure|fsharp|julia|elisp|cuda|sql|vue|svelte|graphql|protobuf|ocaml|\
    objc|nix|elm|lean|fortran|verilog|glsl)
      SHOULD_BLOCK=true
      BLOCK_TARGET="type='$TYPE_FIELD'" ;;
  esac
fi

# --- Check path field for specific code file (only if glob/type not set) ---
if [ "$SHOULD_BLOCK" = false ] && [ -z "$GLOB_FIELD" ] && [ -z "$TYPE_FIELD" ] && [ -n "$PATH_FIELD" ]; then
  if _is_code_file "$PATH_FIELD"; then
    SHOULD_BLOCK=true
    BLOCK_TARGET="path='$(basename "$PATH_FIELD")'"
  fi
fi

# --- Allow if no code target detected ---
[ "$SHOULD_BLOCK" = false ] && exit 0

# --- Block: Redirect to CMM search tools (stderr, exit 2) ---
cat >&2 <<EOF
BLOCKED: Use CMM tools instead of Grep for code search on $BLOCK_TARGET.
  - Symbol search:  mcp__codebase-memory-mcp__search_graph(name_pattern="...")
  - Text search:    mcp__codebase-memory-mcp__search_code(query="...")
  - Trace callers:  mcp__codebase-memory-mcp__trace_path
  Grep is allowed for: non-code files (JSON, YAML, Markdown, config, env)
See skill \`cmm-rules\` for the full protocol.
EOF

# --- Block Counter ---
bash "$(dirname "${BASH_SOURCE[0]}")/track-hook-blocks.sh" "grep" 2>/dev/null || true

exit 2
