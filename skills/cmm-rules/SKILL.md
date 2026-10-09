---
name: cmm-rules
description: "The authoritative `# cmm-exempt` policy for the CMM gate (cmm-grep-nudge.sh) — when the marker is legitimate, where it goes, what to do instead — plus codebase-memory-mcp coverage traps (index_repository fast/moderate mode excluding source dirs, worktree indexes, GDScript class_name gap, inline HTML JS) and size-gating a grep before dumping files. The tool decision table and orient-first pattern are in the always-loaded ~/.claude/rules/cmm-rules.md. Use when the CMM gate blocks a command, when search_code/search_graph returns 0 hits for something you know exists, before claiming a language is unindexed, or before cat-ing a grep match set."
---

# CMM rules — gate policy and coverage traps

The decision table (`get_architecture` → `search_graph` → `get_code_snippet`, `trace_path`,
`query_graph`, `search_code`), the generic behavior notes (v0.8.1: 200-row cap,
multi-word `search_code` regex, `list_projects`, LSP-resolved CALLS, complexity metrics)
and "CMM vs context-mode" live in the always-loaded `~/.claude/rules/cmm-rules.md`. This
skill holds only what that file points here for.

## When the CMM gate fires — `# cmm-exempt` policy (AUTHORITATIVE)

A PreToolUse hook (`cmm-grep-nudge.sh`) blocks Bash commands that pair a navigation verb (`grep`/`rg`/`find`/`cat`/`head`/`tail`/`wc`) with a source-ish path. **This section is the single authoritative `# cmm-exempt` policy.** The hook ignores the marker when a nav verb's own arguments name a file CMM indexes (`.py`, `.gd`, `.js`, `.ts`, `.go`, `.php`, ...) — read those with CMM, `Read`, or `ctx_execute_file`. Other docs (`~/.claude/rules/cmm-rules.md`) defer to this section.

**CMM tools are mandatory — never exempt — for actual code navigation:** searching or reading source to understand code → `search_graph` / `search_code` / `get_code_snippet`. Never use the marker to author files either — `cat >`/heredoc → `Write`/`Edit`.

**`# cmm-exempt` is legitimate in exactly three classes** (append the marker and proceed):

1. **Engine/CLI driver output filtering** — piping a launched binary's output through nav verbs (e.g. `godot --headless … | grep`): the source-ish path is the program being run, not code being searched.
2. **Non-source state files** — checks/diffs/reads of markdown, logs, git state, `.import`/staged-copy artifacts: CMM doesn't index these and no CMM tool does the job. Prefer `Read`/`ctx_execute_file` when they fit; exempt when a shell one-liner is the right tool.
3. **ctx tools unavailable** — the sanctioned `ctx_execute` path is unreachable because no ctx_* tools are registered in the session (ToolSearch finds none).

**Marker placement: the marker must be the LAST thing on the command line.** Everything after `#` is a shell comment, so `cmd1 # cmm-exempt (reason); cmd2; cmd3` silently never runs cmd2/cmd3 — and the output looks complete, so the miss is invisible (this ate a staged-index check in a real session). When chaining commands, put the marker only at the very end of the full chain, or split into separate invocations.

**One command cannot carry two bypass markers.** Each gate matches its own marker as an exact suffix, so `… # ctx-exempt cmm-exempt` satisfies neither. When a command would trip both the engine-pipe (ctx) gate and the CMM gate — e.g. it runs the engine AND greps a `.import` file — split it: the engine run redirects its output to a file under the job's tmp dir (no pipe, so no ctx marker needed), and the grep/read step goes in its own call ending in `# cmm-exempt`.

Outside these three classes, treat a gate firing as a routing signal, not an obstacle to disable:

- Inspecting/parsing a file (incl. binary like GLB/PNG) → `ctx_execute` opening the bytes itself; `Read` for text files.
- **Commit messages** whose prose contains a nav-verb word ("the trail streams behind", "…HEAD"): write the message to a file and `git commit -F <msgfile>` — the reliable fix; the marker is not for this.
- **File mutations (`rm`/`mv`) flagged because a path looks source-ish:** run them via `ctx_execute(language:"shell", …)` — it mutates the real filesystem and isn't gated (class 3 applies if ctx is absent).

(A separate `cwd-guard` hook blocks persistent `cd` — use absolute paths, `git -C <dir>`, or a subshell `( cd … && … )` instead.)

## Coverage traps — an empty result is not proof of absence

- **GDScript IS indexed — use CMM on `.gd` like any other language.** Measured 2026-10-01 on a Godot game: 5093 Function nodes from `.gd` files (one per `func`) with signature, docstring and callers; `search_code` finds `.gd` text. Use `search_graph` (`name_pattern` / `file_pattern="*player.gd"`), `get_code_snippet`, `trace_path`. Two real gaps: (1) a `class_name` gets NO node, so `search_graph name_pattern="CrowFlight"` returns 0 — find the class's file with `search_code "class_name CrowFlight"` mode `files`, then `search_graph file_pattern`; (2) GDScript CALLS edges are heuristic, not LSP-resolved. (An older note said GDScript had ZERO coverage — it came from a class_name lookup and sent every session on that game to grep.)
- **Inline `<script>` JS in `.html` is NOT parsed into functions** (the file is one Module node). Grep/ctx there is the right tool.
- **`index_repository` `mode="fast"` (and `"moderate"`) apply a directory filter — on ANY checkout.** It can drop the dirs that hold most of the code: fast mode excluded `tools`, `scripts`, `docs`, `assets` on a game repo's main checkout as well as on a fresh worktree, and `search_code` then returned 0 matches with no warning for a word a script under `scripts/` used 60+ times. So: (1) read the `excluded` field of every index result; (2) if a source dir is listed, index with `mode="full"` — at the START of the task (see the mid-task caveat below), or for a worktree point navigation at a main-checkout project that was itself indexed full; (3) treat 0 hits on a symbol you know is present as a coverage gap — check `excluded` before concluding anything.
- **In a git worktree, read `excluded.dirs` before trusting an empty search.** On a `.claude/worktrees/` checkout of a game repo, `mode=fast` excluded `tools`, `docs`, `scripts`, `assets` and kept 196 nodes, so `search_code` found no game source — while the result still said `indexed`. Query the MAIN checkout's project (e.g. `Users-you-code-mygame`) for navigation and `Read` the worktree's own files for edits — the main index does not know the worktree's uncommitted changes. Do not start a `mode=full` re-index mid-task: one dropped the CMM server for the rest of the session.
- **Before trusting or writing a "language X has no coverage" claim, probe it:** `query_graph` `MATCH (f:Function) WHERE f.file_path ENDS WITH '.gd' RETURN count(f)`. One empty name lookup proves nothing about a language.

## Widening a grep into file dumps — size-gate the join

Never expand a `grep -rl` match set into `cat`/full-file reads without checking
sizes first: `grep -rl | xargs cat` couples a cheap, precise operation ("which
files") to an unbounded one ("all their bytes"), and the match set's SIZE is
independent of its relevance — one oversized file dominates the whole batch.
Print `wc -c` for the match set, filter by size, or grep for the specific field
with `-A N` context instead of dumping whole files. In repos with a monolithic
scene/config file, assume it is in EVERY match set for any domain noun: one game's
world scene (about 1 MB) matches almost any gameplay term — a two-scene
creature lookup that cat'ed its grep matches returned 9,075 lines (186 indexed
sections) for an answer two targeted greps gave in under 2 KB.
