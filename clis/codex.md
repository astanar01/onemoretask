You are Codex, not Claude Code. The task board copies the rules below from Claude Code's files, so they were written
for Claude Code. Follow them, with these changes.

**Tools.** Where the rules name a Claude Code tool, use your own:

| Claude Code | Codex |
|---|---|
| Bash | your shell tool (`exec_command` / `local_shell`) |
| Read | the shell (`sed -n '1,200p' <file>`, `cat`) |
| Edit, Write | `apply_patch` |
| Grep | `grep_files`, or `rg` in the shell |
| Glob | `rg --files -g '<pattern>'` in the shell |
| Skill | open the skill's `SKILL.md` and follow it (see Skills) |
| Agent | `spawn_agent` |
| ToolSearch | nothing: your MCP tools are always loaded |

**Does not apply to you** (these are Claude Code's own parts): the hooks and gates the rules name
(`cmm-grep-nudge.sh`, `cmm-index-mode-guard.sh`, `agent-cmm-gate.sh`, `cwd-guard.sh`, the per-prompt Muninn reminder,
the session-start hook), the `# cmm-exempt` marker, ToolSearch and "deferred tools", the Skill tool, and Claude's
auto-memory. No hook will remind you: recall Muninn yourself at the start of a task and store what you learn at the
end.

**MCP tools** keep their server and tool names (`codebase-memory-mcp`: `search_graph`, `get_code_snippet`,
`search_code`, ...; `muninn`: `muninn_recall`, `muninn_remember`, ...). context-mode here is the 1.x npm version: its
1.x tool names apply where the rules name 2.x ones (`ctx_batch_execute` for `ctx_batch`, `ctx_execute_file` for
`ctx_execute` with a file, `ctx_fetch_and_index` for `ctx_index` with a URL).

**Skills.** Your skill list holds the user's skills; to use one by hand, read `~/.claude/skills/<name>/SKILL.md` or
`<project>/.claude/skills/<name>/SKILL.md` and follow it.

**The skill observer** (`task-observer`, then `task-observer-extras`, as the rules say): open its `SKILL.md`; run its
scripts with `bash` (for a new observation: `bash scripts/new-observation.sh <slug> <workspace root>`, from the skill's
folder; it prints the new file's path); write the observation into that file with `apply_patch`; skip the parts that
rely on Claude Code hooks.

**In a project**, also read and follow its `CLAUDE.md` and `CLAUDE.local.md` when they exist, as if they were part of
this file.
