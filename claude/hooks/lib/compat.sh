#!/bin/bash
# compat.sh — shared helpers so the hooks run the same on macOS (bash 3.2), Linux and Windows Git Bash.
# Source it, don't execute it:  . "${BASH_SOURCE[0]%/*}/lib/compat.sh"   (or .../lib/compat.sh from lib/)
#
# Provides:
#   OMT_TMP          scratch dir for sentinels and caches ($TMPDIR, else /tmp), no trailing slash
#   omt_py "$@"      run Python: python3, else python, else "py -3" (Windows launcher). Only an interpreter that
#                    really runs counts (the Microsoft Store "python3" stub and the macOS Xcode stub do not).
#                    Returns 127 when there is none — callers must then fail OPEN (exit 0), never block.
#   omt_has_py       0 when omt_py works
#   omt_md5 STRING   hex md5 of STRING (md5 on macOS, md5sum elsewhere; cksum as a last resort — shorter, still unique enough for a sentinel name)
#   omt_json_get KEY-PATH   read a dotted key from the hook JSON on stdin held in $OMT_INPUT; prints "" when
#                    missing or when there is no Python
[ -n "$_OMT_COMPAT_LOADED" ] && return 0
_OMT_COMPAT_LOADED=1

OMT_TMP=${TMPDIR:-/tmp}
OMT_TMP=${OMT_TMP%/}
[ -d "$OMT_TMP" ] && [ -w "$OMT_TMP" ] || OMT_TMP=/tmp

_omt_py_probe() {
  # "$1 ..." must print ok; stubs exit non-zero or print nothing.
  [ "$("$@" -c 'print("ok")' 2>/dev/null </dev/null)" = ok ]
}
_omt_py_resolve() {
  _OMT_PY_CMD=
  _f="$OMT_TMP/omt-python-cmd-$(id -u 2>/dev/null || echo 0)"
  if [ -f "$_f" ]; then
    _c=$(cat "$_f" 2>/dev/null)
    # Trust the cache only while that interpreter still answers (a removed Python must not stick).
    case $_c in
      "") ;;
      "py -3") _omt_py_probe py -3 && _OMT_PY_CMD=$_c ;;
      *) _omt_py_probe "$_c" && _OMT_PY_CMD=$_c ;;
    esac
    [ -n "$_OMT_PY_CMD" ] && return 0
  fi
  for _c in python3 python; do
    command -v "$_c" >/dev/null 2>&1 && _omt_py_probe "$_c" && { _OMT_PY_CMD=$_c; break; }
  done
  [ -z "$_OMT_PY_CMD" ] && command -v py >/dev/null 2>&1 && _omt_py_probe py -3 && _OMT_PY_CMD="py -3"
  [ -n "$_OMT_PY_CMD" ] && printf '%s\n' "$_OMT_PY_CMD" > "$_f" 2>/dev/null
  [ -n "$_OMT_PY_CMD" ]
}
omt_py() {
  [ -n "$_OMT_PY_CMD" ] || _omt_py_resolve || return 127
  case $_OMT_PY_CMD in
    "py -3") py -3 "$@" ;;
    *) "$_OMT_PY_CMD" "$@" ;;
  esac
}
omt_has_py() { [ -n "$_OMT_PY_CMD" ] || _omt_py_resolve; }

omt_md5() {
  if command -v md5 >/dev/null 2>&1; then printf '%s' "$1" | md5 -q
  elif command -v md5sum >/dev/null 2>&1; then printf '%s' "$1" | md5sum | cut -d' ' -f1
  else printf '%s' "$1" | cksum | cut -d' ' -f1
  fi
}

omt_json_get() {
  [ -n "$OMT_INPUT" ] || return 0
  printf '%s' "$OMT_INPUT" | omt_py -c '
import json, sys
try:
    d = json.load(sys.stdin)
    for k in sys.argv[1].split("."):
        d = d.get(k) if isinstance(d, dict) else None
    print("" if d is None else d if isinstance(d, str) else json.dumps(d))
except Exception:
    pass' "$1" 2>/dev/null
}
