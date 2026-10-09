#!/bin/bash
# ctx-engine-pipe-gate.sh — PreToolUse:Bash hook (blocks engine-output truncation)
# BLOCKING: exits 2 when a Godot engine run's output is piped into a truncation
# filter (grep/head/tail/sed/awk). Bytes discarded before capture never reach
# the context-mode index (ctx-rules anti-pattern), and the kept bytes still
# flood the context window — ctx_batch_execute does both jobs properly.
# Escape hatch: append "# ctx-exempt" to the command (advertised in the error).
#
# Registered in settings.json under PreToolUse, matcher "Bash".

INPUT=$(cat)
CMD=$(echo "$INPUT" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('command',''))" 2>/dev/null || echo "")

[ -z "$CMD" ] && exit 0

# --- Explicit bypass marker ---
case "$CMD" in
  *ctx-exempt*) exit 0 ;;
esac

# --- Engine run piped into a truncation filter ---
# Precision guards: godot must sit in COMMAND position (line start or after a
# ; & | ( separator, allowing a path prefix but no spaces), and the invocation
# must carry --headless or --path (how a scripted Godot run looks). No Godot
# in the command means no match, so the gate never fires in other projects
# — so "echo godot ... | grep" and unrelated pipes never trip.
if echo "$CMD" | grep -qiE '(^|[;&|(])[[:space:]]*[^|;&[:space:]]*godot[^|;&]*--(headless|path)[^|]*\|[[:space:]]*(grep|head|tail|sed|awk)\b'; then
  cat >&2 <<'BLOCKED'
BLOCKED: engine output piped into a truncation filter (grep/head/tail/sed/awk).
Bytes discarded before capture are permanently lost from the context-mode index
(ctx-rules anti-pattern), and the kept bytes still enter your context raw.

Run it through context-mode instead — the FULL output gets indexed and stays
searchable, and only the sections you asked about return:

  mcp__plugin_context-mode_context-mode__ctx_batch_execute(
    commands: [{label: "verify run", command: "<same engine command, UNPIPED>"}],
    queries: ["FAIL", "SCRIPT ERROR", "PASS"])   # note: queries is REQUIRED

Single run? ctx_execute(language: "shell", code: "...", intent: "...") — above
5KB the intent switches the return to search hits instead of raw output.

Truly just eyeballing a short fixed line (e.g. a version string)? Append
"# ctx-exempt" to the command to bypass this gate.
BLOCKED
  exit 2
fi

exit 0
