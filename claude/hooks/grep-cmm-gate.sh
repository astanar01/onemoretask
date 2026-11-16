#!/bin/bash
. "${BASH_SOURCE[0]%/*}/lib/compat.sh"
# grep-cmm-gate.sh — PreToolUse:Grep hook (hard-block source-code Grep in indexed CMM repos with machine-readable REPLACE WITH: advisory)
# BLOCKING: exits 2 for code-targeted Grep when CMM is registered AND the project is indexed (sentinel present); otherwise exits 0.
# Fails open without Python (the parse below returns nothing).
#
# Installed by setup/install_hooks.py. Matcher: PreToolUse:Grep. Coexists with cmm-grep-nudge.sh.

# --- Input Parsing ---
INPUT=$(cat 2>/dev/null)
[ -z "$INPUT" ] && exit 0

PARSED=$(echo "$INPUT" | omt_py -c "
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
print(d.get('tool_name',''))
ti = d.get('tool_input',{}) or {}
print(ti.get('pattern','') or '')
print(ti.get('type','') or '')
print(ti.get('glob','') or '')
print(ti.get('path','') or '')
print(d.get('cwd','') or '')
" 2>/dev/null)

# Malformed JSON or parse failure → fail open
[ -z "$PARSED" ] && exit 0

TOOL_NAME=$(echo "$PARSED" | sed -n '1p')
PATTERN_FIELD=$(echo "$PARSED" | sed -n '2p')
TYPE_FIELD=$(echo "$PARSED" | sed -n '3p')
GLOB_FIELD=$(echo "$PARSED" | sed -n '4p')
PATH_FIELD=$(echo "$PARSED" | sed -n '5p')
CWD_FIELD=$(echo "$PARSED" | sed -n '6p')

# Only match Grep — every other tool is a no-op
[ "$TOOL_NAME" != "Grep" ] && exit 0

# --- Exempt fast paths ---
# (The '# cmm-exempt' pattern marker is checked after the block decision: it never covers a source file.)

# Explicit non-code type → allow
case "$TYPE_FIELD" in
  yaml|yml|json|toml|md|markdown|lock|xml|ini|cfg|conf|txt|html|css|env)
    exit 0 ;;
esac

# Explicit non-code glob → allow
case "$GLOB_FIELD" in
  *.yml|*.yaml|*.json|*.md|*.markdown|*.toml|*.lock|*.xml|*.ini|*.cfg|*.conf|*.txt|*.html|*.css|*.env)
    exit 0 ;;
esac

# Planning/config path exemptions (parity with cmm-grep-nudge.sh)
case "$PATH_FIELD" in
  */.planning/*|*/.claude/*|*/node_modules/*|*/.git/*) exit 0 ;;
esac

# --- Determine repo root (walk upward from path or cwd) ---
REPO_ROOT=""
if [ -n "$PATH_FIELD" ] && [ -d "$PATH_FIELD" ]; then
    REPO_ROOT=$(git -C "$PATH_FIELD" rev-parse --show-toplevel 2>/dev/null)
elif [ -n "$PATH_FIELD" ] && [ -f "$PATH_FIELD" ]; then
    REPO_ROOT=$(git -C "$(dirname "$PATH_FIELD")" rev-parse --show-toplevel 2>/dev/null)
fi
if [ -z "$REPO_ROOT" ] && [ -n "$CWD_FIELD" ] && [ -d "$CWD_FIELD" ]; then
    REPO_ROOT=$(git -C "$CWD_FIELD" rev-parse --show-toplevel 2>/dev/null)
    [ -z "$REPO_ROOT" ] && REPO_ROOT="$CWD_FIELD"
fi
[ -z "$REPO_ROOT" ] && exit 0

# --- Small-file exemption: specific file under 50 lines ---
if [ -n "$PATH_FIELD" ] && [ -f "$PATH_FIELD" ]; then
    _LINES=$(wc -l < "$PATH_FIELD" 2>/dev/null || echo 9999)
    if [ "${_LINES:-9999}" -lt 50 ] 2>/dev/null; then
        exit 0
    fi
fi

# --- CMM availability check (lib/compat.sh; parity with cmm-nudge.sh / cmm-grep-nudge.sh) ---
# If CMM not registered anywhere, fail open
omt_cmm_registered "$REPO_ROOT" || exit 0

# --- Indexed-repo sentinel probe (same omt_md5 hash as lib/project-root.sh) ---
_HOOK_DIR=$(cd "${BASH_SOURCE[0]%/*}" && pwd)
PROJECT_HASH=$(cd "$REPO_ROOT" 2>/dev/null && . "$_HOOK_DIR/lib/project-root.sh" && printf '%s' "$PROJECT_HASH")  # a worktree maps to the main checkout
[ -z "$PROJECT_HASH" ] && exit 0
CMM_SENTINEL="$OMT_TMP/cmm-session-ready-${PROJECT_HASH}"
# If project not yet indexed for this session, fail open (same deadlock-prevention posture as ctx-execute-enforcer).
# A `stale` sentinel (reindex-after-commit.sh) does not arm the gate either, as in cmm-grep-nudge.sh.
[ ! -f "$CMM_SENTINEL" ] && exit 0
grep -q '^stale$' "$CMM_SENTINEL" 2>/dev/null && exit 0

# --- Block-trigger decision tree ---
SHOULD_BLOCK=false
MARKER_OK=false

# (a) type is a CMM-indexed language
case "$TYPE_FIELD" in
  go|py|python|js|ts|tsx|jsx|sh|bash|rs|rust|java|rb|ruby|c|cpp|h|hpp|swift|kt|kotlin|php|cs|csharp|scala|lua|zig|ex|exs|elixir|hs|haskell|dart|pl|perl|pm|groovy|erl|erlang|r|clj|clojure|fs|fsharp|jl|julia|el|elisp|cu|cuda|sql|vue|svelte|graphql|proto|protobuf|ml|ocaml|m|mm|objc|nix|elm|lean|f90|f95|f03|f08|fortran|v|sv|verilog|glsl|frag|vert|gd|gdscript|mjs|cjs)
    SHOULD_BLOCK=true ;;
esac

# (b) glob matches a code extension
GLOB_OPEN=false
if [ "$SHOULD_BLOCK" = false ] && [ -n "$GLOB_FIELD" ]; then
    case "$GLOB_FIELD" in
      *.go|*.py|*.js|*.jsx|*.mjs|*.cjs|*.ts|*.tsx|*.sh|*.bash|*.rs|*.java|*.rb|*.c|*.cc|*.cpp|*.cxx|*.h|*.hpp|*.swift|*.kt|*.php|*.cs|*.scala|*.lua|*.zig|*.ex|*.exs|*.hs|*.dart|*.pl|*.pm|*.groovy|*.erl|*.r|*.R|*.clj|*.fs|*.jl|*.el|*.cu|*.sql|*.vue|*.svelte|*.graphql|*.proto|*.ml|*.m|*.mm|*.nix|*.elm|*.lean|*.f90|*.f95|*.f03|*.f08|*.cuh|*.v|*.sv|*.glsl|*.frag|*.vert|*.gd)
        SHOULD_BLOCK=true ;;
      *\{*\}*)
        # Brace expansion like *.{ts,tsx} — inspect each alternative
        _BRACE=$(echo "$GLOB_FIELD" | sed 's/.*{\(.*\)}.*/\1/')
        IFS=',' read -ra _EXTS <<< "$_BRACE"
        for _E in "${_EXTS[@]}"; do
            case ".${_E}" in
              .go|.py|.js|.jsx|.mjs|.cjs|.ts|.tsx|.sh|.bash|.rs|.java|.rb|.c|.cc|.cpp|.cxx|.h|.hpp|.swift|.kt|.php|.cs|.scala|.lua|.zig|.ex|.exs|.hs|.dart|.pl|.pm|.groovy|.erl|.r|.R|.clj|.fs|.jl|.el|.cu|.sql|.vue|.svelte|.graphql|.proto|.ml|.m|.mm|.nix|.elm|.lean|.f90|.f95|.f03|.f08|.cuh|.v|.sv|.glsl|.frag|.vert|.gd)
                SHOULD_BLOCK=true; break ;;
            esac
        done ;;
    esac
    # A glob that names no fixed extension (`*`, `test_*`, `*.*`) can still match code: use the path check (c).
    # One that names a non-code extension (`*.csv`) stays allowed.
    if [ "$SHOULD_BLOCK" = false ]; then
        _GT="${GLOB_FIELD##*/}"
        case "$_GT" in
          *.*) case "${_GT##*.}" in ""|*[\*\?\[]*) GLOB_OPEN=true ;; esac ;;
          *) GLOB_OPEN=true ;;
        esac
    fi
fi

# (c) neither type nor a code glob is set AND path is inside an indexed repo
if [ "$SHOULD_BLOCK" = false ] && [ -z "$TYPE_FIELD" ] && { [ -z "$GLOB_FIELD" ] || [ "$GLOB_OPEN" = true ]; }; then
    # Single-file path with a non-code extension → allow (QA M4). The explicit-glob
    # allowlist above never fires in the path-only branch because GLOB_FIELD is empty,
    # so re-check the file's own extension here before blocking Grep on README.md etc.
    # Case-insensitive match so README.MD, Config.YAML, etc. are also allowed (QA n3).
    if [ -n "$PATH_FIELD" ] && [ -f "$PATH_FIELD" ]; then
        shopt -s nocasematch
        case "$PATH_FIELD" in
          *.yml|*.yaml|*.json|*.md|*.markdown|*.toml|*.lock|*.xml|*.ini|*.cfg|*.conf|*.txt|*.html|*.css|*.env)
            shopt -u nocasematch
            exit 0 ;;
        esac
        shopt -u nocasematch
    fi
    # We already confirmed CMM is registered + sentinel present above
    SHOULD_BLOCK=true
    # '# cmm-exempt' in the pattern may pass a folder search, never a source file (same rule as cmm-grep-nudge.sh).
    MARKER_OK=true
    case "$PATH_FIELD" in
      *.py|*.gd|*.js|*.jsx|*.mjs|*.ts|*.tsx|*.go|*.php|*.cs|*.java|*.kt|*.c|*.cc|*.cpp|*.h|*.hpp|*.cu) MARKER_OK=false ;;
    esac
fi

[ "$SHOULD_BLOCK" = false ] && exit 0
if [ "$MARKER_OK" = true ]; then
    case "$PATTERN_FIELD" in *"# cmm-exempt"*) exit 0 ;; esac
fi

# --- REPLACE WITH classifier (6-line bash regex; per 46-CONTEXT.md) ---
# Identifier = matches ^[A-Za-z_][A-Za-z0-9_]{2,63}$ AND contains NO regex metacharacters
IS_IDENTIFIER=false
if [[ "$PATTERN_FIELD" =~ ^[A-Za-z_][A-Za-z0-9_]{2,63}$ ]]; then
    # Length already bounded 3..64 by regex; also guard no metacharacters (belt-and-suspenders)
    case "$PATTERN_FIELD" in
      *\|*|*.*|*\**|*\+*|*\?*|*\[*|*\(*|*\)*|*\\*|*\^*|*\$*) ;;
      *) IS_IDENTIFIER=true ;;
    esac
fi

if [ "$IS_IDENTIFIER" = true ]; then
    FIRST_LINE="REPLACE WITH: mcp__codebase-memory-mcp__search_graph(name_pattern=\"${PATTERN_FIELD}\")"
    SECOND_LINE="OR:           mcp__codebase-memory-mcp__search_code(query=\"${PATTERN_FIELD}\")"
else
    FIRST_LINE="REPLACE WITH: mcp__codebase-memory-mcp__search_code(query=\"${PATTERN_FIELD}\")"
    SECOND_LINE="OR:           mcp__codebase-memory-mcp__search_graph(name_pattern=\"${PATTERN_FIELD}\")"
fi

# --- Block message (stderr, exit 2) ---
cat >&2 <<EOF
[grep-cmm-gate] BLOCKED -- source-code search in indexed repo.
${FIRST_LINE}
${SECOND_LINE}
Bypass: use an explicit non-code type/glob. ('# cmm-exempt' in the pattern passes only a folder search with no code glob/type.)
EOF

# --- Block counter (best-effort, parity with cmm-grep-nudge.sh) ---
bash "$(dirname "${BASH_SOURCE[0]}")/track-hook-blocks.sh" "grep" 2>/dev/null || true

exit 2
